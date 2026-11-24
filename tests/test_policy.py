"""Policy tests. Written BEFORE app/policy.py, then the code was written to pass them.

Each test is one row of docs/03_policy.md. The last tests are invariant tests:
they throw hundreds of random inputs at the policy and check the money rules
can never be broken, whatever the model extracted.
"""
import random
from datetime import timedelta

import pytest

from app.autonomy import gate, load_autonomy_config
from app.policy import decide, load_policy_config
from app.types import NOW, Disposition as D, Extraction, Intent as I, PolicyInput, Reason as R

CFG = load_policy_config()
CAP = CFG["AUTO_REFUND_CAP"]


def line(price=69.0, size="41", final_sale=False, exchanges=0, refunded=0.0, no=1, sku="PW-SNK", name="Daily Sneaker"):
    return dict(line_no=no, sku=sku, name=name, size=size, paid_price=price,
                final_sale=final_sale, exchanges_used=exchanges, refunded=refunded)


def order(status="delivered", days=5, hours=None, lines=None, refunded_total=0.0):
    lines = lines or [line()]
    delivered = NOW - (timedelta(hours=hours) if hours is not None else timedelta(days=days))
    return dict(order_id="O1001", customer_id="C001", status=status,
                placed_at=(NOW - timedelta(days=days + 3)).isoformat(),
                delivered_at=delivered.isoformat() if status == "delivered" else None,
                eta="2026-11-22", total=round(sum(l["paid_price"] for l in lines), 2),
                refunded_total=refunded_total, payment_method="card ending 4242", lines=lines)


def ext(intent, **kw):
    kw.setdefault("confidence", 0.9)
    kw.setdefault("order_id", "O1001")
    return Extraction(intent=intent, **kw)


def run(extraction, o="default", **kw):
    if o == "default":
        o = order()
    kw.setdefault("stock", {"41": 3, "42": 3, "43": 0, "44": 2})
    p = PolicyInput(verified=kw.pop("verified", True), extraction=extraction, order=o,
                    order_ref_given=kw.pop("order_ref_given", True), **kw)
    return decide(p, CFG)


def names(actions):
    return [a.name for a in actions]


# ---------- global rules ----------

def test_g1_unverified_asks_and_shares_nothing():
    d = run(ext(I.WISMO), verified=False)
    assert d.disposition == D.CLARIFY and d.rule_id == "G1" and not d.actions
    assert not any("O1001" in p for p in d.reply_points)


def test_g5_invalid_model_output_escalates():
    d = run(None)
    assert (d.disposition, d.reason) == (D.ESCALATED, R.LOW_CONFIDENCE)


def test_g5_low_confidence_asks_first():
    # Changed after error analysis (decision #38): "hello??" deserves a question before a person is pulled in.
    d = run(ext(I.WISMO, confidence=0.5))
    assert (d.disposition, d.rule_id) == (D.CLARIFY, "G5") and not d.actions


def test_g8_still_unclear_after_two_of_our_replies_goes_to_a_person():
    d = run(ext(I.WISMO, confidence=0.5), prior_agent_replies=2)
    assert (d.disposition, d.reason, d.rule_id) == (D.ESCALATED, R.LOW_CONFIDENCE, "G8")


def test_g8_does_not_touch_a_clear_answer_late_in_a_thread():
    d = run(ext(I.WISMO), o=order(status="shipped"), prior_agent_replies=3)
    assert (d.rule_id, d.disposition) == ("W2", D.AUTO_RESOLVED)


def test_g0_customer_asking_for_a_person_gets_one_even_unverified():
    for verified in (True, False):
        d = run(ext(I.WISMO), verified=verified, wants_human=True)
        assert (d.disposition, d.reason, d.rule_id) == (D.ESCALATED, R.CUSTOMER_ASKED, "G0") and not d.actions


def test_g9_asking_about_another_person_asks_for_own_order_and_alerts():
    d = run(ext(I.OTHER, order_id=None), o=None, risk_flags=["other_person", "other_email"])
    assert (d.disposition, d.rule_id) == (D.CLARIFY, "G9") and not d.actions and d.alert
    assert not any("O1001" in p for p in d.reply_points)


