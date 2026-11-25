# Build log: how this was built, and how AI was used

## Who did what

I set the goal, the scope and the limits, made every policy and product call, ran the system live, labelled the results by hand, and decided what to fix after each round. Claude, Anthropic's model, working as a coding agent, wrote the code, the tests and first drafts of the docs from my plan and my calls. Commits made with it say so.

The limits I set at the start: no agent frameworks, no vector database, no paid services, no Docker, and nothing from any employer. Every number labelled synthetic.

## Timeline

| Day | What happened |
|---|---|
| 23 Nov 2026 | Wrote the plan: phases, constraints, what "done" means for each. Built the policy table and its tests first, then the synthetic data, the model steps, the action service and the evals. Everything ran with no API key, against a scripted stand-in model. |
| 24 Nov | Groq had retired the two Llama models in the plan, so the first ping failed. Moved to the models Groq recommends, added a model check before every live run, and taught the client to skip a model that hits its daily limit. First live run. Labelled 39 traces. Round-one error analysis (decisions #38 to #44). |
| 25 Nov | Second live run on the fixes. Labelled again. Round-two error analysis (#45 to #51). Third live run on those fixes. |

## How the work was run

- **Plan first.** A written plan with phases and checks, so each phase could be reviewed on its own and a fresh session could pick up from the repo alone.
- **Rules and tests together.** Each row of the policy table (`docs/03_policy.md`) has a test. A business-rule change changes the table, the test and the expected outcomes together, and the decision log says why.
- **Expected outcomes before any model ran.** The test tickets' answers were written from the policy, not from what the system did. Eleven were changed later, each on a named policy call, and marked where they are.
- **Dry runs before every live run.** A local fake model server stands in for Groq, so the whole live flow (pings, pacing, daily limits, recording, label export) runs before spending quota. It caught a syntax slip in a hand-run script, and a label-sheet bug that would have wasted most of round three's carried-over labels.
- **Record and replay.** Every live model call is recorded. CI replays them with no key, so anyone can rerun the evals and get the same numbers.
- **Mistakes become rules.** Each mistake, and the rule added so it doesn't happen again, is listed in `CLAUDE.md`.

## Where the methods came from

The eval method follows practices people who run LLM systems in production share publicly: pass/fail labels instead of scores, open notes on the first thing that went wrong, grouping notes into failure types before fixing, and checking an LLM judge against human labels by how many bad replies it catches and good ones it passes, not by "agreement". I adapted them to this case. The safety design (cut the way out, so an attack has nowhere to send data) is common practice for agents that read untrusted text.

## What I would do differently

- **Label with a second person.** One labeller means my blind spots are in every round.
- **Write the label sheet more carefully the first time.** Its gaps caused more wrong labels in round one than the system did (decision #43).
- **Get a second model to review the diffs.** Not done here.
