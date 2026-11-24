# PRD: Order-issue resolution agent

**Status: research preview.** Built in one day. Pebblewalk is a made-up shoe brand. All data is synthetic. Nothing here has run on real customers.

## Who it is for
- **Pebblewalk customers** who message about an order they already placed.
- **CX associates**, who today answer every one of these by hand.

## The problem
Most order messages are the same few questions. Where is it? Cancel it. Wrong size. It broke. Where's my refund?
To answer one, an associate opens the order system, the courier portal and the payments screen, then writes a reply.
Customers wait hours for answers that are a lookup. The tickets that really need a person wait in the same queue.

## Goal
Resolve the simple order issues end to end, where policy allows. Send everything else to a person with a full case file, so they don't start from zero.

**Success means every ticket ends in the correct disposition.** Either it was resolved correctly, or it went to a person (for approval or for a decision) with the facts attached.
**And the agent takes zero money actions that policy does not allow.**

The model is right most of the time, not all of the time. So the model never gets the final say on money. Code does. That is how a model that is right 90% of the time can sit inside a process that has to be right every time.

## Non-goals (v1)
- Product questions ("do you have this in blue?"). No knowledge base yet. These go to a person as KNOWLEDGE_GAP.
- Tickets with two requests in one message. These go to a person as POLICY_GAP.
- Languages other than English (Singlish is in the test set).
- Real integrations. The order system, payments and courier are mocks.

## Use cases
| Intent | Example message | What good looks like |
|---|---|---|
| WISMO | "Where is O1001?" | Tracking number and ETA in one reply, no human |
| CANCEL | "Cancel O1004, ordered by mistake" | Cancelled and refunded if not shipped yet. Return steps if it has. |
| RETURN_EXCHANGE | "Size 42 too small, can I get 44?" | Exchange set up if in stock and in policy |
| DAMAGED_WRONG_ITEM | "Sole came off on day one" | Replacement or refund up to S$80 with no photo. Above that, a human checks a photo. |
| REFUND_STATUS | "Where's my refund?" | Status and date from payments. Late refunds go to the payments team. |
| ADDRESS_CHANGE | "Change address for O1018" | Updated if not shipped and in Singapore |
| OTHER | complaint, injury, legal threat | Straight to a person, with the reason |

## Acceptance criteria for v1
v1 may move to shadow mode (reading real tickets, sending nothing) only when all of these hold.

| Criterion | Target | Checked by |
|---|---|---|
| Policy tests | 100% pass | hard gate in `make eval` and CI |
| Money actions that policy does not allow | 0, in the normal run and in the run with every detector off | hard gate |
| Another customer's data in a reply | 0, in both runs | hard gate |
| Harmful outcomes from attack tickets | 0, in both runs | hard gate |
| Recorded model calls that CI can't replay | 0 | hard gate |
| Correct disposition rate | at or above the floor in `evals/thresholds.yaml`, set from the first live run | hard gate once the floor is set |
| Reply judge checked against hand labels | bad replies caught and good replies passed are both reported, with the base rate | eval report, read by a person |

For each intent, "What good looks like" in the use-case table is its acceptance test. Each one is written as tickets in `data/scenarios.py`, with expected outcomes fixed before any model ran. A failing hard gate fails the build.

## Known failure modes
Filled in from error analysis on simulated traffic. See `06_failure_log.md`.

## Metrics, each paired with a counter-metric
A metric on its own can be gamed. The counter-metric catches the game.

| Metric | Why | Counter-metric |
|---|---|---|
| Correct disposition rate | The main goal | Over-escalation rate. Sending everything to a human is "safe" and useless. |
| Correct auto-resolution rate | Work taken off associates | Reopen or complaint rate after an auto reply (needs production) |
| Unauthorized money actions | Safety. Must be 0. | Missed escalations (should have gone to a human, didn't) |
| Cost per correctly auto-resolved ticket | Unit cost | Human review minutes per approval. Approvals are a hidden cost. |
| Cycle time, message to resolution | Customer wait | Reply quality: judge pass rate plus a weekly human sample |
| Approval queue time | Customer wait on bigger cases | Rubber-stamp rate: approvals done in under 10 seconds |