def test_model_flag_alone_lets_a_plain_answer_out_with_an_alert():
    o = order(lines=[line(final_sale=True)])
    d = run(ext(I.RETURN_EXCHANGE, suspicious=True), o=o, risk_flags=["model_flagged"])
    assert (d.rule_id, d.disposition) == ("R3", D.AUTO_RESOLVED) and d.alert and not d.actions


def test_model_flag_alone_still_blocks_any_action():
    d = run(ext(I.CANCEL, suspicious=True), o=order(status="placed"), risk_flags=["model_flagged"])
    assert (d.rule_id, d.reason) == ("G3", R.RISK) and not d.actions and not d.approval_actions


def test_an_unknown_risk_flag_escalates_by_default():
    d = run(ext(I.WISMO), o=order(status="shipped"), risk_flags=["some_new_flag"])
    assert (d.rule_id, d.reason) == ("G3", R.RISK)


def test_g3_risk_flag_beats_a_valid_cancel():
    d = run(ext(I.CANCEL), o=order(status="placed"), risk_flags=["injection"])
    assert (d.disposition, d.reason) == (D.ESCALATED, R.RISK)
    assert not d.actions and not d.approval_actions


def test_g4_system_error_escalates_as_data_gap():
    d = run(ext(I.WISMO), data_error="oms timeout")
    assert (d.disposition, d.reason) == (D.ESCALATED, R.DATA_GAP)


def test_g6_two_intents_escalate_as_policy_gap():
    d = run(ext(I.CANCEL, other_intents=[I.REFUND_STATUS]), o=order(status="placed"))
    assert (d.disposition, d.reason) == (D.ESCALATED, R.POLICY_GAP) and not d.actions


def test_g2_order_not_on_account_is_not_found():
    d = run(ext(I.WISMO, order_id="O1050"), o=None)
    assert d.disposition == D.CLARIFY and d.rule_id == "G2"


def test_g7_no_order_reference_asks_which_order():
    d = run(ext(I.WISMO, order_id=None), o=None, order_ref_given=False)
    assert d.disposition == D.CLARIFY and d.rule_id == "G7"


@pytest.mark.parametrize("kind,reason", [("question", R.KNOWLEDGE_GAP), ("complaint", R.JUDGEMENT),
                                         ("legal", R.JUDGEMENT), (None, R.JUDGEMENT)])
def test_o1_other_always_goes_to_a_person(kind, reason):
    d = run(ext(I.OTHER, other_kind=kind, order_id=None), o=None)
    assert (d.disposition, d.reason) == (D.ESCALATED, reason)


# ---------- WISMO ----------

def test_w1_placed_gives_eta():
    d = run(ext(I.WISMO), o=order(status="placed"))
    assert (d.rule_id, d.disposition) == ("W1", D.AUTO_RESOLVED)
    assert any("22 Nov 2026" in p for p in d.reply_points)


def test_w2_shipped_gives_tracking():
    s = dict(carrier="LionPost", tracking_no="SG10010001", eta="2026-11-22", status="in_transit")
    d = run(ext(I.WISMO), o=order(status="shipped"), shipment=s)
    assert d.rule_id == "W2" and any("SG10010001" in p for p in d.reply_points)


def test_w4_not_received_under_48h_waits():
    d = run(ext(I.WISMO, delivered_not_received=True), o=order(hours=20))
    assert (d.rule_id, d.disposition) == ("W4", D.AUTO_RESOLVED) and not d.actions


def test_w5_not_received_low_value_reships_on_its_own():
    d = run(ext(I.WISMO, delivered_not_received=True), o=order(hours=72))
    assert d.rule_id == "W5" and names(d.actions) == ["open_carrier_investigation", "create_reship"]


def test_w6_not_received_high_value_needs_approval_for_reship():
    d = run(ext(I.WISMO, delivered_not_received=True), o=order(hours=72, lines=[line(price=149)]))
    assert d.disposition == D.PENDING_APPROVAL and d.rule_id == "W6"
    assert names(d.actions) == ["open_carrier_investigation"]
    assert names(d.approval_actions) == ["create_reship"]


