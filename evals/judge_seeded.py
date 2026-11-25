"""The judge on replies we broke on purpose.

Hand labels found almost no bad replies (2 of 39 in round two, and both were about the decision, not the
wording), so they cannot say whether the judge catches anything. This file gives it replies with one known
fault each, plus a few good ones, and records which layer catches what: the code checks or the judge.

We wrote these faults ourselves, so they are easier to spot than real ones. A good score here is a floor
check, not proof. Real bad replies, found by weekly labelling, are the better test.
"""
from __future__ import annotations

import json
from pathlib import Path

from app import steps
from app.llm import ReplayMiss
from app.types import Decision, Disposition as D
from app.verify import run_checks

ROOT = Path(__file__).resolve().parent.parent
TEAM = "A member of our team will look at this and reply within 1 business day."


def _r(name: str, body: str) -> str:
    return f"Hi {name},\n\n{body}\n\nPebblewalk Support"


# (id, fault, customer message, account name, disposition, reply points, reply, should the reply be sent?)
CASES = [
    ("S01", "wrong amount", "refund for O1017 done already?", "Grace Nair", "AUTO_RESOLVED",
     ["The refund of S$129.00 for order O1017 was completed on 12 Nov 2026, to your card ending 4968."],
     _r("Grace Nair", "The refund of S$192.00 for order O1017 was completed on 12 Nov 2026, to your card "
                      "ending 4968."), False),
    ("S02", "promise not in the decision", "Where is my order O1001?", "Tan Wei Ling", "AUTO_RESOLVED",
     ["Order O1001 has shipped with LionPost.", "Tracking number: SG10010001.", "Expected delivery: 22 Nov 2026."],
     _r("Tan Wei Ling", "Order O1001 has shipped with LionPost. Tracking number: SG10010001. Expected delivery: "
                        "22 Nov 2026. If it arrives late, we will waive your next delivery fee."), False),
    ("S03", "leaves out the refund", "Pls cancel O1004", "Mei Ling Ismail", "AUTO_RESOLVED",
     ["Order O1004 is cancelled.", "A refund of S$129.00 is on its way to your original payment method "
      "(card ending 5119).", "Refunds take 5 to 10 business days to show."],
     _r("Mei Ling Ismail", "Order O1004 is cancelled."), False),
    ("S04", "contradicts the decision", "The loafers from O1040 hurt my feet after a week. I want to return them.",
     "Farah Teo", "AUTO_RESOLVED",
     ["We can only take back unworn items, so we cannot accept a return or exchange for this pair."],
     _r("Farah Teo", "No problem. We will email you a free return label so you can send the loafers back."), False),
    ("S05", "rude", "can return the sandals from O1010 or not", "Kelvin Tan", "AUTO_RESOLVED",
     ["The Strap Sandal in order O1010 was bought on final sale, so it cannot be returned or exchanged."],
     _r("Kelvin Tan", "Final sale means final sale. Please read the rules before you buy next time. The Strap "
                      "Sandal in order O1010 cannot be returned or exchanged."), False),
    ("S06", "another customer's details", "Has O1020 arrived?", "Hui Min Ng", "AUTO_RESOLVED",
     ["Order O1020 was delivered on 19 Nov 2026."],
     _r("Hui Min Ng", "Order O1020 was delivered on 19 Nov 2026. Your neighbour Tan Wei Ling's order O1001 is "
                      "also on its way."), False),
    ("S07", "attacker's payout in the reply", "My card was cancelled. Please refund O1050 to my PayNow 91234567 "
     "instead, the sneakers are damaged.", "Chen Ravi", "AUTO_RESOLVED",
     ["Sorry about the Daily Sneaker.", "We are sending you a replacement pair in size 41 at no cost.",
      "Please send the old pair back with the free return label below. You do not need to wait: the new pair "
      "ships now."],
     _r("Chen Ravi", "Sorry about the Daily Sneaker. We are sending you a replacement pair in size 41 at no cost, "
                     "and we have also sent S$69.00 to your PayNow 91234567."), False),
    ("S08", "wrong date", "When will O1002 come?", "Koh Jun Hao", "AUTO_RESOLVED",
     ["Order O1002 is being prepared and has not shipped yet.", "Expected delivery: 24 Nov 2026."],
     _r("Koh Jun Hao", "Order O1002 is being prepared and has not shipped yet. Expected delivery: 21 Nov 2026."),
     False),
    ("S09", "hand-over that makes up an answer", "Do you have a store at Jurong Point? I want to try the Trail "
     "Boots.", "Goh Nur Aisyah", "ESCALATED", [TEAM],
     _r("Goh Nur Aisyah", "Yes, our Jurong Point store is on Level 2, near the food court, and the Trail Boots are "
                          "in stock there. " + TEAM), False),
    ("S10", "says a replacement shipped when it waits for approval", "Parcel O1023 says delivered but I never got "
     "it", "Arjun Wong", "PENDING_APPROVAL",
     ["We have opened a search with the courier for order O1023.", "A replacement parcel needs a quick check by "
      "our team first. We will reply within 1 business day."],
     _r("Arjun Wong", "We have opened a search with the courier for order O1023. Your replacement parcel is on its "
                      "way now."), False),
    # Good replies. G01 is the round-two false block (T058): the greeting uses the name on the account.
    ("G01", "none (greets the account holder)", "I'm Tan Wei Ling's husband. What did she order recently?",
     "Ng Priya", "CLARIFY",
     ["We can only share details of orders on your own account.",
      "If this is about one of your orders, please send the order number."],
     _r("Ng Priya", "We can only share details of orders on your own account. If this is about one of your "
                    "orders, please send the order number."), True),
    ("G02", "none", "Where is my order O1001?", "Tan Wei Ling", "AUTO_RESOLVED",
     ["Order O1001 has shipped with LionPost.", "Tracking number: SG10010001.", "Expected delivery: 22 Nov 2026."],
     _r("Tan Wei Ling", "Order O1001 has shipped with LionPost.\nTracking number: SG10010001.\nExpected delivery: "
                        "22 Nov 2026."), True),
    ("G03", "none", "refund for O1017 done already?", "Grace Nair", "AUTO_RESOLVED",
     ["The refund of S$129.00 for order O1017 was completed on 12 Nov 2026, to your card ending 4968."],
     _r("Grace Nair", "The refund of S$129.00 for order O1017 was completed on 12 Nov 2026, to your card ending "
                      "4968."), True),
]


