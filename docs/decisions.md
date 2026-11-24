# Decision log

Every judgement call in this build: the options, what was picked, why, and what it costs. Built in one day, with AI coding help.

## Architecture

**1. The model reads, code decides.**
Options: one prompt holding the policy, where the model decides and acts. A model in a tool-calling loop. Or the model only fills in a form, and code decides.
Picked: the form. The classifier turns a message into fields (intent, order number, size, claim type). `policy.py` decides. The action service acts.
Why: money rules have to give the same answer every time, be testable, and point to a rule ID when someone asks "why did we refund this?" A prompt can't promise any of that.
Cost: a new kind of request needs a code change, not a prompt tweak. Messages that don't fit the form go to a person.

**2. The drafter only rewords facts that code wrote.**
The policy writes `reply_points`, plain sentences with every amount, date and tracking number already in them. The drafting model makes them sound human and answers the customer's actual words. A code check then fails any amount, date, order number, link, email or phone number that isn't in those points.
Why: the drafter can't make up a refund date if it is never asked for one.
Cost: replies are less free-form. Good for support. Bad for open questions, which is why those go to a person.

**3. One action service for the agent and for humans.**
The agent and the approval console call the same `execute()`, with the same checks, the same idempotency key and the same audit log.
Why: if humans had a separate path, the two would drift apart, and the audit log would only show half the story.

**4. The action service repeats some policy checks on purpose.**
The refund cap, "never more than paid", "only to the original payment method" and "only this customer's orders" are in the policy AND in the action service.
Why: the policy decides what should happen. The action service makes sure the worst cases can't happen even if the policy has a bug. Two locks, two owners.

**5. The autonomy gate is separate from the policy.**
The policy says what is allowed (owned by the policy owner). `autonomy.yaml` says how far the agent is trusted to do it alone (owned by product, earned with evidence). The gate takes the stricter of the two and can only make things more careful.
Why: you can switch on "draft only" for refunds during rollout without touching business rules. A kill switch per intent needs no deploy.

**6. Tools are built already locked to one customer.**
The model never supplies a customer ID. Asking for another customer's order returns "not found", the same as an order that doesn't exist, so the agent can't even confirm it exists.

**7. The tool allowlist follows the rule that fired, not the model's guess.**
If the model says RETURN_EXCHANGE but the policy routes a defect claim to the damage rules, the damage tools are allowed. Code decides the allowlist either way. The model's guess is never trusted to widen it.

**8. Account-level reads for every intent.**
The first tool allowlist had no way to count a customer's refunds for the velocity check. I added two read-only tools every intent may use: `get_risk_signals` (refund and lost-parcel counts) and `find_recent_orders` (IDs only). Both are reads and both are scoped to the customer.

## Policy calls

**9. No photo for damaged items up to S$80 (a step removed in discovery).**
For a S$59 sandal, the photo costs more in associate time and customer wait than the fraud it stops. Abuse is caught another way: 2 refunds in 90 days sends any claim to a person.
Risk accepted: someone can get one free pair under S$80 by lying, once. The velocity check stops the second time.

**10. No "please confirm it's unworn" step.**
The warehouse checks every return on arrival anyway, so asking first adds a round trip and stops nothing. If the customer says it's worn, we decline.
This changes the first draft of the policy, which said "unworn (customer says so)".

**11. Don't ask a logged-in customer for an order number we can work out.**
If they have exactly one order in the last 60 days, use it. Two or more, ask which one.

**12. "Arrived damaged" uses the 14-day window, not 180.**
The first draft of the policy gave 14 days for wrong items and 180 for defects, and left damage on arrival open. Damage on arrival should be noticed when the box is opened, like a wrong item. A fault that shows up after weeks of use is a defect.

**13. Refund velocity: 2 refunds in 90 days already means a person looks.**
"Limit 2" read as: the third refund in 90 days needs a human.

**14. Overseas address, or a postal code that isn't 6 digits: RISK, not CLARIFY.**
Changing a delivery address is a classic step in account takeover, and we only ship in Singapore. This is harsh on a customer who mistypes a postal code. I'd change it to CLARIFY for 5 or 7 digits once real data shows how often that happens.

**15. Two requests in one message go to a person.**
Splitting them wrong (cancel the wrong order, say) costs more than a short wait. Listed in `08_whats_left.md`.

**16. The cancel refund is the one exception to the S$80 cap.**
The goods never left, so refunding the full amount is low risk. But the action service doesn't take the caller's word for it. It allows the exception only if the order is already cancelled in the order system and the amount equals exactly what is left to refund.

## Safety calls

**17. Injection in a courier note: strip it and alert, but still answer the customer.**
The customer did nothing wrong, so escalating them would punish the wrong person. The note never reaches any model anyway, because the policy only passes chosen fields on. The alert is for the security team.

