"""Exports live-run traces for hand labelling: evals/labels/to_label.csv. Read HOW_TO_LABEL.md first.

Picks every trace an automatic check flagged, then random others, up to 40 in all (or N: `make labels N=60`),
in shuffled order.
The labeller does not see the expected answer or the judge's verdict, so neither can bias the label.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from data.seed import load_tickets, seeded_store

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evals" / "labels" / "to_label.csv"
COLS = ["trace_id", "ticket_id", "customer_message", "order_facts", "what_the_system_did", "actions", "reply",
        "ok_overall", "reply_ok", "first_error_note"]


def order_facts(store, tr) -> str:
    oid = (tr.get("extraction") or {}).get("order_id") or tr.get("order_inferred")
    o = store.order_view(oid) if oid else None
    if not o or o["customer_id"] != tr.get("customer_id"):
        return f"verified: {tr.get('identity')}. order: {oid or 'none given'} (not on this account or not given)"
    items = "; ".join(f"{l['name']} size {l['size']} S${l['paid_price']:.2f}{' FINAL SALE' if l['final_sale'] else ''}"
                      f"{' exchanged once' if l['exchanges_used'] else ''}" for l in o["lines"])
    refunds = store.one("SELECT COUNT(*) AS n FROM refunds WHERE customer_id=?", o["customer_id"])["n"]
    return (f"{oid}: {o['status']}, delivered {str(o['delivered_at'])[:16] if o['delivered_at'] else '-'}, {items}. "
            f"Customer refunds on record: {refunds}. Photo attached: {'yes' if tr.get('photo') else 'see message'}")


def main(n: int = 40):
    traces = [json.loads(l) for l in (ROOT / "traces" / "recorded-ours.jsonl").read_text().splitlines() if l]
    rows = {r["ticket_id"]: r for r in json.loads((ROOT / "evals" / "results" / "recorded.json").read_text())["rows"]["ours"]}
    flagged = [t for t in traces if not (rows[t["ticket_id"]]["disp_ok"] and rows[t["ticket_id"]]["actions_ok"]
                                         and rows[t["ticket_id"]]["mention_ok"]) or t["reply_held"]
               or (t.get("judge") or {}).get("pass") is False]
    rng = random.Random(1)
    rest = [t for t in traces if t not in flagged]
    rng.shuffle(rest)
    pick = flagged + rest[:max(0, n - len(flagged))]
    rng.shuffle(pick)
    # Never lose labels: rows already in the file stay first, as they are. Only new traces are added after them.
    kept = list(csv.DictReader(OUT.open())) if OUT.exists() else []
    have = {r["trace_id"] for r in kept}
    pick = [tr for tr in pick if f"{tr['run_id']}:{tr['ticket_id']}" not in have][:max(0, n - len(kept))]
    store, tickets = seeded_store(), {t["ticket_id"]: t for t in load_tickets()}
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in COLS} for r in kept)
        for tr in pick:
            tr["photo"] = bool(tickets[tr["ticket_id"]].get("attachments"))
            ext = tr.get("extraction") or {}
            w.writerow({"trace_id": f"{tr['run_id']}:{tr['ticket_id']}", "ticket_id": tr["ticket_id"],
                        "customer_message": tickets[tr["ticket_id"]]["messages"][0]["text"],
                        "order_facts": order_facts(store, tr),
                        "what_the_system_did": f"read as {ext.get('intent', 'unreadable')} -> rule {tr['rule_id']} -> "
                                               f"{tr['disposition']} {tr['reason_code'] or ''}".strip(),
                        "actions": ", ".join(f"{a['name']} {a.get('amount') or ''} ({a['status']})".replace(" ()", "")
                                             for a in tr["actions"]) or "none",
                        "reply": tr["reply"], "ok_overall": "", "reply_ok": "", "first_error_note": ""})
    print(f"kept {len(kept)} rows, added {len(pick)} traces ({len(flagged)} flagged by checks in all) "
          f"to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
