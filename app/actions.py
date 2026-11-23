"""The action service: one door for every change to an outside system.

The agent and the human approval console both call execute(), with the same checks:
  1. the action exists and is not on the never-allowed list
  2. the order belongs to the customer on the ticket
  3. the role may use this tier, up to its money limit
  4. money is never more than what was paid, and only goes to the original payment method
  5. an idempotency key, so a retry or a double click never pays twice
  6. an append-only audit row for every attempt, allowed or denied

These checks repeat some of the policy on purpose. The policy decides what should
happen. This layer makes sure that, even if the policy has a bug, the worst cases
cannot happen.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from app.mock_systems import MockSystems
from app.policy import load_policy_config
from app.store import Store
from app.types import SGT, Action

WRITE, MONEY = "WRITE_REVERSIBLE", "MONEY"
TIERS = {"open_carrier_investigation": WRITE, "cancel_order": WRITE, "update_address": WRITE,
         "create_exchange": WRITE, "create_return_label": WRITE,
         "create_reship": MONEY, "create_replacement": MONEY, "issue_refund": MONEY}
# Not built as tools at all. Listed so an attempt is denied by name and shows up in the audit log.
NEVER = {"change_payment_method", "change_email", "refund_to_new_destination", "bulk_refund", "bulk_cancel",
         "export_customers"}
ROLES = {"agent": {WRITE, MONEY}, "cx_associate": {WRITE, MONEY}, "cx_lead": {WRITE, MONEY}, "policy_owner": set()}
HUMAN_APPROVERS = {"cx_associate", "cx_lead"}


class ActionDenied(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}")
        self.code = code


class ActionService:
    def __init__(self, store: Store, systems: MockSystems, cfg: dict | None = None):
        self.s, self.sys, self.c = store, systems, cfg or load_policy_config()

    @staticmethod
    def idem_key(ticket_id: str, a: Action) -> str:
        return hashlib.sha256(f"{ticket_id}|{a.name}|{a.order_id}|{a.line_no}".encode()).hexdigest()[:16]

    def money_limit(self, role: str) -> float:
        return {"agent": self.c["AUTO_REFUND_CAP"], "cx_associate": self.c["ASSOCIATE_APPROVAL_MAX"]}.get(
            role, float("inf"))

    def execute(self, *, actor: str, role: str, action: Action, customer_id: str, ticket_id: str,
                rule_id: str, note: str | None = None) -> dict:
        key = self.idem_key(ticket_id, action)
        prior = self.s.one("SELECT result FROM idempotency WHERE idem_key=?", key)
        if prior:
            self._audit(actor, role, action, None, None, rule_id, ticket_id, key, "duplicate_ignored")
            return {**json.loads(prior["result"]), "duplicate": True}
        try:
            self._check(role, action, customer_id, note)
        except ActionDenied as e:
            self._audit(actor, role, action, None, None, rule_id, ticket_id, key, f"denied:{e.code}")
            raise
        before = self._snapshot(action.order_id)
        result = self._run(action, customer_id)
        self.s.x("INSERT INTO idempotency VALUES (?,?)", key, json.dumps(result))
        self._audit(actor, role, action, before, self._snapshot(action.order_id), rule_id, ticket_id, key, "done")
        return result

    # ---------- checks ----------
    def _check(self, role: str, a: Action, customer_id: str, note: str | None) -> None:
        if a.name in NEVER:
            raise ActionDenied("never_allowed", a.name)
        tier = TIERS.get(a.name)
        if tier is None:
            raise ActionDenied("unknown_action", a.name)
        if tier not in ROLES.get(role, set()):
            raise ActionDenied("role", f"{role} may not use {tier}")
        o = self.s.order_view(a.order_id) if a.order_id else None
        if o is None or o["customer_id"] != customer_id:
            raise ActionDenied("not_this_customer", str(a.order_id))
        dest = a.args.get("destination")
        if dest is not None and dest != o["payment_method"]:
            raise ActionDenied("new_destination", "refunds only go to the original payment method")
        if tier != MONEY:
            return
        amount = a.amount or 0
        if amount <= 0:
            raise ActionDenied("bad_amount", str(amount))
        ceiling = self._ceiling(a, o)
        if amount > ceiling + 1e-9:
            raise ActionDenied("above_paid", f"{amount} > {ceiling} paid and not yet refunded")
        if role == "agent" and self._is_cancel_refund(a, o, ceiling):
            return  # the one exception to the agent cap, checked against the order, not the caller's word
        if amount > self.money_limit(role):
            raise ActionDenied("above_role_limit", f"{amount} > {self.money_limit(role)} for {role}")
        if role == "cx_lead" and amount > self.c["ASSOCIATE_APPROVAL_MAX"] and not note:
            raise ActionDenied("reason_required", "cx_lead must write a reason above the associate limit")

    @staticmethod
    def _ceiling(a: Action, o: dict) -> float:
        left_on_order = o["total"] - o["refunded_total"]
        line = next((l for l in o["lines"] if l["line_no"] == a.line_no), None)
        if a.name == "create_reship":
            return o["total"]
        if line is None:
            return round(left_on_order, 2)
        if a.name == "create_replacement":
            return line["paid_price"]
        return round(min(line["paid_price"] - line["refunded"], left_on_order), 2)

    @staticmethod
    def _is_cancel_refund(a: Action, o: dict, ceiling: float) -> bool:
        return (a.name == "issue_refund" and a.args.get("reason") == "cancel_before_dispatch"
                and o["status"] == "cancelled" and a.line_no is None and abs((a.amount or 0) - ceiling) < 0.01)

    # ---------- doing it ----------
    def _run(self, a: Action, customer_id: str) -> dict:
        sys, oid = self.sys, a.order_id
        if a.name == "issue_refund":
            pay = self.s.order_view(oid)["payment_method"]  # never taken from the request
            return sys.issue_refund(customer_id, oid, a.line_no, a.amount, pay)
        if a.name == "cancel_order":
            return sys.cancel_order(oid)
        if a.name == "update_address":
            return sys.update_address(oid, a.args["address"], a.args["postal_code"])
        if a.name == "create_exchange":
            return sys.create_exchange(oid, a.line_no, a.args["new_size"])
        if a.name == "create_return_label":
            return sys.create_return_label(oid, a.line_no)
        if a.name == "create_replacement":
            return sys.create_replacement(oid, a.line_no, a.args["size"])
        if a.name == "create_reship":
            return sys.create_reship(oid)
        return sys.open_carrier_investigation(oid, customer_id)

    def _snapshot(self, order_id: str | None) -> dict | None:
        o = self.s.order_view(order_id) if order_id else None
        return {k: o[k] for k in ("status", "address", "refunded_total")} if o else None

    def _audit(self, actor, role, a: Action, before, after, rule_id, ticket_id, key, outcome) -> None:
        self.s.x("INSERT INTO audit_log(ts, actor, role, action, target, before, after, rule_id, ticket_id, "
                 "idem_key, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 datetime.now(SGT).isoformat(timespec="seconds"), actor, role, a.name,
                 json.dumps(a.model_dump(exclude_none=True)), json.dumps(before), json.dumps(after), rule_id,
                 ticket_id, key, outcome)

    # ---------- approval queue (the human side of the same door) ----------
    def queue(self, *, ticket_id: str, customer_id: str, action: Action, rule_id: str, summary: str,
              risk_score: float) -> int:
        return self.s.x("INSERT INTO approvals(ticket_id, customer_id, action, amount, risk_score, rule_id, summary)"
                        " VALUES (?,?,?,?,?,?,?)", ticket_id, customer_id, action.model_dump_json(),
                        action.amount, risk_score, rule_id, summary)

    def pending(self) -> list[dict]:
        return self.s.q("SELECT * FROM approvals WHERE status='pending' ORDER BY risk_score DESC, approval_id")

    def approve(self, approval_id: int, *, actor: str, role: str, note: str | None = None) -> dict:
        ap = self.s.one("SELECT * FROM approvals WHERE approval_id=?", approval_id)
        if not ap or ap["status"] != "pending":
            raise ActionDenied("not_pending", str(approval_id))
        if role not in HUMAN_APPROVERS:
            raise ActionDenied("role", f"{role} cannot approve")
        result = self.execute(actor=actor, role=role, action=Action.model_validate_json(ap["action"]),
                              customer_id=ap["customer_id"], ticket_id=ap["ticket_id"],
                              rule_id=ap["rule_id"] + "/approved", note=note)
        self.s.x("UPDATE approvals SET status='approved', decided_by=?, decided_role=?, note=? WHERE approval_id=?",
                 actor, role, note, approval_id)
        return result

    def reject(self, approval_id: int, *, actor: str, role: str, note: str) -> None:
        if role not in HUMAN_APPROVERS:
            raise ActionDenied("role", f"{role} cannot reject")
        self.s.x("UPDATE approvals SET status='rejected', decided_by=?, decided_role=?, note=? WHERE approval_id=?",
                 actor, role, note, approval_id)
