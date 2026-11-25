# What's left before production

> An honest list. The prototype shows the design works on synthetic tickets against mock systems. Everything below stands between that and real customers.

## Integrations and security
- **Real systems.** The order system, payments, courier and stock are mocks backed by SQLite. Real ones lag, time out halfway and return half-written data. The tool layer retries reads once and hands off on failure, but has never met a real API.
- **Single sign-on and role-based access** for the approval console. Roles exist in code (`app/actions.py`), not in an identity provider.
- **Personal data rules.** Singapore's PDPA: what is stored, for how long, who can see traces. Traces today keep full messages.
- **A data-processing agreement** with the model provider, since messages and order facts are sent to it.
- **External red-teaming.** The 15 attack tickets were written by the same person who built the defences.

## Evals
- **Hundreds of real tickets,** labelled, in place of 63 synthetic ones written by the policy author.
- **More than one labeller,** and a check that they agree. One person labelled every round here.
- **The judge on real bad replies.** Two labelled rounds found none to measure it on, so its catch rate comes from 10 faults we wrote ourselves (decision #48).
- **Languages.** English and Singlish only.
- **Load testing.** Every run here was one ticket at a time.

## Product gaps
- **Two requests in one message** go to a person today (1 of the 63 test tickets).
- **Product questions** ("do you have this in blue?") go to a person. A knowledge base would answer many.
- **Hand-over replies** are written by the drafting model, but they are nearly fixed text. A template would save a draft call and a judge call on nearly 3 tickets in 10 (18 of 63 in round two).
- **Vague first messages** can land on either side of the 0.6 confidence line between runs (T044, decision #49).
- **The same-size exchange rule** (decision #46) treats every such request as a wrong size. Real data may show other reasons.
- **The approval console** is a command line. It needs a real screen, and research with associates on what they need to see to decide fast without rubber-stamping.

## Running it for others
- **A hosted demo.** Chat mode runs in a terminal. A shared web demo needs a small server and a paid model key, because the free tier's daily limits would run out with a few users.
