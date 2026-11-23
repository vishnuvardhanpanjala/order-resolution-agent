PY ?= python3

.PHONY: test eval demo seed check-models live labels judge

test:          ## unit tests, including the policy tests (no key needed)
	$(PY) -m pytest -q

eval:          ## replays recordings if they exist, else uses the scripted model (no key needed)
	$(PY) -m evals.run_evals --mode auto

demo:          ## runs the 10 hero tickets and shows the approval queue (no key needed)
	$(PY) -m app.demo

seed:          ## rebuilds data/tickets.jsonl from data/scenarios.py
	$(PY) -m data.seed

check-models:  ## pings each Groq model in config/models.yaml (needs GROQ_API_KEY)
	$(PY) -m app.llm --check

live:          ## full live run on Groq: ours, ours without the scanner, and the baseline (needs GROQ_API_KEY)
	$(PY) -m evals.run_evals --mode live --suites ours,no_scanner,baseline

labels:        ## exports traces for hand labelling
	$(PY) -m evals.export_labels

judge:         ## compares the LLM judge with your labels
	$(PY) -m evals.judge_validation
