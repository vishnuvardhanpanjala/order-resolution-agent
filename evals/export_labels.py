"""Exports live-run traces for hand labelling: evals/labels/to_label.csv. Read HOW_TO_LABEL.md first.

Picks every trace an automatic check flagged, then the tickets on the last sheet, then random others, up to 40
in all (or N: `make labels N=60`), in shuffled order. The labeller does not see the expected answer or the judge's verdict, so neither can bias
the label.

The sheet spells every fact out in words. The first version printed "placed, delivered -", which reads as
"delivered", and 8 of 16 "wrong outcome" labels in the first round came from that and similar gaps (failure log, F1).

After a new live run, the previous sheet is kept as to_label_run<k>.csv. Labels carry over only for a
conversation that came out exactly the same (same rule, actions and reply). Everything else is blank again.
Coming from the round-one sheet, only "OK" outcome labels carry and reply_ok starts blank, because that sheet
misled and reply_ok's meaning was narrowed after it (decisions #41, #43). From round two on, both labels carry.
"""
from __future__ import annotations

import csv
import json
import random
from pathlib import Path

from app.policy import day
from app.types import NOW
from data.seed import load_tickets, seeded_store

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "evals" / "labels" / "to_label.csv"
COLS = ["trace_id", "ticket_id", "conversation", "facts_the_system_saw", "what_the_system_did", "actions", "reply",
        "ok_overall", "reply_ok", "first_error_note", "carried_from_last_run"]


def facts(store, tr, ticket) -> str:
    """Everything the system knew, in plain words."""
    lines = [f"Today in this test: {NOW:%a} {day(NOW)}, 10:00 Singapore time"]
    cid = tr.get("customer_id")
    lines.append(f"Customer: logged in as {cid}" if tr.get("identity") == "session" else
                 f"Customer: identified by order number + email ({cid})" if cid else
                 "Customer: NOT identified (not logged in, no matching order number + email)")
    e = tr.get("extraction") or {}
    oid = e.get("order_id")
    o = store.order_view(oid) if oid else None
    if not oid:
        lines.append("Order: none. The customer gave no order number, so the system did not look one up.")
    elif not o or o["customer_id"] != cid:
        lines.append(f"Order: {oid} was given, but it is not on this customer's account. Nothing was shown.")
    else:
        how = "found in the message by code (the model missed it)" if tr.get("order_from_text") else "named by the customer"
        status = {"placed": "placed, not shipped yet", "packed": "packed, not shipped yet",
                  "shipped": "shipped, not delivered yet", "cancelled": "cancelled"}.get(o["status"])
        if o["status"] == "delivered":
            status = f"delivered on {day(o['delivered_at'])} ({(NOW - _dt(o['delivered_at'])).days} days ago)"
        lines.append(f"Order: {oid}, {how}. Status: {status}.")
        lines.append("Items: " + "; ".join(
            f"{l['name']} size {l['size']}, paid S${l['paid_price']:.2f}" + (", FINAL SALE" if l["final_sale"] else "")
            + (", already exchanged once" if l["exchanges_used"] else "") for l in o["lines"]))
    if cid:
        r90 = store.one("SELECT COUNT(*) AS n FROM refunds WHERE customer_id=? AND created_at>=?", cid,
                        _ago(90))["n"]
        lost = store.one("SELECT COUNT(*) AS n FROM lost_claims WHERE customer_id=? AND created_at>=?", cid,
                         _ago(180))["n"]
        lines.append(f"Refunds in the last 90 days: {r90}. Lost-parcel claims in the last 180 days: {lost}.")
    lines.append("Photo attached: " + ("yes" if ticket.get("attachments") else "no"))
    return "\n".join(lines)


def _dt(x):
    from datetime import datetime
    return datetime.fromisoformat(str(x))


def _ago(days: int) -> str:
    from datetime import timedelta
    return (NOW - timedelta(days=days)).isoformat()


def what_it_did(tr) -> str:
    e = tr.get("extraction") or {}
    d = tr.get("decision") or {}
    out = [f"Read as {e.get('intent', 'unreadable')} (confidence {e.get('confidence', '-')}).",
           f"Rule {tr['rule_id']}: {tr['disposition']}" + (f" ({tr['reason_code']})" if tr["reason_code"] else "") + "."]
    if d.get("note"):
        out.append(f"Why: {d['note']}.")
    if tr["rule_id"].startswith("G4"):
        out.append("Every read is tried twice, so the system was still down on the second try.")
    out += [f"Quiet alert to the risk team: {a.get('handled')}" for a in tr["security_alerts"]]
    if tr["reply_held"]:
        out.append("The draft failed its checks, so a fixed safe reply was sent.")
    return " ".join(out)


def actions_text(tr) -> str:
    return ", ".join(f"{a['name']} {a.get('amount') or ''} ({a['status']})".replace(" ()", "")
                     for a in tr["actions"]) or "none"