def test_w6_reply_says_what_is_waiting():
    d = run(ext(I.WISMO, delivered_not_received=True), o=order(days=4, lines=[line(price=149.0)]))
    assert d.rule_id == "W6" and any("needs a quick check" in p for p in d.reply_points)


def test_w6_repeat_lost_parcel_claimer_needs_approval():
    d = run(ext(I.WISMO, delivered_not_received=True), o=order(hours=72), lost_claims_180d=1)
    assert d.rule_id == "W6" and names(d.approval_actions) == ["create_reship"]


# ---------- CANCEL ----------

def test_c1_cancel_before_dispatch_refunds_full_amount_even_above_cap():
    d = run(ext(I.CANCEL), o=order(status="placed", lines=[line(price=129)]))
    assert (d.rule_id, d.disposition) == ("C1", D.AUTO_RESOLVED)
    assert names(d.actions) == ["cancel_order", "issue_refund"]
    assert d.actions[1].amount == 129 and d.action_class == "cancel_before_dispatch"


def test_c2_cancel_after_dispatch_explains_returns():
    d = run(ext(I.CANCEL), o=order(status="shipped"))
    assert d.rule_id == "C2" and not d.actions


# ---------- RETURN_EXCHANGE ----------

def test_r1_first_exchange_in_stock_is_auto():
    d = run(ext(I.RETURN_EXCHANGE, target_size="44"))
    assert d.rule_id == "R1" and names(d.actions) == ["create_exchange"]


def test_r2_second_exchange_needs_approval():
    d = run(ext(I.RETURN_EXCHANGE, target_size="44"), o=order(lines=[line(exchanges=1)]))
    assert d.disposition == D.PENDING_APPROVAL and names(d.approval_actions) == ["create_exchange"]


def test_r6_target_size_out_of_stock_gives_return_label():
    d = run(ext(I.RETURN_EXCHANGE, target_size="43"))
    assert d.rule_id == "R6" and names(d.actions) == ["create_return_label"]


def test_r7_plain_return_gives_return_label():
    d = run(ext(I.RETURN_EXCHANGE))
    assert d.rule_id == "R7" and names(d.actions) == ["create_return_label"]


def test_r3_final_sale_declined():
    d = run(ext(I.RETURN_EXCHANGE, target_size="44"), o=order(lines=[line(final_sale=True)]))
    assert d.rule_id == "R3" and not d.actions


def test_r4_outside_30_days_declined():
    d = run(ext(I.RETURN_EXCHANGE), o=order(days=31))
    assert d.rule_id == "R4" and not d.actions


def test_r4_defect_outside_return_window_goes_to_damage_rules():
    d = run(ext(I.RETURN_EXCHANGE, claim_type="defect"), o=order(days=60))
    assert d.rule_id == "D1" and names(d.actions) == ["create_replacement"]


def test_r5_worn_item_declined():
    d = run(ext(I.RETURN_EXCHANGE, unworn=False))
    assert d.rule_id == "R5" and not d.actions


def test_l1_multi_item_order_without_hint_asks_which_item():
    o = order(lines=[line(), line(no=2, sku="PW-SAN", name="Strap Sandal", price=59)])
    d = run(ext(I.RETURN_EXCHANGE), o=o)
    assert (d.rule_id, d.disposition) == ("L1", D.CLARIFY)


# ---------- DAMAGED_WRONG_ITEM ----------

def test_d1_low_value_in_stock_replaced_without_photo():
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"))
    assert (d.rule_id, d.disposition) == ("D1", D.AUTO_RESOLVED)
    assert names(d.actions) == ["create_replacement"]


def test_d1_wrong_item_comes_back_on_a_free_label_damaged_does_not():
    wrong = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="wrong_item"))
    assert names(wrong.actions) == ["create_replacement", "create_return_label"]
    damaged = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"))
    assert names(damaged.actions) == ["create_replacement"]


def test_d2_low_value_out_of_stock_refunds_paid_minus_prior_refunds():
    o = order(lines=[line(price=59, size="43", refunded=10)], refunded_total=10)
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"), o=o)
    assert d.rule_id == "D2" and names(d.actions) == ["issue_refund"]
    assert d.actions[0].amount == 49


