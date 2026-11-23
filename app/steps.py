"""The three model steps: classify, draft, judge. Each logs its own tokens, cost, latency and prompt version.

None of them has tools. The customer's text always goes in as quoted data inside tags.
"""
from __future__ import annotations

import json
import re
import time

from pydantic import ValidationError

from app.llm import LLMUnavailable, load_prompt
from app.types import Decision, Extraction

PROMPTS = {name: load_prompt(name) for name in ("classify", "draft", "judge")}


def _json_part(text: str) -> str:
    m = re.search(r"\{.*\}", text or "", re.S)
    return m.group(0) if m else (text or "")


def _call(llm, step: str, messages: list[dict], trace: dict, json_mode: bool, attempt: int = 1):
    version = PROMPTS[step][0] if step in PROMPTS else step
    t0 = time.perf_counter()
    try:
        out, usage = llm.complete(step, messages, json_mode)
    except LLMUnavailable as e:
        trace["steps"].append({"step": step, "prompt_version": version, "attempt": attempt, "error": str(e)[:300],
                               "wall_ms": round((time.perf_counter() - t0) * 1000)})
        raise
    # wall_ms includes rate-limit waits. latency_ms is the model call itself. Reports use latency_ms.
    entry = {"step": step, "prompt_version": version, "attempt": attempt, **usage, "output": (out or "")[:600],
             "wall_ms": round((time.perf_counter() - t0) * 1000)}
    trace["steps"].append(entry)
    return out, entry


def classify(llm, text: str, trace: dict) -> Extraction | None:
    """Returns None if the output is invalid twice. Raises LLMUnavailable if the provider is down."""
    messages = [{"role": "system", "content": PROMPTS["classify"][1]},
                {"role": "user", "content": f"<customer_message>\n{text}\n</customer_message>"}]
    for attempt in (1, 2):
        out, entry = _call(llm, "classify", messages, trace, json_mode=True, attempt=attempt)
        try:
            return Extraction.model_validate(json.loads(_json_part(out)))
        except (ValueError, ValidationError) as e:  # temporary crutch: one retry with the error shown
            entry["error"] = "invalid: " + str(e).replace("\n", " ")[:200]
            messages = messages + [{"role": "assistant", "content": out or ""},
                                   {"role": "user", "content": "That was not valid: " + entry["error"] +
                                    ". Return only the JSON object."}]
    return None


def draft(llm, decision: Decision, customer_name: str | None, text: str, trace: dict, attempt: int,
          feedback: str | None = None) -> str:
    payload = {"disposition": decision.disposition.value, "reply_points": decision.reply_points,
               "customer_name": customer_name}
    user = f"<decision>\n{json.dumps(payload)}\n</decision>\n<customer_message>\n{text}\n</customer_message>"
    if feedback:
        user += f"\n\nYour last draft failed these checks: {feedback}\nWrite it again and fix that."
    messages = [{"role": "system", "content": PROMPTS["draft"][1]}, {"role": "user", "content": user}]
    out, _ = _call(llm, "draft", messages, trace, json_mode=False, attempt=attempt)
    return (out or "").strip()


def judge(llm, text: str, decision: Decision, reply: str, trace: dict) -> dict:
    """Binary verdict. Returns pass=None if the judge itself fails, which the pipeline treats as 'hold'."""
    payload = {"disposition": decision.disposition.value, "reply_points": decision.reply_points}
    user = (f"<customer_message>\n{text}\n</customer_message>\n<decision>\n{json.dumps(payload)}\n</decision>\n"
            f"<reply>\n{reply}\n</reply>")
    messages = [{"role": "system", "content": PROMPTS["judge"][1]}, {"role": "user", "content": user}]
    for attempt in (1, 2):
        try:
            out, entry = _call(llm, "judge", messages, trace, json_mode=True, attempt=attempt)
        except LLMUnavailable as e:  # a ReplayMiss is not caught: it must fail loudly
            return {"pass": None, "reason": f"judge unavailable: {str(e)[:120]}"}
        try:
            v = json.loads(_json_part(out))
            if isinstance(v.get("pass"), bool):
                return {"pass": v["pass"], "reason": str(v.get("reason", ""))[:200]}
        except ValueError:
            pass
        entry["error"] = "invalid judge output"
    return {"pass": None, "reason": "judge output invalid twice"}
