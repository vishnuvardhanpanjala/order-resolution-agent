"""Compares the LLM judge with hand labels (reply_ok column), as a confusion matrix.

Raw agreement hides the failure that matters: a judge that passes everything agrees with humans
most of the time and is useless. So we report two rates instead:
  true-pass rate: of replies humans call OK, how many the judge passes
  true-fail rate: of replies humans call not OK, how many the judge fails
Held replies are left out: the customer got a fixed template, not the draft the judge saw.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    labels = list(csv.DictReader((ROOT / "evals" / "labels" / "to_label.csv").open()))
    traces = {json.loads(l)["ticket_id"]: json.loads(l)
              for l in (ROOT / "traces" / "recorded-ours.jsonl").read_text().splitlines() if l}
    tp = fp = fn = tn = 0
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
    n = tp + fp + fn + tn
    res = {"n": n, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
           "tpr": f"{tp}/{tp + fn}" + (f" ({tp / (tp + fn):.0%})" if tp + fn else ""),
           "tnr": f"{tn}/{tn + fp}" + (f" ({tn / (tn + fp):.0%})" if tn + fp else " (no 'not OK' replies labelled)")}
    (ROOT / "evals" / "results" / "judge_validation.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