def test_d3_high_value_without_photo_asks_for_photo():
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"), o=order(lines=[line(price=129)]))
    assert (d.rule_id, d.disposition) == ("D3", D.CLARIFY) and not d.actions


def test_d6_high_value_with_photo_waits_for_human():
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"), o=order(lines=[line(price=129)]),
            photo_attached=True)
    assert d.disposition == D.PENDING_APPROVAL and not d.actions and len(d.approval_actions) == 1


def test_d5_refund_velocity_goes_to_risk():
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"), refunds_90d=2)
    assert (d.disposition, d.reason) == (D.ESCALATED, R.RISK) and not d.actions


def test_d4_wrong_item_after_14_days_declined_but_defect_at_100_days_accepted():
    assert run(ext(I.DAMAGED_WRONG_ITEM, claim_type="wrong_item"), o=order(days=20)).rule_id == "D4"
    assert run(ext(I.DAMAGED_WRONG_ITEM, claim_type="defect"), o=order(days=100)).rule_id == "D1"


def test_d7_nothing_left_to_refund_escalates():
    o = order(lines=[line(refunded=69)], refunded_total=69)
    d = run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged"), o=o)
    assert (d.rule_id, d.reason) == ("D7", R.JUDGEMENT)


# ---------- REFUND_STATUS ----------

def _refund(days_ago, status="pending", amount=69.0, order_id="O1001"):
    created = NOW - timedelta(days=days_ago)
    return dict(refund_id="RF1", order_id=order_id, line_no=1, amount=amount, status=status,
                created_at=created.isoformat(),
                settled_at=(created + timedelta(days=5)).isoformat() if status == "settled" else None)


def test_f1_recent_pending_refund_reported():
    d = run(ext(I.REFUND_STATUS), refunds=[_refund(3)])
    assert (d.rule_id, d.disposition) == ("F1", D.AUTO_RESOLVED)
    assert any("S$69.00" in p for p in d.reply_points)


def test_f2_refund_late_past_10_business_days_escalates():
    d = run(ext(I.REFUND_STATUS), refunds=[_refund(20)])
    assert (d.rule_id, d.reason) == ("F2", R.JUDGEMENT)


def test_refund_status_without_an_order_number_asks_which_order():
    # Policy owner's call after error analysis (decision #39): ask, don't guess, even with one refund on file.
    d = run(ext(I.REFUND_STATUS, order_id=None), refunds=[_refund(3)])
    assert (d.rule_id, d.disposition) == ("G7", D.CLARIFY)


def test_f3_no_refund_found():
    d = run(ext(I.REFUND_STATUS), refunds=[_refund(3, order_id="O1002")])
    assert d.rule_id == "F3"


# ---------- ADDRESS_CHANGE ----------

def test_a1_singapore_address_before_dispatch_updated():
    e = ext(I.ADDRESS_CHANGE, new_address="21 Tampines Ave 3, #05-12, Singapore 529705",
            new_postal_code="529705", new_country="SG")
    d = run(e, o=order(status="placed"))
    assert d.rule_id == "A1" and names(d.actions) == ["update_address"]


def test_a2_after_dispatch_cannot_change():
    e = ext(I.ADDRESS_CHANGE, new_address="5 Jurong West St 41", new_postal_code="649414")
    assert run(e, o=order(status="shipped")).rule_id == "A2"


def test_a4_overseas_address_explains_and_alerts():
    # Changed after error analysis (decision #38): say what we can do, alert the risk team, change nothing.
    e = ext(I.ADDRESS_CHANGE, new_address="12 Jalan Ampang, Kuala Lumpur", new_postal_code="50450",
            new_country="MY")
    d = run(e, o=order(status="placed"))
    assert (d.rule_id, d.disposition) == ("A4", D.CLARIFY) and d.alert and not d.actions
    assert any("only deliver within Singapore" in p for p in d.reply_points)


def test_a1_reply_shows_the_postal_code():
    e = ext(I.ADDRESS_CHANGE, new_address="21 Tampines Ave 3, #05-12, Singapore", new_postal_code="529705",
            new_country="SG")
    d = run(e, o=order(status="placed"))
    assert d.rule_id == "A1" and any("529705" in p for p in d.reply_points)
    assert d.actions[0].args == {"address": "21 Tampines Ave 3, #05-12, Singapore", "postal_code": "529705"}


