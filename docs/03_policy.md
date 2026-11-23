# Policy decision table

This is the human-readable copy of `app/policy.py`. Each rule ID appears in every trace and audit-log row, so any outcome can be traced back to one line here. Numbers come from `config/policy.yaml`. Amounts are SGD.

## Global rules (checked first, in this order)
| ID | When | Outcome |
|---|---|---|
| G1 | Identity not verified | CLARIFY: ask for order number and email. Share no account data. |
| G5 | Model output invalid twice | ESCALATED, LOW_CONFIDENCE |
| G3 | Risk flag: injection, refund to a new account or PayNow number, change of email or payment method, asking about another person | ESCALATED, RISK. No actions. |
| G4 | A system errored, even after one retry | ESCALATED, DATA_GAP |
| G5 | Model confidence under 0.6 | ESCALATED, LOW_CONFIDENCE |
| A3 | Address change plus a money request | ESCALATED, RISK |
| G6 | Two or more requests in one message | ESCALATED, POLICY_GAP |
| O1 | OTHER intent | ESCALATED. KNOWLEDGE_GAP for a question, JUDGEMENT for anything else. |
| G2 | Order number given but not on this account | CLARIFY: "we could not find it". Never confirm it exists. |
| G7 | No order number and no single recent order | CLARIFY: which order? |
| L1 | Order has several items and we can't tell which | CLARIFY: which item? |

## Refund rules (apply everywhere)
- Refund amount = what was paid for the line (after discounts) minus earlier refunds on it. Never more.
- Refunds go only to the original payment method.

## By intent
| ID | Intent | When | Outcome |
|---|---|---|---|
| W1 | WISMO | placed or packed | AUTO: give the ETA |
| W2 | WISMO | shipped | AUTO: tracking number and ETA |
| W3 | WISMO | delivered, no complaint | AUTO: delivery date |
| W4 | WISMO | delivered but not received, under 48h | AUTO: check locker and neighbours, wait 48h |
| W5 | WISMO | not received, 48h or more, order up to S$80, no lost-parcel claim in 180 days | AUTO: open courier search and reship |
| W6 | WISMO | not received, 48h or more, otherwise | Courier search now. Reship waits for approval. |
| C1 | CANCEL | placed or packed | AUTO: cancel and full refund, at any amount (the goods never left) |
| C2 | CANCEL | shipped or delivered | AUTO: explain the return route |
| R0 | RETURN_EXCHANGE | not delivered yet | ESCALATED, POLICY_GAP |
| R3 | RETURN_EXCHANGE | final sale (and no defect) | AUTO: decline, say why |
| R4 | RETURN_EXCHANGE | over 30 days (and no defect) | AUTO: decline, say why |
| R5 | RETURN_EXCHANGE | customer says it is worn | AUTO: decline |
| R1 | RETURN_EXCHANGE | first exchange, new size in stock | AUTO: create the exchange |
| R2 | RETURN_EXCHANGE | already exchanged once | Exchange waits for approval |
| R6 | RETURN_EXCHANGE | new size out of stock | AUTO: return label, refund when the item is back |
| R7 | RETURN_EXCHANGE | return, no new size wanted | AUTO: return label, refund when the item is back |
| D0 | DAMAGED_WRONG_ITEM | not delivered yet | ESCALATED, POLICY_GAP |
| D4 | DAMAGED_WRONG_ITEM | outside window: 14 days for wrong or damaged on arrival, 180 for a defect | AUTO: decline, say why |
| D5 | DAMAGED_WRONG_ITEM | 2 or more refunds in the last 90 days | ESCALATED, RISK |
| D7 | DAMAGED_WRONG_ITEM | nothing left to refund on the line | ESCALATED, JUDGEMENT |
| D1 | DAMAGED_WRONG_ITEM | value up to S$80, in stock | AUTO: replacement, no photo |
| D2 | DAMAGED_WRONG_ITEM | value up to S$80, out of stock | AUTO: refund the line value, no photo |
| D3 | DAMAGED_WRONG_ITEM | value over S$80, no photo | CLARIFY: ask for a photo |
| D6 | DAMAGED_WRONG_ITEM | value over S$80, photo attached | Replacement or refund waits for a human to check the photo |
| F1 | REFUND_STATUS | refunds found, none late | AUTO: status, amount, date |
| F2 | REFUND_STATUS | a refund pending over 10 business days | ESCALATED, JUDGEMENT (payments team) |
| F3 | REFUND_STATUS | no refund found | AUTO: say so, ask what they expected |
| A1 | ADDRESS_CHANGE | placed or packed, Singapore address with 6-digit postal code | AUTO: update |
| A2 | ADDRESS_CHANGE | shipped or delivered | AUTO: explain it can't change |
| A4 | ADDRESS_CHANGE | address outside Singapore | ESCALATED, RISK |
| A5 | ADDRESS_CHANGE | no full address given | CLARIFY: ask for it |

## Autonomy gate (after the policy)
`config/autonomy.yaml` sets a level for each action class. If the policy says AUTO but the class is at L1, the same actions wait for a human. At L0 the ticket goes to a person. A kill switch per intent forces L1 with no deploy.

## Who can approve what
| Role | Can do |
|---|---|
| agent | Read, reversible writes, money up to S$80 (cancel-before-dispatch refund is the one exception, checked against the order state) |
| cx_associate | Approve money up to S$300 |
| cx_lead | Approve any amount, with a written reason |
| policy_owner | Change `policy.yaml` through a pull request. Takes no actions. |

Never allowed for anyone through this system: refund to a new destination, change of email or payment method, bulk actions, reading another customer's data.
