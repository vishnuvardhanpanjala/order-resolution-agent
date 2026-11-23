"""Stand-ins for the outside systems: order system (OMS), courier and payments.

In production each is an API owned by another team. Here they read and write the
SQLite store. Two orders are wired to fail, so we can test what happens when a
system is down.
"""
from __future__ import annotations

import json
from datetime import timedelta

from app.store import Store
from app.types import NOW

ALWAYS_FAIL = {"O1045"}  # OMS times out every time: tests the DATA_GAP path
FAIL_ONCE = {"O1026"}    # OMS times out once, then works: tests the retry


class SystemTimeout(Exception):
    pass


class MockSystems:
    def __init__(self, store: Store):
        self.s = store
        self._failed_once: set[str] = set()

    def _oms(self, order_id: str):
        if order_id in ALWAYS_FAIL:
            raise SystemTimeout(f"OMS timeout on {order_id}")
        if order_id in FAIL_ONCE and order_id not in self._failed_once:
            self._failed_once.add(order_id)
            raise SystemTimeout(f"OMS timeout on {order_id} (first try)")

    def _effect(self, kind: str, order_id: str, **detail) -> None:
        self.s.x("INSERT INTO side_effects VALUES (?,?,?)", kind, order_id, json.dumps(detail))

    # ---------- order system ----------
    def get_order(self, order_id: str) -> dict | None:
        self._oms(order_id)
        return self.s.order_view(order_id)

    def owner_of(self, order_id: str) -> str | None:
        row = self.s.one("SELECT customer_id FROM orders WHERE order_id=?", order_id)
        return row["customer_id"] if row else None

    def orders_since(self, customer_id: str, days: int) -> list[str]:
        since = (NOW - timedelta(days=days)).isoformat()
        rows = self.s.q("SELECT order_id FROM orders WHERE customer_id=? AND placed_at>=? ORDER BY placed_at DESC",
                        customer_id, since)
        return [r["order_id"] for r in rows]

    def check_stock(self, sku: str) -> dict[str, int]:
        return {r["size"]: r["qty"] for r in self.s.q("SELECT size, qty FROM stock WHERE sku=?", sku)}

    def cancel_order(self, order_id: str) -> dict:
        self._oms(order_id)
        self.s.x("UPDATE orders SET status='cancelled' WHERE order_id=?", order_id)
        return {"status": "cancelled"}

    def update_address(self, order_id: str, address: str, postal_code: str) -> dict:
        self._oms(order_id)
        self.s.x("UPDATE orders SET address=?, postal_code=? WHERE order_id=?", address, postal_code, order_id)
        return {"address": address, "postal_code": postal_code}

    def _line_sku(self, order_id: str, line_no: int) -> str:
        return self.s.one("SELECT sku FROM order_lines WHERE order_id=? AND line_no=?", order_id, line_no)["sku"]

    def create_exchange(self, order_id: str, line_no: int, new_size: str) -> dict:
        self._oms(order_id)
        sku = self._line_sku(order_id, line_no)
        self.s.x("UPDATE stock SET qty=qty-1 WHERE sku=? AND size=? AND qty>0", sku, new_size)
        self.s.x("UPDATE order_lines SET exchanges_used=exchanges_used+1 WHERE order_id=? AND line_no=?",
                 order_id, line_no)
        ref = f"EX-{order_id}-{line_no}"
        self._effect("exchange", order_id, ref=ref, new_size=new_size)
        return {"exchange_id": ref, "return_label": f"https://pebblewalk.example/returns/{ref}"}

    def create_return_label(self, order_id: str, line_no: int) -> dict:
        ref = f"RL-{order_id}-{line_no}"
        self._effect("return_label", order_id, ref=ref)
        return {"return_label": f"https://pebblewalk.example/returns/{ref}"}

    def create_replacement(self, order_id: str, line_no: int, size: str) -> dict:
        self._oms(order_id)
        sku = self._line_sku(order_id, line_no)
        self.s.x("UPDATE stock SET qty=qty-1 WHERE sku=? AND size=? AND qty>0", sku, size)
        ref = f"RP-{order_id}-{line_no}"
        self._effect("replacement", order_id, ref=ref, size=size)
        return {"replacement_id": ref}

    def create_reship(self, order_id: str) -> dict:
        self._oms(order_id)
        self._effect("reship", order_id, ref=f"RS-{order_id}")
        return {"reship_id": f"RS-{order_id}"}

    # ---------- courier ----------
    def get_shipment(self, order_id: str) -> dict | None:
        return self.s.one("SELECT * FROM shipments WHERE order_id=?", order_id)

    def open_carrier_investigation(self, order_id: str, customer_id: str) -> dict:
        self.s.x("INSERT INTO lost_claims VALUES (?,?,?)", customer_id, order_id, NOW.isoformat())
        return {"case_id": f"CI-{order_id}"}

    # ---------- payments ----------
    def get_refunds(self, customer_id: str) -> list[dict]:
        return self.s.q("SELECT * FROM refunds WHERE customer_id=? ORDER BY created_at DESC", customer_id)

    def issue_refund(self, customer_id: str, order_id: str, line_no: int | None, amount: float,
                     destination: str) -> dict:
        n = self.s.one("SELECT COUNT(*) AS n FROM refunds")["n"] + 1
        rid = f"RF{n:04d}"
        self.s.x("INSERT INTO refunds VALUES (?,?,?,?,?,?,?,?,?)", rid, customer_id, order_id, line_no, amount,
                 "pending", destination, NOW.isoformat(), None)
        return {"refund_id": rid, "amount": amount, "destination": destination}
