# Rollout plan

> Written down, not run. There is no real traffic. This is how the prototype would go live if it were real.

## Stages

Each stage is earned with evidence from the one before. Any stage can be rolled back with the kill switch, without a deploy.

| Stage | What the agent does | Who acts | Moves on when |
|---|---|---|---|
| 1. Shadow | Reads real tickets and decides, but sends nothing | Associates work as today. The agent's decision is compared with theirs. | Its decision matches the associate's on at least 95% of tickets over two weeks, with 0 money actions outside policy |
| 2. Drafts | Drafts every reply and action | An associate approves, edits or rejects each one | Each action type meets the promotion rule below |
| 3. Auto for answers and cancels | Sends replies that move no money, and cancels before dispatch | Associates handle the rest, from drafts | Two weeks with no reopen-rate rise against the holdout |
| 4. Auto for money within the cap | Refunds, replacements and reships up to S$80 within policy | Associates approve everything above the cap | Ongoing, with the monitoring below |

**Holdout.** 10% of eligible tickets stay with people the whole time. They are the comparison for reopen rate, customer satisfaction and cycle time. Without a holdout, "the agent is faster" has nothing to be faster than.

## Promote and demote, per action type

From `config/autonomy.yaml`. An action type moves from "draft, a person approves" to "acts on its own" only if, over at least 50 reviewed drafts:
- people changed the decision 0 times
- people edited the text in 10% of drafts or fewer
- there were 0 policy violations

Any violation moves it back at once. Every human edit is kept as labelled data for the next eval round.

**Kill switch.** Adding a request type to `kill_switch` in `config/autonomy.yaml` sends every action for that type to a person, at once.

## Who owns what

| Thing | Owner | What they decide |
|---|---|---|
| The policy file (caps, windows, rules) | Policy owner (CX lead) | Rule changes, through a reviewed pull request with a test for each change |
| Refunds and replacements above S$80 | CX associate up to S$300, CX lead above | Approve or reject each queued action |
| Payments escalations (late refunds) | Payments team | The refund itself |
| Prompts, models, evals | The product owner for the agent | Changes only with a fresh eval run and the gates green |
| The reply judge | The product owner, with weekly labels | Whether it stays on the send path (decision #48 sets the rule) |
| Risk alerts | Risk or fraud lead | Follow-up on quiet alerts (someone asking about another person's orders, delivery abroad) |

## Monitoring once live

- **Daily:** a sample of traces runs through the code checks and the judge. Anything flagged goes to a person.
- **Weekly:** someone labels 40 to 60 traces with the same sheet and method as the eval rounds (`evals/labels/HOW_TO_LABEL.md`), groups what went wrong, and fixes it at the layer that owns it. Each fix gets a regression test.
- **The judge, weekly:** its catch rate on real bad replies and its wrong-block rate. If it catches fewer than half of at least 5 real bad replies, or blocks more than about 1 good reply in 50, it comes off the send path (decision #48).

**Alerts:**
- any attempt at an action that is not allowed (should be 0, and every one is read)
- hand-off rate up more than a third on the 7-day average
- cost per ticket up more than half
- provider errors, or calls going to the backup model, above 5%
- judge blocks above 2% of replies

## Before stage 1

- **Training for associates:** how to read a case file, how the approval queue ranks items, when to override.
- **An escalation playbook:** what an associate does with each hand-off reason (risk, policy gap, knowledge gap, low confidence, data gap, customer asked).
- **Sign-off** on the policy file from the policy owner.

## What we measure

| Metric | Definition | Counter-metric |
|---|---|---|
| Cycle time | Customer's first message to the ticket being resolved or handed to a person, median and 90th percentile | Reopen rate within 7 days |
| Error rate | Wrong decisions in the weekly labelled sample, by failure type | Hand-off rate, because handing everything off makes errors vanish |
| Adoption | Share of eligible tickets resolved without a person, and share of drafts approved without edits | Rubber-stamp rate: approvals done in under 10 seconds |
| Cost per completed task | Model cost plus review minutes at a loaded hourly rate, divided by tickets correctly resolved | Customer satisfaction on auto-resolved tickets against the holdout |

Model cost alone is small: about $0.0006 per ticket at list price in the eval runs. Review minutes will cost far more than that, which is why the approval queue is ranked by risk and shows everything in one view.
