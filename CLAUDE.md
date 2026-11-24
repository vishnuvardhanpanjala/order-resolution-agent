# Working rules for AI coding assistants on this repo

This repo was built with AI coding help. These rules keep that help honest.

## Rules
1. The model never decides money. `app/policy.py` decides, and it is plain code.
2. Write the test before the code for anything in `app/policy.py` or `app/actions.py`.
3. Never weaken a test or a gate to make it pass. Fix the code, or write down why the test was wrong.
4. Every number in a doc comes from a script run on synthetic data. Do not type numbers by hand.
5. Industry figures are quotes from a named source, never our own data.
6. Pebblewalk is a made-up brand. Do not copy any real brand's name, products or policies.
7. Plain English in docs and comments. Short sentences. No em dashes.
8. When something breaks, add a line to "Mistakes fixed" below with the rule that stops it happening again.

## Mistakes fixed (rule added each time)
- The cross-customer leak check matched substrings, so "Wong Xin Yi" was flagged as leaking another customer
  called "Ng Xin Yi". Rule: identifier checks match whole words only, and every gate failure is read by hand
  before anything is changed, to tell a real leak from a broken check.
- Running the baseline in a second thread crashed: the seed database belonged to the main thread. Found by a
  dry run of `make live` against a fake Groq server, before any real run. Rule: before any command that costs
  time or API quota, dry-run it end to end against a fake provider.
- `make live` asked for a suite called `no_scanner`, but the runner only knows `no_detection`. It would have run,
  and the detection-off safety gates would have been silently left out of the report. The dry run didn't catch it,
  because nothing checked which suites the gates covered. Found by reading the Makefile line by line. Rule: the
  runner refuses unknown suite names, and a test checks every suite the Makefile names.
- "Label more rows" would have re-exported the label file and wiped labels already written. Rule: the label
  export keeps every existing row and its labels, and only adds new rows at the bottom.
