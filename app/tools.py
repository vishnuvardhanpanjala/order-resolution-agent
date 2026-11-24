"""The agent's tools, built already bound to one verified customer.

The model never supplies a customer ID and never picks a tool. Code picks tools,
and only from the allowlist for the ticket's intent. Reads check the order
belongs to this customer. If it doesn't, the answer is "not found", the same as
an order that doesn't exist, so the agent can't confirm someone else's order.
Writes go through the shared action service, the same one humans use.
"""
from __future__ import annotations

from datetime import timedelta

from app.actions import ActionService
from app.mock_systems import MockSystems, SystemTimeout
from app.types import NOW, Action, Intent

ALLOWLIST: dict[Intent, set[str]] = {
    Intent.WISMO: {"get_order", "get_shipment", "open_carrier_investigation", "create_reship"},
    Intent.CANCEL: {"get_order", "cancel_order", "issue_refund"},
    Intent.RETURN_EXCHANGE: {"get_order", "check_stock", "create_exchange", "create_return_label"},
    Intent.DAMAGED_WRONG_ITEM: {"get_order", "check_stock", "create_replacement", "issue_refund",
                                "create_return_label"},  # a wrong item comes back on a free label
    Intent.REFUND_STATUS: {"get_refunds"},
    Intent.ADDRESS_CHANGE: {"get_order", "update_address"},
    Intent.OTHER: set(),
}
# Account-level read every intent may use: refund and lost-parcel counts.
ALWAYS_READ = {"get_risk_signals"}


class ToolNotAllowed(Exception):
    pass


class DataGap(Exception):
    pass


class ScopedTools:
    def __init__(self, customer_id: str, intent: Intent | None, systems: MockSystems, actions: ActionService,
                 ticket_id: str, log: list):
        self.cid, self.intent, self.sys, self.actions = customer_id, intent, systems, actions
        self.ticket_id, self.log = ticket_id, log

    def allowed(self, name: str) -> bool:
        return name in ALWAYS_READ or (self.intent is not None and name in ALLOWLIST[self.intent])

    def _call(self, name: str, fn, args: dict):
        entry = {"tool": name, "args": args, "allowed": self.allowed(name)}
        self.log.append(entry)
        if not entry["allowed"]:
            entry["error"] = "not in allowlist for this intent"
            raise ToolNotAllowed(name)
        for attempt in (1, 2):  # retry once, then give up and escalate as DATA_GAP
            try:
                out = fn()
                entry.update(ok=True, attempts=attempt, result=_summary(out))
                return out
            except SystemTimeout as e:
                entry["error"] = str(e)
        entry.update(ok=False, attempts=2)
        raise DataGap(f"{name}: {entry['error']}")

    def _mine(self, order_id: str) -> bool:
        return self.sys.owner_of(order_id) == self.cid

    # ---------- reads ----------
    def get_order(self, order_id: str) -> dict | None:
        o = self._call("get_order", lambda: self.sys.get_order(order_id) if self._mine(order_id) else None,
                       {"order_id": order_id})
        return o

    def get_shipment(self, order_id: str) -> dict | None:
        return self._call("get_shipment", lambda: self.sys.get_shipment(order_id) if self._mine(order_id) else None,
                          {"order_id": order_id})

    def check_stock(self, sku: str) -> dict:
        return self._call("check_stock", lambda: self.sys.check_stock(sku), {"sku": sku})

    def get_refunds(self) -> list[dict]:
        return self._call("get_refunds", lambda: self.sys.get_refunds(self.cid), {})


    def get_risk_signals(self, refund_days: int, claim_days: int) -> dict:
        def run():
            r_since = (NOW - timedelta(days=refund_days)).isoformat()
            c_since = (NOW - timedelta(days=claim_days)).isoformat()
            s = self.sys.s
            return {"refunds_90d": s.one("SELECT COUNT(*) AS n FROM refunds WHERE customer_id=? AND created_at>=?",
                                         self.cid, r_since)["n"],
                    "lost_claims_180d": s.one("SELECT COUNT(*) AS n FROM lost_claims WHERE customer_id=? AND "
                                              "created_at>=?", self.cid, c_since)["n"]}
        return self._call("get_risk_signals", run, {})

    # ---------- writes (through the shared action service) ----------
    def act(self, action: Action, rule_id: str) -> dict:
        return self._call(action.name, lambda: self.actions.execute(
            actor="agent", role="agent", action=action, customer_id=self.cid, ticket_id=self.ticket_id,
            rule_id=rule_id), action.model_dump(exclude_none=True))


def _summary(out) -> str:
    if out is None:
        return "not found"
    if isinstance(out, list):
        return f"{len(out)} rows"
    if isinstance(out, dict):
        keys = [k for k in ("order_id", "status", "tracking_no", "refund_id", "amount") if k in out]
        return ", ".join(f"{k}={out[k]}" for k in keys) or ", ".join(list(out)[:4])
    return str(out)[:80]