def conversation(ticket) -> str:
    msgs = ticket["messages"]
    if len(msgs) == 1:
        return msgs[0]["text"]
    return "\n".join(("Customer: " if m["role"] == "customer" else "Pebblewalk: ") + m["text"] for m in msgs)


def _same(row: dict, tr: dict) -> bool:
    norm = lambda s: " ".join(str(s).split())  # noqa: E731
    return (norm(row.get("reply")) == norm(tr["reply"]) and norm(row.get("actions")) == norm(actions_text(tr))
            and f"rule {tr['rule_id']}".lower() in norm(row.get("what_the_system_did")).lower())


def carry_labels(old: list[dict], stale: list[dict]) -> dict[str, dict]:
    """Labels from the last sheet that still apply: only rows where nothing the labeller saw has changed.

    Round two carried only "OK" labels and blanked reply_ok, because the round-one sheet misled and reply_ok's
    meaning changed after it. A sheet with the fixed facts column carries both labels as they are."""
    fixed_sheet = bool(old) and all("Today in this test" in r.get("facts_the_system_saw", "") for r in old)
    carried = {}
    for r in old:
        if r in stale or not r.get("ok_overall", "").strip():
            continue
        if not fixed_sheet:
            if r["ok_overall"].strip().lower() != "y":
                continue
            r = dict(r, reply_ok="")
        carried[r["ticket_id"]] = r
    return carried


def choose(traces: list[dict], flagged: list[dict], keep_ids: set[str], n: int, seed: int = 1) -> list[dict]:
    """Every flagged trace, then the tickets on the last sheet, then random others, up to n, shuffled.

    Re-using last round's tickets means unchanged rows keep their labels and the two rounds compare on the
    same tickets. A flagged or re-used ticket is never dropped, so the sheet can run a little over n."""
    rng = random.Random(seed)
    first = flagged + [t for t in traces if t["ticket_id"] in keep_ids and t not in flagged]
    rest = [t for t in traces if t not in first]
    rng.shuffle(rest)
    pick = first + rest[:max(0, n - len(first))]
    rng.shuffle(pick)
    return pick


def main(n: int = 40):
    traces = [json.loads(l) for l in (ROOT / "traces" / "recorded-ours.jsonl").read_text(encoding="utf-8").splitlines() if l]
    by_id = {t["ticket_id"]: t for t in traces}
    rows = {r["ticket_id"]: r for r in json.loads((ROOT / "evals" / "results" / "recorded.json").read_text(encoding="utf-8"))["rows"]["ours"]}
    flagged = [t for t in traces if not (rows[t["ticket_id"]]["disp_ok"] and rows[t["ticket_id"]]["actions_ok"]
                                         and rows[t["ticket_id"]]["mention_ok"]) or t["reply_held"]
               or (t.get("judge") or {}).get("pass") is False]
    old = list(csv.DictReader(OUT.open(encoding="utf-8-sig", newline=""))) if OUT.exists() else []
    stale = [r for r in old if r["ticket_id"] not in by_id or not _same(r, by_id[r["ticket_id"]])]
    pick = choose(traces, flagged, {r["ticket_id"] for r in old}, n)
    carried = {}
    if stale:  # a sheet from an earlier run: keep it, start a fresh one, carry labels only where nothing changed
        k = 1
        while (OUT.parent / f"to_label_run{k}.csv").exists():
            k += 1
        OUT.rename(OUT.parent / f"to_label_run{k}.csv")
        carried = carry_labels(old, stale)
        print(f"previous sheet kept as to_label_run{k}.csv; {len(carried)} unchanged conversations keep their labels")
        old = []
    have = {r["ticket_id"] for r in old}
    pick = [tr for tr in pick if tr["ticket_id"] not in have][:max(0, n - len(old))]
    store, tickets = seeded_store(), {t["ticket_id"]: t for t in load_tickets()}
    OUT.parent.mkdir(exist_ok=True)
    # utf-8-sig writes the marker Excel needs to show non-English text, and strips it again when reading.
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in COLS} for r in old)
        for tr in pick:
            t = tickets[tr["ticket_id"]]
            prev = carried.get(tr["ticket_id"], {})
            w.writerow({"trace_id": f"{tr['run_id']}:{tr['ticket_id']}", "ticket_id": tr["ticket_id"],
                        "conversation": conversation(t), "facts_the_system_saw": facts(store, tr, t),
                        "what_the_system_did": what_it_did(tr), "actions": actions_text(tr), "reply": tr["reply"],
                        "ok_overall": prev.get("ok_overall", ""), "reply_ok": prev.get("reply_ok", ""),
                        "first_error_note": prev.get("first_error_note", ""),
                        "carried_from_last_run": "yes" if prev else ""})
    print(f"kept {len(old)} rows, added {len(pick)} traces ({len(flagged)} flagged by checks in all) "
          f"to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
