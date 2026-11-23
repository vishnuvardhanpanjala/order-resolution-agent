# order-resolution-agent

> **Work in progress.** One-day research prototype, built with AI coding help. Synthetic data, fictional brand (Pebblewalk), no employer material. The full write-up lands here after the live model run.

An agent that resolves customer order issues end to end where policy allows, and sends everything else to a person with a full case file. Models only read and write text. Code makes every decision and takes every action.

## Run it with no API key (2 minutes)

```bash
pip install -r requirements.txt
make test    # 93 tests: policy, controls, verifier, pipeline, Groq client against a fake server
make eval    # evals and hard gates (replays recorded model calls, or a scripted stand-in model)
make demo    # 10 hero tickets, the approval queue, one human approval, the audit log
```
No `make` (Windows)? Use `python -m pytest -q`, `python -m evals.run_evals`, `python -m app.demo`.

## Live run on Groq (free tier, about 20 to 25 minutes)

1. Get a free key at https://console.groq.com/keys
2. `cp .env.example .env` and paste the key after `GROQ_API_KEY=`. `.env` is git-ignored.
3. `make check-models`. Every line should say OK. A model that fails can be swapped in `config/models.yaml`.
4. `make live`. It runs our system, the same system with detection off, and the one-big-prompt baseline. It paces itself to the free-tier limits and records every call to `recordings/calls.jsonl`. If it stops halfway, run it again: recorded calls are reused.
5. `make labels` exports traces for hand labelling. Committed recordings let CI replay the run with no key.
