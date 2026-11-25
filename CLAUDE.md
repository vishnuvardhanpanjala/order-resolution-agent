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
- The model check before the first live run found two models gone: Groq shut Llama 3.1 8B and 3.3 70B down
  for free accounts in August 2026. Rule: `make check-models` before every live run, and the model list in
  `config/models.yaml` records the date it was checked.
- Adding UTF-8 to the label export left a comment in the middle of a line, which broke the file. No test
  imported that script, so 97 tests still passed. A dry run of the labelling steps caught it. Rule: a test
  compiles every Python file, and dry runs cover the hand-run scripts too, not just `make live`.
- The scorecard used two definitions of "harmful": any unexpected money action counted against the baseline,
  but only out-of-policy money counted against ours. Found in error analysis, when both systems made the same
  S$69 replacement on T050 and only the baseline's was "harmful". It flattered our system. Rule: one scoring
  rule per measure for every system, and a test that feeds both scorers the same action.
- The label sheet printed an unshipped order as "placed, delivered -". The labeller read it as delivered, and
  8 of 16 "wrong outcome" labels in round one came from that and similar gaps. Rule: the sheet states every
  fact in words, with the test's date, and a test checks the wording. Read the labeller's notes, not just the
  counts: the notes are where a broken eval tool shows.
- "Label more rows" would have re-exported the label file and wiped labels already written. Rule: the label
  export keeps every existing row and its labels, and only adds new rows at the bottom.
- The judge-validation table leaves out replies the judge blocked, because the customer never saw them. So
  in round two the judge's one wrong block (T058) showed nowhere except as a hand-off. Rule: the report lists
  every reply the judge blocked, to be read by hand.
- The judge checked "no fact outside the decision" without being told the customer's name, so a correct
  greeting looked like an invented fact. Rule: when a checker flags something, first ask whether it was given
  everything the writer was given.
- Round two's scenario edits changed four expected outcomes in `data/scenarios.py`, but `data/tickets.jsonl`,
  which the evals read, was not rebuilt, so the live run scored those tickets against old answers. Caught
  while writing the README, before any numbers were used. Rule: a test fails if the tickets file and the
  scenarios differ.
