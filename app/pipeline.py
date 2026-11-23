"""The pipeline: one ticket in, one trace out.

  identity (code) -> scan (code) -> classify (small model) -> facts (tools locked to the customer)
  -> policy (code) -> autonomy gate (code) -> actions (shared action service)
  -> draft (mid model, no tools) -> checks (code) -> judge (second model) -> send (code)

Models only read and write text. Code makes every decision and takes every action.
The trace is also the case file a human sees when a ticket is escalated.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from app import steps
from app.actions import ActionDenied, ActionService
from app.autonomy import gate, load_autonomy_config
from app.llm import LLMUnavailable
from app.mock_systems import MockSystems
from app.policy import TEAM_REPLY, decide, load_policy_config, pick_line
from app.risk import scan_message, scan_tool_text
from app.store import Store
from app.tools import DataGap, ScopedTools, ToolNotAllowed
from app.types import Disposition as D, Intent as I, PolicyInput, Reason as R
from app.verify import run_checks
from data.seed import seeded_store

ORDER_RE = re.compile(r"\bO\d{4}\b", re.I)
RULE_INTENT = {"W": I.WISMO, "C": I.CANCEL, "R": I.RETURN_EXCHANGE, "D": I.DAMAGED_WRONG_ITEM,
               "F": I.REFUND_STATUS, "A": I.ADDRESS_CHANGE}
HOLD_REPLY = "Hi there,\n\nThanks for your message. " + TEAM_REPLY + "\n\nPebblewalk Support"


@dataclass
class Env:
    llm: object
    mode: str
    detection_on: bool = True  # off = no regex scanner and no model "suspicious" flag (for the ablation run)
    run_id: str = "dev"
    policy_cfg: dict = field(default_factory=load_policy_config)
    autonomy_cfg: dict = field(default_factory=load_autonomy_config)


def identify(ticket: dict, store: Store, text: str) -> tuple[str | None, str]:
    """Who is this? From the login session, or an order number plus the matching email. Never from what the
    message claims."""
    if ticket.get("authenticated_customer_id"):
        return ticket["authenticated_customer_id"], "session"
    m = ORDER_RE.search(text)
    email = (ticket.get("from_email") or "").lower()
    if m and email:
        row = store.one("SELECT c.customer_id FROM orders o JOIN customers c ON c.customer_id=o.customer_id "
                        "WHERE o.order_id=? AND lower(c.email)=?", m.group(0).upper(), email)
        if row:
            return row["customer_id"], "order_number+email"
    return None, "unverified"


def gather_facts(tools: ScopedTools | None, ext, text: str, cfg: dict, tr: dict, detection_on: bool) -> dict:
    f = dict(order=None, order_ref_given=False, shipment=None, stock={}, refunds=[], refunds_90d=0,
             lost_claims_180d=0, data_error=None)
    if tools is None or ext is None:
        return f
    try:
        f.update(tools.get_risk_signals(cfg["REFUND_VELOCITY_DAYS"], cfg["LOST_CLAIM_LOOKBACK_DAYS"]))
        if ext.intent == I.REFUND_STATUS:
            f["refunds"] = tools.get_refunds()
            return f
        if ext.intent == I.OTHER:
            return f
        oid = ext.order_id or (ORDER_RE.search(text).group(0).upper() if ORDER_RE.search(text) else None)
        if oid:
            f["order_ref_given"] = True
        else:  # step removed in discovery: don't ask a logged-in customer for an order number we can work out
            recent = tools.find_recent_orders(cfg["RECENT_ORDER_DAYS"])
            if len(recent) == 1:
                oid = tr["order_inferred"] = recent[0]
        if not oid:
            return f
        o = f["order"] = tools.get_order(oid)
        if o is None:
            return f
        if ext.intent == I.WISMO:
            s = tools.get_shipment(oid)
            if s and detection_on and scan_tool_text(s.get("courier_note")):
                tr["security_alerts"].append({"source": "courier_note", "order_id": oid,
                                              "handled": "text removed; it never reaches a model either way"})
                s = {**s, "courier_note": "[removed: suspicious text]"}
            f["shipment"] = s
        if ext.intent in (I.RETURN_EXCHANGE, I.DAMAGED_WRONG_ITEM):
            line = pick_line(o, ext.item_hint)
            if line:
                f["stock"] = tools.check_stock(line["sku"])
    except DataGap as e:
        f["data_error"] = str(e)
    except ToolNotAllowed as e:
        f["data_error"] = f"tool not allowed: {e}"
    return f


def _execute(decision, tools, service, tr, facts, ext, cid):
    done, extra_points = set(), []
    for a in decision.actions:
        rec = {"name": a.name, "order_id": a.order_id, "line_no": a.line_no, "amount": a.amount}
        tr["actions"].append(rec)
        try:
            res = tools.act(a, decision.rule_id)
            rec.update(status="done", result=res)
            done.add(a.name)
            if res.get("return_label"):
                extra_points.append(f"Your free return label: {res['return_label']}")
        except (ActionDenied, DataGap, ToolNotAllowed) as e:
            rec.update(status="failed", error=str(e)[:200])
            reason = R.DATA_GAP if isinstance(e, DataGap) else R.RISK
            return decision.model_copy(update=dict(
                disposition=D.ESCALATED, reason=reason, rule_id=decision.rule_id + "/X1", approval_actions=[],
                reply_points=[TEAM_REPLY], note=f"action {a.name} failed: {e}")), done, []
    o = facts.get("order") or {}
    for a in decision.approval_actions:
        risk = round((a.amount or 0) / 100 + facts["refunds_90d"] + 2 * facts["lost_claims_180d"], 2)
        summary = (f"{cid} | {ext.intent.value if ext else '?'} | rule {decision.rule_id} | {a.name} on {a.order_id} "
                   f"S${a.amount or 0:.2f} | order {o.get('status')}, delivered {str(o.get('delivered_at'))[:10]} | "
                   f"refunds 90d: {facts['refunds_90d']}, lost claims 180d: {facts['lost_claims_180d']} | "
                   f"{decision.note}")
        aid = service.queue(ticket_id=tr["ticket_id"], customer_id=cid, action=a, rule_id=decision.rule_id,
                            summary=summary, risk_score=risk)
        tr["actions"].append({"name": a.name, "order_id": a.order_id, "line_no": a.line_no, "amount": a.amount,
                              "status": "queued", "approval_id": aid})
    return decision, done, extra_points


def _reply(env, decision, name, text, done, tr):
    feedback, draft_text, verdict, checks, why_held = None, None, None, [], None
    for attempt in (1, 2):  # draft, check, and regenerate once with the failure shown
        try:
            draft_text = steps.draft(env.llm, decision, name, text, tr, attempt, feedback)
        except LLMUnavailable:
            why_held = "drafting model unavailable"
            break
        checks = run_checks(draft_text, decision.reply_points, done, name)
        failed = [c for c in checks if not c["pass"]]
        if failed:
            feedback = "; ".join(c["detail"] for c in failed)
            continue
        verdict = steps.judge(env.llm, text, decision, draft_text, tr)
        if verdict["pass"] is True:
            return draft_text, checks, verdict, None
        if verdict["pass"] is None:
            why_held = verdict["reason"]
            break
        feedback = "judge said: " + verdict["reason"]
    tr["held_draft"] = draft_text
    return None, checks, verdict, why_held or feedback


def run_ticket(ticket: dict, env: Env, store: Store | None = None) -> dict:
    t_start = time.perf_counter()
    store = store or seeded_store()
    systems = MockSystems(store)
    service = ActionService(store, systems, env.policy_cfg)
    text = "\n".join(m["text"] for m in ticket["messages"] if m["role"] == "customer")
    tr = {"ticket_id": ticket["ticket_id"], "run_id": env.run_id, "mode": env.mode, "detection_on": env.detection_on,
          "steps": [], "tool_calls": [], "security_alerts": [], "actions": []}

    cid, how = identify(ticket, store, text)
    customer = store.one("SELECT name, email FROM customers WHERE customer_id=?", cid) if cid else None
    tr.update(customer_id=cid, identity=how)
    flags = scan_message(text, customer["email"] if customer else None) if env.detection_on else []

    ext, provider_error = None, None
    try:
        ext = steps.classify(env.llm, text, tr)
    except LLMUnavailable as e:
        provider_error = f"model provider unavailable: {str(e)[:120]}"
    if ext and ext.suspicious and env.detection_on:
        flags.append("model_flagged")
    tr["extraction"], tr["risk_flags"] = (ext.model_dump(mode="json") if ext else None), flags

    tools = ScopedTools(cid, ext.intent if ext else None, systems, service, ticket["ticket_id"],
                        tr["tool_calls"]) if cid else None
    facts = gather_facts(tools, ext, text, env.policy_cfg, tr, env.detection_on)
    if provider_error:
        facts["data_error"] = provider_error
    p = PolicyInput(verified=cid is not None, extraction=ext, risk_flags=flags,
                    photo_attached=any(a.get("type") == "image" for a in ticket.get("attachments", [])), **facts)
    policy_decision = decide(p, env.policy_cfg)
    decision = gate(policy_decision, ext.intent if ext else None, env.autonomy_cfg)
    tr["gate_changed"] = decision.rule_id != policy_decision.rule_id

    done = set()
    if tools is not None and (decision.actions or decision.approval_actions):
        tools.intent = RULE_INTENT.get(decision.rule_id[0], tools.intent)  # allowlist follows the rule, not the model
        decision, done, extra = _execute(decision, tools, service, tr, facts, ext, cid)
        decision = decision.model_copy(update={"reply_points": decision.reply_points + extra})

    reply, checks, verdict, why_held = _reply(env, decision, customer["name"] if customer else None, text, done, tr)
    if reply is None:
        reply = HOLD_REPLY  # a fixed template, so the customer still hears from us
        if decision.disposition in (D.AUTO_RESOLVED, D.CLARIFY):
            decision = decision.model_copy(update=dict(disposition=D.ESCALATED, reason=R.LOW_CONFIDENCE,
                                                       rule_id=decision.rule_id + "/V1",
                                                       note=(decision.note + f"; reply held: {why_held}").strip("; ")))
    thread = f"{ticket['channel']}:{cid or ticket.get('from_email')}"
    store.x("INSERT INTO outbox VALUES (?,?,?,?)", ticket["ticket_id"], thread, cid, reply)

    llm_steps = [s for s in tr["steps"] if "latency_ms" in s]
    code_ms = (time.perf_counter() - t_start) * 1000 - sum(s.get("wall_ms", 0) for s in tr["steps"])
    tr.update(
        decision=decision.model_dump(mode="json", include={"rule_id", "disposition", "reason", "action_class", "note",
                                                           "reply_points"}),
        rule_id=decision.rule_id, disposition=decision.disposition.value,
        reason_code=decision.reason.value if decision.reason else None,
        reply=reply, reply_held=why_held is not None or reply == HOLD_REPLY, checks=checks, judge=verdict,
        models={s["step"]: s.get("model") for s in llm_steps},
        tokens_in=sum(s.get("tokens_in", 0) for s in llm_steps), tokens_out=sum(s.get("tokens_out", 0) for s in llm_steps),
        cost_usd=round(sum(s.get("cost_usd", 0) for s in llm_steps), 8),
        latency_ms=round(max(code_ms, 0) + sum(s.get("latency_ms", 0) for s in llm_steps)),
        price_is_stand_in=any(s.get("price_is_stand_in") for s in llm_steps),
    )
    return tr
