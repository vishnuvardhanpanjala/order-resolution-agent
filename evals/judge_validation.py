"""Compares the LLM judge with hand labels (reply_ok column), as a confusion matrix.

Raw agreement hides the failure that matters. If 1 reply in 10 is bad, a judge that passes everything
"agrees" with humans 90% of the time and catches nothing. So we report two rates, plus the base rate:
  catches bad replies: of replies humans call not OK, how many the judge fails
  passes good replies: of replies humans call OK, how many the judge passes
  base rate: how many replies humans call not OK at all
Held replies are left out: the customer got a fixed template, not the draft the judge saw.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN_BAD = 5  # fewer bad replies than this and the catch rate is mostly noise


def _rate(hit: int, total: int, empty: str) -> str:
    return f"{hit}/{total} ({hit / total:.0%})" if total else f"0/0 ({empty})"


def main():
    labels = list(csv.DictReader((ROOT / "evals" / "labels" / "to_label.csv").open()))
    traces = {json.loads(l)["ticket_id"]: json.loads(l)
              for l in (ROOT / "traces" / "recorded-ours.jsonl").read_text().splitlines() if l}
    tp = fp = fn = tn = 0  # "positive" here means the judge passes the reply
    for row in labels:
        human = row["reply_ok"].strip().lower()[:1]
        tr = traces.get(row["ticket_id"])
        if human not in ("y", "n") or not tr or tr["reply_held"] or not tr.get("judge"):
            continue
        judge = tr["judge"]["pass"]
        if judge is None:
            continue
        tp += judge and human == "y"
        fp += judge and human == "n"
        fn += (not judge) and human == "y"
        tn += (not judge) and human == "n"
    n, bad = tp + fp + fn + tn, tn + fp
    res = {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "base_rate": _rate(bad, n, "nothing labelled"),
           "catches_bad": _rate(tn, bad, "no bad replies labelled"),
           "passes_good": _rate(tp, tp + fn, "no good replies labelled"),
           "warning": (f"Only {bad} bad replies labelled. The catch rate can't be trusted below {MIN_BAD}."
                       if bad < MIN_BAD else "")}
    (ROOT / "evals" / "results" / "judge_validation.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
