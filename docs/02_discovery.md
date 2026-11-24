# Discovery: map the work, remove steps, then automate

All of this is a simulated discovery. Pebblewalk is fictional. The as-is workflow is a typical one for a small ecommerce CX team, written as if from interviews with associates. Frequencies below are **assumptions**, not measurements.

## As-is: a damaged-item ticket today (9 steps)
1. Customer writes "my shoe broke" in web chat, while logged in.
2. Associate asks for the order number.
3. Customer replies with the order number, sometimes hours later.
4. Associate looks the order up in the order system.
5. Associate asks for a photo. Always, whatever the price.
6. Customer sends a photo, often the next day.
7. Associate opens the courier portal by hand to find the delivery date and check the window.
8. Associate checks stock, picks replacement or refund, and asks a team lead to OK any refund.
9. Associate does the replacement or refund, writes the reply, and logs it in a spreadsheet.

## Steps removed before automating anything
Automating a bad process just makes the bad process faster. So first, cut.

| Step | Change | Why |
|---|---|---|
| 2 | **Kept, but asked once and only when needed** | The chat already knows who the customer is, so no email or account check. The first version also skipped asking for the order number when the customer had one order in the last 60 days. The policy owner reversed that after the first round of labels: answering about a guessed order felt wrong to the person reviewing, and asking costs one short message (decision #39). |
| 3 | **Shortened** | The customer replies in the same chat, and the agent reads the whole conversation, so nothing they already said is asked again. |
| 5, 6 | **Removed for items up to S$80** | For a S$59 sandal, the photo costs more in associate time and customer wait than the fraud it stops. Abuse is caught by a refund-velocity check instead (2 refunds in 90 days sends the claim to a person). Above S$80 the photo stays, and a person looks at it. |
| 7 | **Replaced** by an API call | Looking up a delivery date by hand is pure lookup. Code does it exactly. |
| 8 | **Narrowed** | Team lead OK is only needed above S$300. The agent alone can do up to S$80, an associate up to S$300. |
| 9 | **Replaced** | The audit log is written automatically by the action service. No spreadsheet. |
| (return flow) | **Removed** "please confirm the item is unworn" | The warehouse checks every return on arrival anyway. Asking the customer to promise first adds a round trip and stops nothing. If they say it is worn, we decline. |

## To-be: the same ticket (who does each step, and why)
| # | Step | Done by | Why this and not the others |
|---|---|---|---|
| 1 | Know who the customer is | Code | Comes from the login session. Never from the message text, which anyone can type. |
| 2 | Read the message: intent, order number, claim type, size | AI (small model) | Messy text, typos, Singlish. This is what language models are good at, and a mistake here is caught by later steps. |
| 3 | Fetch order, delivery date, stock, refund history | Code | Exact lookups. Tools are locked to this customer's ID. |
| 4 | Decide: replace, refund, ask for photo, or escalate | Code (policy) | Money rules must be exact, testable and the same every time. A model is none of those. |
| 5 | Check a damage photo above S$80 | Human | Judging a photo is hard to verify, and a person should be accountable for bigger money. |
| 6 | Do the replacement or refund | Code (action service) | Caps, idempotency and the audit log live here, shared with the human console. |
| 7 | Write the reply | AI (mid model) | Tone, angry customers, Singlish. It gets the decision and the facts, and has no tools. |
| 8 | Check the reply | Code first, then AI judge | Code checks every number, date and link against the facts. A second model checks the reply answers what was asked. |
| 9 | Send, and log | Code | Only to the verified customer's own thread. |

## Which intents to build first
Scored 1 (low) to 3 (high). Frequency is an assumption based on typical ecommerce ticket mixes. It has not been measured. Cost means cost to build and run: build effort, model calls, and the human review time the flow still needs. Also an estimate.

| Intent | Freq (assumed) | Impact | Feasibility | Risk | Cost | Decision |
|---|---|---|---|---|---|---|
| WISMO | 3 (~35%) | 3 | 3: pure lookup | 1 | 1: one lookup, one small-model call | Build first, full auto |
| RETURN_EXCHANGE | 2 (~20%) | 3 | 2: stock and windows | 2 | 2: stock checks, labels, warehouse handling | Build, auto for first exchange |
| DAMAGED_WRONG_ITEM | 2 (~12%) | 3 | 2 | 3: money | 2: replacement goods, and a person checks photos above S$80 | Build, auto only up to S$80 |
| CANCEL | 2 (~10%) | 2 | 3 | 2: money, but goods never left | 1 | Build, auto before dispatch |
| REFUND_STATUS | 2 (~10%) | 2 | 3 | 1 | 1 | Build, full auto |
| ADDRESS_CHANGE | 1 (~5%) | 2 | 3 | 3: classic account-takeover step | 1 | Build narrowly: Singapore only, never combined with a refund |
| OTHER | 1 (~8%) | 1 | 1 | 3 | 3: needs a knowledge base, and most cases still need a person | Do not automate. Route to a person. |

## What we chose not to automate
- Complaints about staff, injury, legal or chargeback threats. These need a person who is accountable.
- Judging damage photos above S$80.
- Any refund above S$80 by the agent.
- A second exchange on the same item. It is often a sign of something else going on.
- Product questions. There is no knowledge base in v1, and a made-up answer is worse than a slow one (a chatbot once invented a refund policy and the airline had to honour it).
- Tickets with two requests. Rare, and splitting them wrong is costly.
