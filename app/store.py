"""SQLite store for the synthetic world.

One in-memory database holds customers, orders, refunds, the approval queue and
the audit log. Each eval ticket runs on a fresh copy, so tickets can't affect
each other. The audit log is append-only, enforced by the database itself.
"""
from __future__ import annotations

import sqlite3
import threading

_CLONE_LOCK = threading.Lock()

SCHEMA = """
CREATE TABLE customers(customer_id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT);
CREATE TABLE orders(order_id TEXT PRIMARY KEY, customer_id TEXT, status TEXT, placed_at TEXT,
  delivered_at TEXT, eta TEXT, address TEXT, postal_code TEXT, payment_method TEXT);
CREATE TABLE order_lines(order_id TEXT, line_no INT, sku TEXT, name TEXT, size TEXT, paid_price REAL,
  final_sale INT, exchanges_used INT, PRIMARY KEY(order_id, line_no));
CREATE TABLE shipments(order_id TEXT PRIMARY KEY, carrier TEXT, tracking_no TEXT, status TEXT, eta TEXT,
  courier_note TEXT);
CREATE TABLE stock(sku TEXT, size TEXT, qty INT, PRIMARY KEY(sku, size));
CREATE TABLE refunds(refund_id TEXT PRIMARY KEY, customer_id TEXT, order_id TEXT, line_no INT, amount REAL,
  status TEXT, destination TEXT, created_at TEXT, settled_at TEXT);
CREATE TABLE lost_claims(customer_id TEXT, order_id TEXT, created_at TEXT);
CREATE TABLE side_effects(kind TEXT, order_id TEXT, detail TEXT);
CREATE TABLE approvals(approval_id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT, customer_id TEXT,
  action TEXT, amount REAL, risk_score REAL, rule_id TEXT, summary TEXT, status TEXT DEFAULT 'pending',
  decided_by TEXT, decided_role TEXT, note TEXT);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, role TEXT, action TEXT,
  target TEXT, before TEXT, after TEXT, rule_id TEXT, ticket_id TEXT, idem_key TEXT, outcome TEXT);
CREATE TABLE idempotency(idem_key TEXT PRIMARY KEY, result TEXT);
CREATE TABLE outbox(ticket_id TEXT, thread TEXT, customer_id TEXT, body TEXT);
CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
"""


class Store:
    def __init__(self, conn: sqlite3.Connection):
        conn.row_factory = sqlite3.Row
        self.db = conn

    @classmethod
    def empty(cls) -> "Store":
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.executescript(SCHEMA)
        return cls(conn)

    def clone(self) -> "Store":
        copy = sqlite3.connect(":memory:", check_same_thread=False)
        with _CLONE_LOCK:  # the eval runs the baseline in a second thread, and both copy the same seed
            self.db.backup(copy)
        return Store(copy)

    def q(self, sql: str, *args) -> list[dict]:
        return [dict(r) for r in self.db.execute(sql, args)]

    def one(self, sql: str, *args) -> dict | None:
        rows = self.q(sql, *args)
        return rows[0] if rows else None

    def x(self, sql: str, *args) -> int:
        cur = self.db.execute(sql, args)
        self.db.commit()
        return cur.lastrowid

    def order_view(self, order_id: str) -> dict | None:
        """The full order as the policy sees it: lines, totals and what was already refunded."""
        o = self.one("SELECT * FROM orders WHERE order_id=?", order_id)
        if not o:
            return None
        lines = self.q("SELECT * FROM order_lines WHERE order_id=? ORDER BY line_no", order_id)
        refunds = self.q("SELECT line_no, amount FROM refunds WHERE order_id=?", order_id)
        for line in lines:
            line["final_sale"] = bool(line["final_sale"])
            line["refunded"] = round(sum(r["amount"] for r in refunds if r["line_no"] == line["line_no"]), 2)
        o["lines"] = lines
        o["total"] = round(sum(line["paid_price"] for line in lines), 2)
        o["refunded_total"] = round(sum(r["amount"] for r in refunds), 2)
        return o
