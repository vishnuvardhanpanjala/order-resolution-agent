"""Tests for the enterprise controls: scoping, allowlists, caps, idempotency, roles, audit, failures."""
import sqlite3

import pytest

from app.actions import ActionDenied, ActionService
from app.mock_systems import MockSystems
from app.tools import DataGap, ScopedTools, ToolNotAllowed
from app.types import Action, Intent
from data.seed import seeded_store


@pytest.fixture
def world():
    s = seeded_store()
    sys = MockSystems(s)
    return s, sys, ActionService(s, sys)


def tools(world, cid="C001", intent=Intent.WISMO, log=None):
    s, sys, svc = world
    return ScopedTools(cid, intent, sys, svc, "T-test", log if log is not None else [])


def refunds_on(s, oid):
    return s.q("SELECT * FROM refunds WHERE order_id=?", oid)


def audit(s):
    return s.q("SELECT action, role, outcome FROM audit_log ORDER BY id")


# ---------- scoping ----------

def test_other_customers_order_reads_as_not_found(world):
    assert tools(world, cid="C048").get_order("O1001") is None
    assert tools(world, cid="C048").get_shipment("O1001") is None
    assert tools(world, cid="C001").get_order("O1001")["customer_id"] == "C001"


def test_action_on_other_customers_order_denied_and_audited(world):
    s, _, svc = world
    with pytest.raises(ActionDenied, match="not_this_customer"):
        svc.execute(actor="agent", role="agent", action=Action(name="create_reship", order_id="O1001", amount=69),
                     customer_id="C048", ticket_id="T", rule_id="x")
    assert audit(s)[-1]["outcome"] == "denied:not_this_customer"


def test_tool_outside_intent_allowlist_denied_and_logged(world):
    log = []
    t = tools(world, cid="C013", intent=Intent.REFUND_STATUS, log=log)
    with pytest.raises(ToolNotAllowed):
        t.act(Action(name="issue_refund", order_id="O1013", line_no=1, amount=59), "x")
    assert log[-1]["allowed"] is False


# ---------- money ----------

def test_agent_refund_within_cap_runs_once_with_idempotency(world):
    s, _, svc = world
    a = Action(name="issue_refund", order_id="O1013", line_no=1, amount=59)
    first = svc.execute(actor="agent", role="agent", action=a, customer_id="C013", ticket_id="T013", rule_id="D2")
    second = svc.execute(actor="agent", role="agent", action=a, customer_id="C013", ticket_id="T013", rule_id="D2")
    assert len(refunds_on(s, "O1013")) == 1 and second["duplicate"] is True
    assert first["destination"] == s.order_view("O1013")["payment_method"]
    assert [r["outcome"] for r in audit(s)] == ["done", "duplicate_ignored"]


def test_agent_refund_above_cap_denied(world):
    _, _, svc = world
    with pytest.raises(ActionDenied, match="above_role_limit"):
        svc.execute(actor="agent", role="agent", action=Action(name="issue_refund", order_id="O1046", line_no=1,
                    amount=149), customer_id="C046", ticket_id="T", rule_id="x")


def test_refund_above_paid_denied_even_for_lead(world):
    _, _, svc = world
    with pytest.raises(ActionDenied, match="above_paid"):
        svc.execute(actor="lee", role="cx_lead", action=Action(name="issue_refund", order_id="O1052", line_no=1,
                    amount=300), customer_id="C052", ticket_id="T", rule_id="x", note="customer upset")


def test_refund_to_new_destination_denied(world):
    _, _, svc = world
    a = Action(name="issue_refund", order_id="O1050", line_no=1, amount=69, args={"destination": "PayNow 91234567"})
    with pytest.raises(ActionDenied, match="new_destination"):
        svc.execute(actor="agent", role="agent", action=a, customer_id="C050", ticket_id="T", rule_id="x")


def test_never_allowed_actions_denied_for_everyone(world):
    _, _, svc = world
    for role in ("agent", "cx_lead"):
        with pytest.raises(ActionDenied, match="never_allowed"):
            svc.execute(actor="x", role=role, action=Action(name="change_email", order_id="O1053"),
                        customer_id="C053", ticket_id="T", rule_id="x", note="n")


def test_cancel_refund_above_cap_only_after_the_order_is_cancelled(world):
    s, _, svc = world
    refund = Action(name="issue_refund", order_id="O1004", amount=129, args={"reason": "cancel_before_dispatch"})
    kw = dict(actor="agent", role="agent", customer_id="C004", rule_id="C1")
    with pytest.raises(ActionDenied, match="above_role_limit"):
        svc.execute(action=refund, ticket_id="T-early", **kw)  # order not cancelled yet
    svc.execute(action=Action(name="cancel_order", order_id="O1004"), ticket_id="T004", **kw)
    svc.execute(action=refund, ticket_id="T004", **kw)
    assert refunds_on(s, "O1004")[0]["amount"] == 129


# ---------- roles and approvals ----------

def test_associate_limit_and_lead_needs_written_reason(world):
    s, sys, svc = world
    s.x("INSERT INTO order_lines VALUES ('O1051', 2, 'PW-BOT', 'Trail Boot', '42', 189, 0, 0)")  # now S$378
    big = Action(name="create_reship", order_id="O1051", amount=378)
    aid = svc.queue(ticket_id="T051", customer_id="C051", action=big, rule_id="W6", summary="test", risk_score=3.8)
    with pytest.raises(ActionDenied, match="above_role_limit"):
        svc.approve(aid, actor="amy", role="cx_associate")
    with pytest.raises(ActionDenied, match="reason_required"):
        svc.approve(aid, actor="lee", role="cx_lead")
    svc.approve(aid, actor="lee", role="cx_lead", note="VIP, courier confirmed loss")
    assert s.one("SELECT status FROM approvals WHERE approval_id=?", aid)["status"] == "approved"


def test_agent_and_policy_owner_cannot_approve(world):
    _, _, svc = world
    aid = svc.queue(ticket_id="T", customer_id="C023", action=Action(name="create_reship", order_id="O1023",
                    amount=149), rule_id="W6", summary="", risk_score=1.5)
    for role in ("agent", "policy_owner"):
        with pytest.raises(ActionDenied, match="role"):
            svc.approve(aid, actor="x", role=role)


def test_approval_queue_is_ranked_by_risk(world):
    _, _, svc = world
    for score in (1.0, 5.0, 3.0):
        svc.queue(ticket_id=f"T{score}", customer_id="C023", action=Action(name="create_reship", order_id="O1023",
                  amount=149), rule_id="W6", summary="", risk_score=score)
    assert [p["risk_score"] for p in svc.pending()] == [5.0, 3.0, 1.0]


# ---------- audit and failures ----------

def test_audit_log_is_append_only(world):
    s, _, svc = world
    svc.execute(actor="agent", role="agent", action=Action(name="create_return_label", order_id="O1009", line_no=1),
                customer_id="C009", ticket_id="T009", rule_id="R7")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.x("UPDATE audit_log SET outcome='edited'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s.x("DELETE FROM audit_log")


def test_system_timeout_retries_once_then_data_gap(world):
    log = []
    with pytest.raises(DataGap):
        tools(world, cid="C045", log=log).get_order("O1045")
    assert log[-1]["attempts"] == 2 and log[-1]["ok"] is False


def test_flaky_system_recovers_on_retry(world):
    log = []
    assert tools(world, cid="C026", log=log).get_order("O1026")["order_id"] == "O1026"
    assert log[-1]["attempts"] == 2 and log[-1]["ok"] is True
