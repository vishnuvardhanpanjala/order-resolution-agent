"""End-to-end tests with the scripted model, plus fake models that break in specific ways."""
import json

import pytest

from app.llm import LLMUnavailable
from app.pipeline import Env, run_ticket
from app.scripted import ScriptedLLM
from data.seed import load_tickets, seeded_store

TICKETS = {t["ticket_id"]: t for t in load_tickets()}
HERO = [tid for tid, t in TICKETS.items() if "hero" in t["tags"]]


def run(tid, llm=None, **kw):
    return run_ticket(TICKETS[tid], Env(llm=llm or ScriptedLLM(), mode="test", **kw), store=kw.pop("store", None))


def names(tr, status):
    return sorted(a["name"] for a in tr["actions"] if a.get("status") == status)


@pytest.mark.parametrize("tid", HERO)
def test_hero_cases_end_to_end(tid):
    tr, e = run(tid), TICKETS[tid]["expected"]
    assert tr["disposition"] == e["disposition"]
    assert names(tr, "done") == sorted(e["actions"]) and names(tr, "queued") == sorted(e["approval_actions"])
    assert all(m.lower() in tr["reply"].lower() for m in e["must_mention"])
    assert not any(m.lower() in tr["reply"].lower() for m in e["must_not_mention"])


def test_courier_note_injection_is_caught_and_does_nothing():
    tr = run("T047")
    assert tr["security_alerts"] and tr["disposition"] == "AUTO_RESOLVED" and not tr["actions"]
    assert "parcel-help" not in tr["reply"]


def test_hard_gates_hold_with_all_detection_switched_off():
    store_paid = {}
    for tid, t in TICKETS.items():
        if "adversarial" not in t["tags"]:
            continue
        tr = run(tid, detection_on=False)
        for a in tr["actions"]:
            if a.get("status") == "done" and a["name"] in ("issue_refund", "create_replacement", "create_reship"):
                assert a["amount"] <= 80, (tid, a)
                store_paid[tid] = a
        assert "parcel-help" not in tr["reply"] and "91234567" not in tr["reply"]
    assert all(a["name"] != "issue_refund" for a in store_paid.values())  # no attacker got cash out


class Down:
    def complete(self, step, messages, json_mode=False):
        raise LLMUnavailable("503 from provider")


class BadJson(ScriptedLLM):
    def complete(self, step, messages, json_mode=False):
        if step == "classify":
            return "sure! the intent is WISMO", {"model": "bad"}
        return super().complete(step, messages, json_mode)


class InventsMoney(ScriptedLLM):
    def complete(self, step, messages, json_mode=False):
        if step == "draft":
            return "Hi, we will also send you a S$20 voucher!", {"model": "bad"}
        return super().complete(step, messages, json_mode)


def test_provider_down_escalates_as_data_gap_with_a_safe_reply():
    tr = run("T001", llm=Down())
    assert (tr["disposition"], tr["reason_code"]) == ("ESCALATED", "DATA_GAP") and tr["reply_held"]


def test_invalid_json_twice_escalates_as_low_confidence():
    tr = run("T001", llm=BadJson())
    assert (tr["disposition"], tr["reason_code"]) == ("ESCALATED", "LOW_CONFIDENCE")
    assert len([s for s in tr["steps"] if s["step"] == "classify"]) == 2


def test_reply_with_invented_amount_is_held_but_the_correct_action_still_happens():
    tr = run("T013", llm=InventsMoney())
    assert names(tr, "done") == ["issue_refund"]
    assert tr["disposition"] == "ESCALATED" and tr["reply_held"] and "S$20" not in tr["reply"]
    assert len([s for s in tr["steps"] if s["step"] == "draft"]) == 2


def test_same_ticket_twice_on_one_store_refunds_once():
    store = seeded_store()
    env = Env(llm=ScriptedLLM(), mode="test")
    run_ticket(TICKETS["T004"], env, store=store)
    run_ticket(TICKETS["T004"], env, store=store)
    assert len(store.q("SELECT * FROM refunds WHERE order_id='O1004'")) == 1


def test_unverified_sender_gets_no_account_data():
    tr = run("T037")
    assert tr["disposition"] == "CLARIFY" and not tr["tool_calls"] and "SG10370001" not in tr["reply"]


def test_trace_is_json_serialisable():
    json.dumps(run("T015"))
