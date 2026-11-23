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
(empty so far)
