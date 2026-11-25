# Policy decision table

This is the human-readable copy of `app/policy.py`. Each rule ID appears in every trace and audit-log row, so any outcome can be traced back to one line here. Numbers come from `config/policy.yaml`. Amounts are SGD.

## Global rules (checked first, in this order)
The principle, set by the policy owner after the first round of labels: ask for details first, act only when it is safe, and hand over to a person when asking stops helping (decision #38).

| ID | When | Outcome |
|---|---|---|
| G0 | The customer asks for a person | ESCALATED, CUSTOMER_ASKED |
| G1 | Identity not verified | CLARIFY: ask for order number and email. Share no account data. |
| G3 | Risk flag: injection or commands, refund to a new account or PayNow number, change of email or payment method, claimed staff authority, asking for internal rules | ESCALATED, RISK. No actions. |
| G4 | A system or the model provider errored, even after one retry | ESCALATED, DATA_GAP |
| G5 | Model output invalid twice | ESCALATED, LOW_CONFIDENCE |
| G9 | Asking about another person's order or account | CLARIFY: we can only share your own orders, send your order number. Quiet alert to the risk team. |
| G5 | Model confidence under 0.6 | CLARIFY: tell us a bit more |
| A3 | Address change plus a money request | ESCALATED, RISK |
| G6 | Two or more requests in one message | ESCALATED, POLICY_GAP |
| O1 | OTHER intent | ESCALATED. KNOWLEDGE_GAP for a question, JUDGEMENT for anything else. |
| G7 | No order number given (any intent, refund status too) | CLARIFY: which order? Never guess one (decision #39). |
| G2 | Order number given but not on this account | CLARIFY: "we could not find it". Never confirm it exists. |
| L1 | Order has several items and we can't tell which | CLARIFY: which item? |

Two checks run on the result of the rules above:
| ID | When | Outcome |
|---|---|---|
| G3 | The model alone found the message suspicious, and the answer would take an action | ESCALATED, RISK. If the answer takes no action (a final-sale "no", a tracking number), it goes out, with a quiet alert. |
| G8 | The answer is a question, and we have already replied twice in this conversation | ESCALATED, LOW_CONFIDENCE |

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
| W6 | WISMO | not received, 48h or more, otherwise | Courier search now. Reship waits for approval. The reply says a check is pending. |
| C1 | CANCEL | placed or packed | AUTO: cancel and full refund, at any amount (the goods never left) |
| C2 | CANCEL | shipped or delivered | AUTO: explain the return route |
| R0 | RETURN_EXCHANGE | not delivered yet | ESCALATED, POLICY_GAP |
| R3 | RETURN_EXCHANGE | final sale (and no defect) | AUTO: decline, say why |
| R4 | RETURN_EXCHANGE | over 30 days (and no defect) | AUTO: decline, say why |
| R5 | RETURN_EXCHANGE | customer says it is worn | AUTO: decline |
| R1 | RETURN_EXCHANGE | first exchange, new size in stock | AUTO: create the exchange. If the "new" size is the size already on the order, it is handled as a wrong-item claim under D1/D2 instead (decision #46). |
| R2 | RETURN_EXCHANGE | already exchanged once | Exchange waits for approval |
| R6 | RETURN_EXCHANGE | new size out of stock | AUTO: return label, refund when the item is back |
| R7 | RETURN_EXCHANGE | return, no new size wanted | AUTO: return label, refund when the item is back |
| D0 | DAMAGED_WRONG_ITEM | not delivered yet | ESCALATED, POLICY_GAP |
| D4 | DAMAGED_WRONG_ITEM | outside window: 14 days for wrong or damaged on arrival, 180 for a defect | AUTO: decline, say why |
| D5 | DAMAGED_WRONG_ITEM | 2 or more refunds in the last 90 days | ESCALATED, RISK |
| D7 | DAMAGED_WRONG_ITEM | nothing left to refund on the line | ESCALATED, JUDGEMENT |
| D1 | DAMAGED_WRONG_ITEM | value up to S$80, in stock | AUTO: replacement, no photo, and a free return label: the replaced pair comes back, wrong or damaged (decisions #40, #45). |
| D2 | DAMAGED_WRONG_ITEM | value up to S$80, out of stock | AUTO: refund the line value, no photo. A wrong item also gets a free return label. A damaged pair stays with the customer. |
| D3 | DAMAGED_WRONG_ITEM | value over S$80, no photo | CLARIFY: ask for a photo |
| D6 | DAMAGED_WRONG_ITEM | value over S$80, photo attached | Replacement or refund waits for a human to check the photo |
| F1 | REFUND_STATUS | order number given, refunds found, none late | AUTO: status, amount, date, and the card it went to (decision #47) |
| F2 | REFUND_STATUS | a refund pending over 10 business days | ESCALATED, JUDGEMENT (payments team) |
| F3 | REFUND_STATUS | no refund found | AUTO: say so, ask what they expected |
| A1 | ADDRESS_CHANGE | placed or packed, Singapore address with 6-digit postal code | AUTO: update. The reply repeats the full address with the postal code. |
| A2 | ADDRESS_CHANGE | shipped or delivered | AUTO: explain it can't change |
| A4 | ADDRESS_CHANGE | address outside Singapore | CLARIFY: we only deliver in Singapore, send a Singapore address. Quiet alert. |
| A5 | ADDRESS_CHANGE | no full address, or the postal code is not 6 digits | CLARIFY: ask for it |

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
