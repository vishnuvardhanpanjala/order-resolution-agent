"""The baseline: one big prompt. Policy as prose, all the facts, the message, and the model decides.

It sees exactly the facts our system fetches for the same verified customer, including the raw
courier note. Its JSON answer {action, amount, reply} goes to a sandbox that records what WOULD
have happened. Nothing is executed. The only difference from our system is who decides.
"""
from __future__ import annotations

import json

from app.llm import LLMUnavailable, load_prompt
from app.pipeline import ORDER_RE, identify
from app.policy import load_policy_config, pick_line
from app.steps import _json_part
from data.seed import seeded_store

VERSION, PROMPT = load_prompt("baseline")
WOULD_DO = {"refund": ["issue_refund"], "replacement": ["create_replacement"], "exchange": ["create_exchange"],
            "replacement_and_return_label": ["create_replacement", "create_return_label"],
            "return_label": ["create_return_label"], "cancel_and_refund": ["cancel_order", "issue_refund"],
            "reship": ["create_reship"], "carrier_investigation": ["open_carrier_investigation"],
            "update_address": ["update_address"]}
DISPOSITION = {"escalate": "ESCALATED", "ask_customer": "CLARIFY", "request_approval": "PENDING_APPROVAL"}


def facts_for(ticket: dict, store, text: str) -> tuple[str | None, dict]:
    cid, _ = identify(ticket, store, text)
    if cid is None:
        return None, {"verified": False}
    cfg = load_policy_config()
    cust = store.one("SELECT name FROM customers WHERE customer_id=?", cid)
    m = ORDER_RE.search(text)
    oid = m.group(0).upper() if m else None
    o = store.order_view(oid) if oid else None
    if o and o["customer_id"] != cid:
        o = None  # same scoping as our system: the baseline can't see other customers' orders either
    facts = {"verified": True, "customer_name": cust["name"], "now": "2026-11-20T10:00+08:00",
             "refunds_last_90_days": store.one("SELECT COUNT(*) AS n FROM refunds WHERE customer_id=? AND "
                                               "created_at>=?", cid, _days_ago(90))["n"],
             "lost_parcel_claims_last_180_days": store.one("SELECT COUNT(*) AS n FROM lost_claims WHERE "
                                                           "customer_id=? AND created_at>=?", cid, _days_ago(180))["n"],
             "refunds": store.q("SELECT order_id, amount, status, created_at, settled_at FROM refunds WHERE "
                                "customer_id=?", cid),
             "order": o, "order_number_given": oid if m else None}
    if o:
        facts["shipment"] = store.one("SELECT carrier, tracking_no, status, eta, courier_note FROM shipments "
                                      "WHERE order_id=?", oid)
        line = pick_line(o, None) or o["lines"][0]
        facts["stock_for_" + line["sku"]] = {r["size"]: r["qty"] for r in
                                             store.q("SELECT size, qty FROM stock WHERE sku=?", line["sku"])}
    return cid, facts


def _days_ago(days: int) -> str:
    from datetime import timedelta
    from app.types import NOW
    return (NOW - timedelta(days=days)).isoformat()


def run_ticket(ticket: dict, llm) -> dict:
    store = seeded_store()
    text = "\n".join(m["text"] for m in ticket["messages"] if m["role"] == "customer")
    convo = text if not any(m["role"] == "agent" for m in ticket["messages"]) else "\n".join(
        ("Customer: " if m["role"] == "customer" else "Pebblewalk: ") + m["text"] for m in ticket["messages"])
    cid, facts = facts_for(ticket, store, text)
    if ticket.get("attachments"):
        facts["attachments"] = ticket["attachments"]
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": f"<facts>\n{json.dumps(facts, default=str)}\n</facts>\n"
                                            f"<customer_message>\n{convo}\n</customer_message>"}]
    tr = {"ticket_id": ticket["ticket_id"], "suite": "baseline", "customer_id": cid, "prompt_version": VERSION}
    try:
        out, usage = llm.complete("baseline", messages, json_mode=True)
    except LLMUnavailable as e:
        return {**tr, "error": str(e)[:200], "action": None, "disposition": "ESCALATED", "would_do": [], "reply": "",
                "amount": None, "cost_usd": 0, "latency_ms": 0}
    try:
        v = json.loads(_json_part(out))
    except ValueError:
        v = {}
    action = str(v.get("action") or "invalid")
    amount = v.get("amount") if isinstance(v.get("amount"), (int, float)) else None
    return {**tr, "action": action, "amount": amount, "order_id": v.get("order_id"), "reply": str(v.get("reply") or ""),
            "would_do": WOULD_DO.get(action, []),
            "disposition": DISPOSITION.get(action, "ESCALATED" if action == "invalid" else "AUTO_RESOLVED"),
            "model": usage.get("model"), "cost_usd": usage.get("cost_usd", 0), "latency_ms": usage.get("latency_ms", 0),
            "tokens_in": usage.get("tokens_in", 0), "tokens_out": usage.get("tokens_out", 0),
            "price_is_stand_in": usage.get("price_is_stand_in", False)}
