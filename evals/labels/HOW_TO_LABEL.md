# How to label (about 45 to 60 minutes)

Open `to_label.csv` in a spreadsheet, with `docs/03_policy.md` next to it.

Each row is one trace: the customer message, the order facts, what the system did and the reply. Fill in three columns. Don't look at the expected answers or the judge's verdicts first. They are left out of the file on purpose.

## ok_overall: y or n
Would a CX lead be happy with this outcome? Right decision, right actions, a reply they'd send.
- If the policy table says one thing and the system did another, that's an **n**.
- If the policy itself gives a bad result for this customer, still mark **n**, and say so in the note. That finding is just as useful.

## reply_ok: y or n
Only the reply text. Does it answer what the customer asked, match what the system did, and promise nothing extra?
This column is what the LLM judge gets compared against, so judge it on its own. A wrong decision with a reply that matches it is still reply_ok = y.

## first_error_note
Only if ok_overall is n. Write one short, specific line about the **first thing that went wrong, furthest upstream**. Stop there. Later errors are often caused by the first one.

Describe what you see. Don't explain why it happened or how to fix it. That comes later.

Good notes:
- "Read 'swap to 44' as a return, not an exchange"
- "Missed that tracking says delivered; treated as in transit"
- "Reply promised a refund date that isn't in the facts"
- "Policy asks for a photo for a S$129 shoe that's clearly the wrong size. Photo makes no sense here."

Not useful: "wrong", "bad reply", "hallucination".

Don't name categories yet. Grouping the notes into failure types is the next step (error analysis), and it works better when the notes were written without categories in mind.

## When to stop
40 rows is the default budget for a one-day build. A common target is 100, but the real rule is to stop when new rows stop showing new kinds of problems. If the last 10 rows still show a kind of problem you haven't seen before, label more: `make labels N=60` adds 20 new rows at the bottom. Rows already in the file, and their labels, are kept as they are.

Save as CSV with the same name when done.