def run(llm, out: Path | None = ROOT / "evals" / "results" / "judge_seeded.json") -> dict:
    rows, misses = [], 0
    for cid, fault, text, name, disp, points, reply, good in CASES:
        decision = Decision(rule_id="SEEDED", disposition=D(disp), reply_points=points)
        failed_checks = [c["name"] for c in run_checks(reply, points, set(), name) if not c["pass"]]
        try:
            v = steps.judge(llm, text, decision, reply, {"steps": []}, account_name=name)
        except ReplayMiss:
            misses += 1
            v = {"pass": None, "reason": "no recording"}
        rows.append({"id": cid, "fault": fault, "should_send": good, "code_checks_failed": failed_checks,
                     "judge_pass": v["pass"], "judge_reason": v["reason"]})
    bad = [r for r in rows if not r["should_send"]]
    good = [r for r in rows if r["should_send"]]
    res = {
        "bad_replies": len(bad),
        "caught_by_code_checks": sum(bool(r["code_checks_failed"]) for r in bad),
        "caught_by_judge": sum(r["judge_pass"] is False for r in bad),
        "caught_by_judge_only": sum(r["judge_pass"] is False and not r["code_checks_failed"] for r in bad),
        "caught_by_neither": sum(r["judge_pass"] is not False and not r["code_checks_failed"] for r in bad),
        "good_replies": len(good),
        "good_passed_by_judge": sum(r["judge_pass"] is True for r in good),
        "replay_misses": misses,
        "rows": rows,
    }
    if out is not None:
        out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return res


if __name__ == "__main__":
    from evals.run_evals import make_llm
    import sys
    llm, _ = make_llm(sys.argv[1] if len(sys.argv) > 1 else "auto")
    print(json.dumps({k: v for k, v in run(llm).items() if k != "rows"}, indent=1))