def test_a5_bad_postal_code_asks_again():
    e = ext(I.ADDRESS_CHANGE, new_address="5 Jurong West St 41", new_postal_code="6494", new_country="SG")
    assert run(e, o=order(status="placed")).rule_id == "A5"


def test_a3_address_change_plus_refund_is_risk():
    e = ext(I.ADDRESS_CHANGE, new_address="8 Marina Blvd", new_postal_code="018981", wants_refund=True)
    d = run(e, o=order(status="placed"))
    assert (d.rule_id, d.reason) == ("A3", R.RISK) and not d.actions


# ---------- autonomy gate ----------

def test_gate_kill_switch_turns_auto_into_approval():
    cfg = load_autonomy_config()
    cfg["kill_switch"] = ["DAMAGED_WRONG_ITEM"]
    d = gate(run(ext(I.DAMAGED_WRONG_ITEM, claim_type="damaged")), I.DAMAGED_WRONG_ITEM, cfg)
    assert d.disposition == D.PENDING_APPROVAL and not d.actions
    assert names(d.approval_actions) == ["create_replacement"]


def test_gate_level_l0_routes_to_person():
    cfg = load_autonomy_config()
    cfg["levels"]["exchange_first"] = "L0"
    d = gate(run(ext(I.RETURN_EXCHANGE, target_size="44")), I.RETURN_EXCHANGE, cfg)
    assert d.disposition == D.ESCALATED and not d.actions and not d.approval_actions


# ---------- invariants over random inputs ----------

def _random_case(rng):
    price = rng.choice([19.0, 39.0, 59.0, 69.0, 80.0, 81.0, 129.0, 189.0])
    refunded = rng.choice([0.0, 0.0, 10.0, price])
    o = order(status=rng.choice(["placed", "packed", "shipped", "delivered", "cancelled"]),
              days=rng.randint(0, 200), lines=[line(price=price, refunded=refunded,
                                                     final_sale=rng.random() < 0.2,
                                                     exchanges=rng.choice([0, 1]))],
              refunded_total=refunded)
    e = ext(rng.choice(list(I)), confidence=rng.random(),
            claim_type=rng.choice([None, "wrong_item", "damaged", "defect"]),
            target_size=rng.choice([None, "41", "43", "44"]), unworn=rng.choice([None, True, False]),
            delivered_not_received=rng.random() < 0.5, wants_refund=rng.random() < 0.3,
            other_intents=rng.choice([[], [], [I.CANCEL]]),
            new_address=rng.choice([None, "1 Test Rd"]), new_postal_code=rng.choice([None, "123456", "50450"]))
    flags = rng.choice([[], [], [], ["injection"], ["model_flagged"], ["other_person"], ["model_flagged", "other_email"]])
    return o, e, flags, rng.choice([0, 1, 3]), rng.random() < 0.5


def test_invariants_hold_for_500_random_inputs():
    rng = random.Random(42)
    for _ in range(500):
        o, e, flags, r90, photo = _random_case(rng)
        d = run(e, o=o, risk_flags=flags, refunds_90d=r90, photo_attached=photo,
                prior_agent_replies=rng.choice([0, 0, 1, 2]), wants_human=rng.random() < 0.1)
        refundable = round(o["lines"][0]["paid_price"] - o["lines"][0]["refunded"], 2)
        for a in d.actions + d.approval_actions:
            if a.name == "issue_refund" and a.args.get("reason") != "cancel_before_dispatch":
                assert a.amount <= refundable + 1e-9, (e, a)
        for a in d.actions:
            if a.name in ("issue_refund", "create_replacement", "create_reship"):
                if a.args.get("reason") != "cancel_before_dispatch":
                    assert a.amount <= CAP, (e, a)
        if flags or e.confidence < CFG["MIN_CONFIDENCE"]:
            assert not d.actions and not d.approval_actions
        if d.disposition in (D.ESCALATED, D.CLARIFY):
            assert not d.actions
