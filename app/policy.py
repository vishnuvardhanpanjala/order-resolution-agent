"""The policy. Plain code: no model, no database, no network.

decide() takes the facts and what the model read from the message, and returns
one Decision. Every rule has an ID (G1, W2, D6...) that is written to the trace
and the audit log, so any outcome can be traced to one row of docs/03_policy.md.

The model's output is an input here, never the answer. If the model gets the
intent wrong, the worst case is a policy-allowed action on the customer's own
order. It cannot invent an amount, a destination or a customer.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from app.types import Action, Decision, Disposition as D, Intent as I, PolicyInput, Reason as R

ROOT = Path(__file__).resolve().parent.parent
TEAM_REPLY = "A member of our team will look at this and reply within 1 business day."


def load_policy_config(path: Path = ROOT / "config" / "policy.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())["constants"]


# ---------- small helpers ----------

def _dt(x) -> datetime:
    return x if isinstance(x, datetime) else datetime.fromisoformat(str(x))


def money(x: float) -> str:
    return f"S${x:,.2f}"


def day(x) -> str:
    d = x if isinstance(x, date) and not isinstance(x, datetime) else _dt(x)
    return f"{d.day} {d:%b %Y}"


def business_days_between(start, end) -> int:
    """Weekdays after start, up to and including end. Public holidays are ignored (known gap)."""
    d, end_d, n = _dt(start).date(), _dt(end).date(), 0
    while d < end_d:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def pick_line(order: dict, hint: str | None) -> dict | None:
    lines = order["lines"]
    if len(lines) == 1:
        return lines[0]
    if hint:
        h = hint.lower()
        found = [l for l in lines if l["sku"].lower() in h or l["name"].lower() in h or h in l["name"].lower()]
        if len(found) == 1:
            return found[0]
    return None


def _d(rule, disp, points, reason=None, cls="info", actions=(), approvals=(), note=""):
    return Decision(rule_id=rule, disposition=disp, reason=reason, action_class=cls, actions=list(actions),
                    approval_actions=list(approvals), reply_points=list(points), note=note)


def _info(rule, points, note=""):
    return _d(rule, D.AUTO_RESOLVED, points, note=note)


def _clarify(rule, points, note=""):
    return _d(rule, D.CLARIFY, points, note=note)


def _escalate(rule, reason, note, points=None):
    # The customer never sees the reason. Telling an attacker "flagged as RISK" helps them.
    return _d(rule, D.ESCALATED, points or [TEAM_REPLY], reason=reason, note=note)


# ---------- the decision ----------

def decide(p: PolicyInput, c: dict) -> Decision:
    e = p.extraction
    # Global rules first. The safest checks come first.
    if not p.verified:
        return _clarify("G1", ["To help with an order, please reply with your order number and the email "
                               "address you used to place it."], "identity not verified")
    if p.risk_flags:
        return _escalate("G3", R.RISK, "risk flags: " + ", ".join(p.risk_flags))
    if p.data_error:  # checked before "model output invalid", so a provider outage is DATA_GAP, not LOW_CONFIDENCE
        return _escalate("G4", R.DATA_GAP, f"system error: {p.data_error}")
    if e is None:
        return _escalate("G5", R.LOW_CONFIDENCE, "model output invalid twice")
    if e.confidence < c["MIN_CONFIDENCE"]:
        return _escalate("G5", R.LOW_CONFIDENCE, f"confidence {e.confidence:.2f}")
    money_intents = {I.REFUND_STATUS, I.CANCEL, I.DAMAGED_WRONG_ITEM}
    if e.intent == I.ADDRESS_CHANGE and (e.wants_refund or set(e.other_intents) & money_intents):
        return _escalate("A3", R.RISK, "address change asked together with money")
    if e.other_intents:
        return _escalate("G6", R.POLICY_GAP, "more than one request: " +
                         ", ".join([e.intent.value] + [i.value for i in e.other_intents]))
    if e.intent == I.OTHER:
        reason = R.KNOWLEDGE_GAP if e.other_kind == "question" else R.JUDGEMENT
        return _escalate("O1", reason, f"other: {e.other_kind or 'unknown'}")
    if e.intent == I.REFUND_STATUS:
        return _refund_status(p, c)

    o = p.order
    if o is None:
        if p.order_ref_given:
            ref = f" {e.order_id}" if e.order_id else ""
            return _clarify("G2", [f"We could not find order{ref} on your account. Please check the order "
                                   "number and send it again."], "order not found on this account")
        return _clarify("G7", ["Which order is this about? Please reply with the order number. It starts "
                               "with the letter O."], "no order reference")
    handler = {I.WISMO: _wismo, I.CANCEL: _cancel, I.RETURN_EXCHANGE: _return,
               I.DAMAGED_WRONG_ITEM: _damaged, I.ADDRESS_CHANGE: _address}[e.intent]
    return handler(p, c, o)


def _wismo(p, c, o):
    e, s, oid = p.extraction, p.shipment or {}, o["order_id"]
    if o["status"] in ("placed", "packed"):
        return _info("W1", [f"Order {oid} is being prepared and has not shipped yet.",
                            f"Expected delivery: {day(o['eta'])}."])
    if o["status"] == "shipped":
        return _info("W2", [f"Order {oid} has shipped with {s.get('carrier', 'our courier')}.",
                            f"Tracking number: {s.get('tracking_no')}.",
                            f"Expected delivery: {day(s.get('eta') or o['eta'])}."])
    if o["status"] == "cancelled":
        return _info("W0", [f"Order {oid} was cancelled."])
    delivered = _dt(o["delivered_at"])
    if not e.delivered_not_received:
        return _info("W3", [f"Order {oid} was delivered on {day(delivered)}."])
    wait = c["DELIVERED_WAIT_HOURS"]
    if (p.now - delivered).total_seconds() / 3600 < wait:
        return _info("W4", [f"Order {oid} was marked as delivered on {day(delivered)}.",
                            f"Parcels marked delivered often turn up within {wait} hours. Please check your "
                            "letterbox, parcel locker, building reception and neighbours.",
                            f"If it has still not arrived by {day(delivered + timedelta(hours=wait))}, reply "
                            "here and we will open a search with the courier."])
    search = Action(name="open_carrier_investigation", order_id=oid)
    reship = Action(name="create_reship", order_id=oid, amount=o["total"])
    if o["total"] <= c["AUTO_REFUND_CAP"] and p.lost_claims_180d == 0:
        return _d("W5", D.AUTO_RESOLVED, [f"We have opened a search with the courier for order {oid}.",
                                          "We are also sending you a replacement parcel now, at no cost. "
                                          "The new tracking number will be sent in this chat within 1 "
                                          "business day."],
                  cls="reship_within_cap", actions=[search, reship])
    return _d("W6", D.PENDING_APPROVAL, [f"We have opened a search with the courier for order {oid}.", TEAM_REPLY],
              cls="reship_over_cap", actions=[search], approvals=[reship],
              note="reship above cap or earlier lost-parcel claim")


def _cancel(p, c, o):
    oid = o["order_id"]
    if o["status"] in ("placed", "packed"):
        amount = round(o["total"] - o.get("refunded_total", 0), 2)
        acts = [Action(name="cancel_order", order_id=oid)]
        points = [f"Order {oid} is cancelled."]
        if amount > 0:
            acts.append(Action(name="issue_refund", order_id=oid, amount=amount,
                               args={"reason": "cancel_before_dispatch"}))
            points += [f"A refund of {money(amount)} is on its way to your original payment method "
                       f"({o['payment_method']}).", "Refunds take 5 to 10 business days to show."]
        return _d("C1", D.AUTO_RESOLVED, points, cls="cancel_before_dispatch", actions=acts)
    if o["status"] in ("shipped", "delivered"):
        return _info("C2", [f"Order {oid} has already left our warehouse, so it can no longer be cancelled.",
                            f"Once it arrives, you can return it unworn within {c['RETURN_WINDOW_DAYS']} days "
                            "for a refund. Just message us here and we will send a free return label."])
    return _info("C3", [f"Order {oid} was already cancelled."])


def _return(p, c, o):
    e, oid = p.extraction, o["order_id"]
    if o["status"] != "delivered":
        return _escalate("R0", R.POLICY_GAP, "return or exchange asked before delivery")
    line = pick_line(o, e.item_hint)
    if line is None:
        return _clarify("L1", [f"Which item in order {oid} is this about? Please tell us the product name."])
    days = (p.now - _dt(o["delivered_at"])).days
    too_late = days > c["RETURN_WINDOW_DAYS"]
    if e.claim_type == "defect" and (too_late or line["final_sale"]):
        return _damaged(p, c, o)
    if line["final_sale"]:
        return _info("R3", [f"The {line['name']} in order {oid} was bought on final sale, so it cannot be "
                            "returned or exchanged."])
    if too_late:
        return _info("R4", [f"Order {oid} was delivered on {day(o['delivered_at'])}. That is more than "
                            f"{c['RETURN_WINDOW_DAYS']} days ago, so it is outside our return window."])
    if e.unworn is False:
        return _info("R5", ["We can only take back unworn items, so we cannot accept a return or exchange "
                            "for this pair."])
    refundable = round(line["paid_price"] - line["refunded"], 2)
    label = Action(name="create_return_label", order_id=oid, line_no=line["line_no"])
    label_points = ["We have created a free return label for you.",
                    f"Once the item arrives back unworn, we will refund {money(refundable)} to your original "
                    "payment method."]
    if e.target_size:
        swap = Action(name="create_exchange", order_id=oid, line_no=line["line_no"],
                      args={"new_size": e.target_size})
        if line["exchanges_used"] >= c["FREE_EXCHANGES_PER_LINE"]:
            return _d("R2", D.PENDING_APPROVAL, ["This item has already been exchanged once, so a team member "
                                                 "needs to approve another exchange.", TEAM_REPLY],
                      cls="exchange_repeat", approvals=[swap], note="second exchange on the same line")
        if p.stock.get(e.target_size, 0) > 0:
            return _d("R1", D.AUTO_RESOLVED, [f"Your exchange is set up: {line['name']} size {line['size']} "
                                              f"for size {e.target_size}.",
                                              "Please send the pair back unworn with the free return label. "
                                              "The new pair ships as soon as the courier scans your parcel."],
                      cls="exchange_first", actions=[swap])
        return _d("R6", D.AUTO_RESOLVED, [f"Size {e.target_size} of the {line['name']} is out of stock, so we "
                                          "cannot exchange it right now."] + label_points,
                  cls="return_label", actions=[label])
    return _d("R7", D.AUTO_RESOLVED, label_points, cls="return_label", actions=[label])


def _damaged(p, c, o):
    e, oid = p.extraction, o["order_id"]
    if o["status"] != "delivered":
        return _escalate("D0", R.POLICY_GAP, "damage claim before delivery")
    line = pick_line(o, e.item_hint)
    if line is None:
        return _clarify("L1", [f"Which item in order {oid} is this about? Please tell us the product name."])
    claim = e.claim_type or "damaged"
    window = c["DEFECT_WINDOW_DAYS"] if claim == "defect" else c["WRONG_ITEM_WINDOW_DAYS"]
    if (p.now - _dt(o["delivered_at"])).days > window:
        what = "a fault" if claim == "defect" else "a wrong or damaged item"
        return _info("D4", [f"Order {oid} was delivered on {day(o['delivered_at'])}. Claims for {what} need to "
                            f"reach us within {window} days of delivery, so we cannot accept this one."])
    if p.refunds_90d >= c["REFUND_VELOCITY_LIMIT"]:
        return _escalate("D5", R.RISK, f"{p.refunds_90d} refunds in the last {c['REFUND_VELOCITY_DAYS']} days")
    value = round(min(line["paid_price"] - line["refunded"], o["total"] - o.get("refunded_total", 0)), 2)
    if value <= 0:
        return _escalate("D7", R.JUDGEMENT, "nothing left to refund on this line")
    in_stock = p.stock.get(line["size"], 0) > 0
    if in_stock:
        fix = Action(name="create_replacement", order_id=oid, line_no=line["line_no"], amount=value,
                     args={"size": line["size"]})
    else:
        fix = Action(name="issue_refund", order_id=oid, line_no=line["line_no"], amount=value)
    sorry = f"Sorry about the {line['name']}."
    if value <= c["PHOTO_REQUIRED_ABOVE"] and value <= c["AUTO_REFUND_CAP"]:
        if in_stock:
            return _d("D1", D.AUTO_RESOLVED, [sorry, f"We are sending you a replacement pair in size "
                                                     f"{line['size']} at no cost.",
                                              "You do not need to send the old pair back."],
                      cls="replacement_within_cap", actions=[fix])
        return _d("D2", D.AUTO_RESOLVED, [sorry, f"That size is out of stock, so we have refunded {money(value)} "
                                                 f"to your original payment method ({o['payment_method']}).",
                                          "Refunds take 5 to 10 business days to show.",
                                          "You do not need to send the old pair back."],
                  cls="refund_within_cap", actions=[fix])
    if not p.photo_attached:
        return _clarify("D3", [sorry, "Please reply with a clear photo of the problem so our team can check it.",
                               "Once we have the photo, a team member will reply within 1 business day."])
    return _d("D6", D.PENDING_APPROVAL, ["Thanks for the photo.", "A team member will check it and reply "
                                         "within 1 business day."],
              cls="replacement_over_cap" if in_stock else "refund_over_cap", approvals=[fix],
              note=f"photo check needed, line value {money(value)}")


def _refund_status(p, c):
    e = p.extraction
    refunds = [r for r in p.refunds if not e.order_id or r["order_id"] == e.order_id]
    if not refunds:
        ref = f" for order {e.order_id}" if e.order_id else ""
        return _info("F3", [f"We cannot find a refund on your account{ref}.",
                            "If you were expecting one, tell us which order and what happened, and we will check."])
    late = [r for r in refunds if r["status"] == "pending"
            and business_days_between(r["created_at"], p.now) > c["REFUND_SLA_BUSINESS_DAYS"]]
    if late:
        return _escalate("F2", R.JUDGEMENT, "refund pending past the SLA",
                         ["Your refund is taking longer than it should. We have passed it to our payments "
                          "team, who will reply within 1 business day."])
    points = []
    for r in refunds:
        if r["status"] == "settled":
            points.append(f"The refund of {money(r['amount'])} for order {r['order_id']} was completed on "
                          f"{day(r['settled_at'])}.")
        else:
            points.append(f"The refund of {money(r['amount'])} for order {r['order_id']} was sent on "
                          f"{day(r['created_at'])} to your original payment method. Refunds take 5 to 10 "
                          "business days to show.")
    return _info("F1", points)


def _address(p, c, o):
    e, oid = p.extraction, o["order_id"]
    if o["status"] in ("shipped", "delivered"):
        return _info("A2", [f"Order {oid} has already shipped, so we can no longer change its delivery address."])
    if o["status"] == "cancelled":
        return _info("A0", [f"Order {oid} was cancelled, so there is no delivery to change."])
    country = (e.new_country or "SG").strip().upper()
    postal = (e.new_postal_code or "").strip()
    if country not in ("SG", "SINGAPORE") or (postal and not re.fullmatch(r"\d{6}", postal)):
        return _escalate("A4", R.RISK, "delivery address outside Singapore")
    if not e.new_address or not postal:
        return _clarify("A5", ["Please send the full new address, including the 6-digit postal code."])
    act = Action(name="update_address", order_id=oid, args={"address": e.new_address, "postal_code": postal})
    return _d("A1", D.AUTO_RESOLVED, [f"The delivery address for order {oid} is now: {e.new_address}."],
              cls="address_update", actions=[act])
