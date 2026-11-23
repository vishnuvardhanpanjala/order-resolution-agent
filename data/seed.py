"""Builds the synthetic world into SQLite, and writes data/tickets.jsonl.

Run: python -m data.seed
Deterministic: same seed, same data, every time.
"""
from __future__ import annotations

import json
import random
from datetime import timedelta
from pathlib import Path

from app.store import Store
from app.types import NOW
from data import scenarios as S

ROOT = Path(__file__).resolve().parent
FIRST = ["Wei Jie", "Siti", "Arjun", "Mei Ling", "Daniel", "Nur Aisyah", "Ravi", "Hui Min", "Marcus", "Priya",
         "Jun Hao", "Farah", "Kelvin", "Shalini", "Bryan", "Xin Yi", "Hafiz", "Grace", "Vikram", "Li Ting"]
LAST = ["Lim", "Tan", "Ng", "Wong", "Koh", "Rahman", "Kumar", "Chua", "Ong", "Pillai", "Goh", "Ismail", "Teo",
        "Nair", "Chen", "Yeo"]
STREETS = ["Ang Mo Kio Ave 3", "Bedok North Rd", "Clementi Ave 2", "Tampines St 21", "Yishun Ring Rd",
           "Bukit Batok West Ave 6", "Toa Payoh Lor 1", "Pasir Ris Dr 3", "Sengkang East Way", "Hougang Ave 8"]
N_CUSTOMERS = 60


def _customers(rng):
    rows = []
    for i in range(1, N_CUSTOMERS + 1):
        if i == 1:
            name, email = "Tan Wei Ling", "weiling.tan@mail.example"
        else:
            first, last = rng.choice(FIRST), rng.choice(LAST)
            name = f"{last} {first}" if rng.random() < 0.5 else f"{first} {last}"
            email = f"{first.lower().replace(' ', '')}.{last.lower()}{i}@mail.example"
        rows.append((f"C{i:03d}", name, email, f"+65 8{i:03d} {5000 + i}"))
    return rows


def _filler_orders(rng):
    # Older delivered orders so customers have history. All more than 90 days old, no refunds,
    # so they never change a scenario's outcome.
    out = []
    for k in range(20):
        cid = f"C{(k * 3) % 40 + 2:03d}"
        sku = rng.choice(sorted(S.PRODUCTS))
        out.append(dict(oid=f"O{1086 + k}", cid=cid, status="delivered", days=90 + (k * 13) % 210, hours=None,
                        lines=[S.L(sku, "30" if sku == "PW-KID" else str(rng.randint(37, 44)))], note=None))
    return out


def build_store() -> Store:
    rng = random.Random(7)
    s = Store.empty()
    customers = _customers(rng)
    s.db.executemany("INSERT INTO customers VALUES (?,?,?,?)", customers)
    addr = {c[0]: (f"{10 + i} {STREETS[i % len(STREETS)]}, #0{i % 9 + 1}-{10 + i}, Singapore",
                   f"{(i * 7919) % 800000 + 100000:06d}", f"card ending {(i * 1373) % 9000 + 1000}")
            for i, c in enumerate(customers)}
    for o in S.ORDERS + _filler_orders(rng):
        address, postal, pay = addr[o["cid"]]
        if o["status"] == "delivered":
            delivered = NOW - (timedelta(hours=o["hours"]) if o["hours"] else timedelta(days=o["days"]))
            placed, eta = delivered - timedelta(days=4), delivered.date()
        else:
            delivered, placed = None, NOW - timedelta(days=o["days"])
            eta = (NOW + timedelta(days=2 if o["status"] == "shipped" else 4)).date()
        s.db.execute("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)",
                     (o["oid"], o["cid"], o["status"], placed.isoformat(),
                      delivered.isoformat() if delivered else None, eta.isoformat(), address, postal, pay))
        for n, line in enumerate(o["lines"], 1):
            s.db.execute("INSERT INTO order_lines VALUES (?,?,?,?,?,?,?,?)",
                         (o["oid"], n, line["sku"], S.PRODUCTS[line["sku"]][0], line["size"], line["price"],
                          int(line["final"]), line["ex"]))
        if o["status"] in ("shipped", "delivered"):
            note = o["note"] or ("Left at front door." if delivered else "Parcel scanned at sorting hub.")
            s.db.execute("INSERT INTO shipments VALUES (?,?,?,?,?,?)",
                         (o["oid"], "LionPost", f"SG{o['oid'][1:]}0001",
                          "delivered" if delivered else "in_transit", eta.isoformat(), note))
    sizes = {sku: [str(x) for x in (range(28, 35) if sku == "PW-KID" else range(36, 46))] for sku in S.PRODUCTS}
    for sku, size_list in sizes.items():
        for size in size_list:
            s.db.execute("INSERT INTO stock VALUES (?,?,?)", (sku, size, 0 if (sku, size) in S.OUT_OF_STOCK else 5))
    for n, (cid, oid, line_no, amount, status, created_ago, settled_ago) in enumerate(S.REFUNDS, 1):
        pay = addr[cid][2]
        s.db.execute("INSERT INTO refunds VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"RF{n:04d}", cid, oid, line_no, amount, status, pay,
                      (NOW - timedelta(days=created_ago)).isoformat(),
                      (NOW - timedelta(days=settled_ago)).isoformat() if settled_ago else None))
    for cid, oid, ago in S.LOST_CLAIMS:
        s.db.execute("INSERT INTO lost_claims VALUES (?,?,?)", (cid, oid, (NOW - timedelta(days=ago)).isoformat()))
    s.db.commit()
    return s


_BASE: Store | None = None


def seeded_store() -> Store:
    """A fresh copy of the seeded world. Cheap: the seed is built once, then copied."""
    global _BASE
    if _BASE is None:
        _BASE = build_store()
    return _BASE.clone()


def build_tickets(store: Store) -> list[dict]:
    emails = {r["customer_id"]: r["email"] for r in store.q("SELECT customer_id, email FROM customers")}
    out = []
    for t in S.TICKETS:
        email = emails[t["cid"]] if t["from_email"] == "__own__" else t["from_email"]
        out.append({
            "ticket_id": t["tid"], "channel": t["channel"],
            "authenticated_customer_id": t["cid"] if t["channel"] == "web_chat" else None,
            "from_email": email if t["channel"] == "email" else None,
            "messages": [{"role": "customer", "text": t["text"]}],
            "attachments": [{"type": "image", "name": "photo_1.jpg"}] if t["photo"] else [],
            "expected": {"intent": t["intent"], "order_id": t["order"], "disposition": t["disp"],
                         "actions": t["actions"], "approval_actions": t["approvals"], "reason_code": t["reason"],
                         "refund_amount": t["refund"], "must_mention": t["mention"], "must_not_mention": t["avoid"]},
            "tags": t["tags"] + (["hero"] if t["tid"] in S.HERO else []),
        })
    return out


def load_tickets(path: Path = ROOT / "tickets.jsonl") -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main():
    store = build_store()
    tickets = build_tickets(store)
    (ROOT / "tickets.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tickets))
    n = {k: store.one(f"SELECT COUNT(*) AS n FROM {k}")["n"] for k in ("customers", "orders", "refunds")}
    tags = {g: sum(g in t["tags"] for t in tickets) for g in ("normal", "edge", "adversarial", "hero")}
    print(f"wrote {len(tickets)} tickets {tags}; store: {n}")


if __name__ == "__main__":
    main()