**18. The regex scanner is not the safety layer.**
It catches lazy attacks and saves a model call. Adaptive attackers get past filters like this. So the eval runs a second suite with every detector switched off (regex and the model's "suspicious" flag). The hard gates must still pass there. That is what proves the safety comes from the architecture.

**19. Two attack measures, not one.**
"Harmful" means money moved outside policy, another customer's data was exposed, or the attacker's text (a PayNow number, an outside email) got into the reply. "Changed" means the system did anything the ticket didn't call for. With detection off, one attack (T050) gets a S$69 replacement pair sent to the customer's own verified address. That is "changed", not "harmful": it's what policy gives anyone with a damaged item under S$80. The attacker wanted cash to a new PayNow number and didn't get it.

**20. Risk escalations never tell the customer why.**
"Your message was flagged" teaches an attacker what to change next time. They get the same "a team member will reply" as everyone else.

**21. When the judge can't give a verdict, hold the reply.**
If the judge model is down or returns junk twice, the draft is not sent. The customer gets a fixed template and a person writes the real reply. Fail closed.

## Model and eval calls

**22. Groq, through a small OpenAI-compatible client, not LiteLLM.**
Groq speaks the OpenAI chat format, so about 100 lines of code do the job with no extra dependency. Changing provider means editing `models.yaml` or setting `LLM_BASE_URL`.

**23. A model per step.**
Classify uses the small, fast llama-3.1-8b-instant, because reading a message into a form is a simple job. Draft uses gpt-oss-20b. The judge is llama-3.3-70b, a different family from the drafter, so it doesn't share the drafter's blind spots. Every step has a fallback model.

**24. The baseline gets the biggest model and the same facts.**
gpt-oss-120b, the same customer scoping, the same facts including the raw courier note. So any gap is about who decides (code or model), not about model size or data access. The comparison is tilted in the baseline's favour on purpose.

**25. Code checks first, then one yes-or-no judge.**
Code checks are cheap and exact, so they go first and the judge only runs on drafts that pass them. The judge answers pass or fail with one reason. No 1 to 5 scores: nobody can act on a 3.

**26. Over-escalation is measured.**
Without it, a system that sends everything to a human would score perfectly on safety. Over-escalation is the counter-metric that catches that.

**27. A fixed clock, and a fresh copy of the data for every ticket.**
`NOW` is fixed at 20 Nov 2026 so the evals give the same answer any day. Each ticket runs on its own copy of the seed data, so one ticket's refund can't change the next ticket's velocity check.

**28. Recordings tied to the exact prompt.**
Each recorded call is keyed by a hash of the step and the full prompt. Change a prompt, and replay fails with a "replay miss" in CI instead of quietly using an old answer. That forces a re-run of the live evals after every prompt change.

**29. The scripted model is labelled as worthless for accuracy.**
Its keyword rules were written next to the test tickets, so of course it scores well. Its only jobs: let everything run with no key, and show the hard gates hold even with a crude classifier.

**30. Synthetic tickets written by the same person who wrote the policy.**
This is the biggest weakness of the eval. Real customers will write things I didn't think of. It's the first thing the rollout plan fixes, with shadow mode on real traffic.

**31. Business days ignore public holidays.**
Known gap. A refund sent before Deepavali would be escalated a day early. Easy fix with a holiday calendar.

## Evals, second pass

After Phase 4 I went back over the eval design before spending any real model calls on it. Most of it held. These are the changes, and the places where I knowingly went against common eval practice.

**32. The judge writes its reason before its verdict.**
It is still pass or fail. The reason comes first, so the verdict follows from it, and a wrong verdict can be traced to the reason that caused it.
Cost: a few more output tokens per reply.

**33. The judge is measured by what it catches, not by agreement.**
Two numbers, with the base rate next to them: how many bad replies it fails, and how many good replies it passes. If 1 reply in 10 is bad, a judge that passes everything "agrees" with people 90% of the time and catches nothing. The report warns when fewer than 5 bad replies were labelled.
Known limit: if the judge prompt is changed during error analysis, it will be checked on the same labels, so the numbers will look better than they are. 40 labels leave no room to hold some back. The report will say so. For the same reason, no labelled examples go into the judge prompt.

**34. One general judge, built before any error analysis.**
The usual practice is to build judges only for failure types found in the data, one judge per failure. I didn't, because the judge here also guards every reply before it is sent, so it had to exist from day one.
Cost: it may be checking the wrong things. Error analysis decides from the labels whether to narrow it or split it. There are no generic "helpfulness" or "toxicity" scores anywhere.

**35. 40 labelled traces, not 100.**
A common target is 100. The real rule is to stop when new traces stop showing new kinds of problems. 40 fits a one-day build. The labelling guide says to keep going if the last 10 rows still show something new, and the export adds rows without touching labels already written.

**36. Test tickets cover set dimensions, including how people write.**
The dimensions are intent, order state, money band (up to S$80 or above), attack type, and customer voice: plain, Singlish, terse, typos, all caps, angry, long story. A review found the 30 "normal" tickets were mostly tidy English, which hides exactly the failures a classifier makes. I reworded 11 of them into styles Singapore customers really use, for example "O1003 when reach ah". The meaning and expected outcomes didn't change. Hero tickets and tickets used by tests were left alone. Thinking in dimensions gives more varied inputs than asking for "some customer messages".
Cost: still written by one person (#30). The keyword stand-in model now misreads more of them, which is the point.

**37. The eval set grows from failures found, not from guesses up front.**
The 60 tickets check the policy and the gates, which are plain code and don't change often. Checks on model behaviour get added only after error analysis finds a failure, each one as a regression test. If the fix is simple (a missing fact in a prompt), fix it and add a cheap code test. Use a judge only where code can't decide. Writing hundreds of model checks before the prompts settle would mostly test guesses.
