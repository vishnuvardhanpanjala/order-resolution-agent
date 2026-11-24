# Windows names the interpreter python (python3 there often opens the Microsoft Store instead).
ifeq ($(OS),Windows_NT)
PY ?= python
else
PY ?= python3
endif

.PHONY: test eval demo seed check-models live labels judge chat

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

live:          ## full live run on Groq: ours, ours with detection off, and the baseline (needs GROQ_API_KEY)
	$(PY) -m evals.run_evals --mode live --suites ours,no_detection,baseline

N ?= 40
labels:        ## exports N traces for hand labelling (default 40: make labels N=60 for more)
	$(PY) -m evals.export_labels $(N)

chat:          ## talk to the agent as a test customer (live model if GROQ_API_KEY is set)
	$(PY) -m app.chat

judge:         ## compares the LLM judge with the hand labels
	$(PY) -m evals.judge_validation
