"""ScriptedLLM: a keyword stand-in for the real models, so the whole system runs with no key.

It exists for wiring, unit tests and CI. Its rules were written next to the test
tickets, so its accuracy says nothing about real models. Only live-run numbers count.
What it does prove: the safety gates hold even with a crude classifier, because
decisions are made by code, not by the model.
"""
from __future__ import annotations

import json
import re

PRODUCT_WORDS = {"slide": "Breeze Slide", "sneaker": "Daily Sneaker", "runner": "Cloudstep Runner",
                 "loafer": "City Loafer", "sandal": "Strap Sandal", "boot": "Trail Boot", "walker": "Mini Walker"}
ZERO_USAGE = {"model": "scripted", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "latency_ms": 0,
              "price_is_stand_in": False, "fallback_used": False}


def _tag(text: str, tag: str) -> str:
    m = re.search(rf"<{tag}>\n?(.*?)\n?</{tag}>", text, re.S)
    return m.group(1) if m else ""


def classify(text: str) -> dict:
    t = text.lower()

    def has(*words):
        return any(w in t for w in words)

    hits = []
    if has("store", "staff", "rude", "complain", "lawyer", "injur", "chargeback"):
        hits.append("OTHER")
    if has("address") and has("change", "update", "moving"):
        hits.append("ADDRESS_CHANGE")
    if re.search(r"\bcancel", t) and not has("card was cancelled", "card is cancelled"):
        hits.append("CANCEL")
    if has("damag", "torn", "snapped", "split", "crack", "unglued", "fell off", "scuff", "broke", "defect",
           "wrong", "instead of", "has a size"):
        hits.append("DAMAGED_WRONG_ITEM")
    elif has("return", "exchange", "swap", "too big", "too small", "tight", "don't fit", "dont fit", "change my"):
        hits.append("RETURN_EXCHANGE")
    if has("refund") and (not hits or " also " in t):
        hits.append("REFUND_STATUS")
    if not hits and has("where is my order", "where's my order", "where is o", "where is it", "arrive",
                        "delivered", "sent out", "reach", "any news", "tracking", "when will", "where is order"):
        hits.append("WISMO")
    intent = hits[0] if hits else "OTHER"
    others = hits[1:] if " also " in t else []

    oid = re.search(r"\bO\d{4}\b", text, re.I)
    size = re.search(r"(?:for|to)\s+(?:a\s+)?size\s*(\d{2})", t)
    if has("haven't worn", "havent worn", "not worn", "never wear", "never worn", "unworn", "only tried"):
        unworn = True
    elif has("wore", "worn", "wearing"):
        unworn = False
    else:
        unworn = None
    claim = None
    if intent == "DAMAGED_WRONG_ITEM":
        claim = "wrong_item" if has("wrong", "instead of", "has a size") else (
            "defect" if has("month", "normal use") else "damaged")
    addr = re.search(r"\bto (\d.+?\b(\d{5,6})\b.*?)(?:\.\s|\.$|$| and )", text)
    other_kind = None
    if intent == "OTHER":
        other_kind = "complaint" if has("rude", "complain") else ("question" if "?" in t and has("store", "do you")
                                                                  else "unknown")
    return {
        "intent": intent, "other_intents": others, "confidence": 0.9 if hits else 0.3,
        "order_id": oid.group(0).upper() if oid else None,
        "item_hint": next((v for k, v in PRODUCT_WORDS.items() if k in t), None),
        "target_size": size.group(1) if size else None, "claim_type": claim, "unworn": unworn,
        "delivered_not_received": has("delivered") and has("don't have", "nothing came", "nothing is here",
                                                            "never got", "not here", "didn't get", "not received"),
        "wants_refund": has("refund"),
        "new_address": addr.group(1).strip(" ,.") if addr else None,
        "new_postal_code": addr.group(2) if addr else None,
        "new_country": ("MY" if has("malaysia", "kuala lumpur") else "SG") if addr else None,
        "other_kind": other_kind, "suspicious": False,
    }


class ScriptedLLM:
    def complete(self, step: str, messages: list[dict], json_mode: bool = False) -> tuple[str, dict]:
        user = messages[-1]["content"]
        usage = {**ZERO_USAGE, "step": step}
        if step == "classify":
            return json.dumps(classify(_tag(user, "customer_message"))), usage
        if step == "draft":
            d = json.loads(_tag(user, "decision"))
            name = d.get("customer_name") or "there"
            return f"Hi {name},\n\n" + " ".join(d["reply_points"]) + "\n\nPebblewalk Support", usage
        if step == "judge":
            return json.dumps({"pass": True, "reason": "scripted judge always passes"}), usage
        raise NotImplementedError(f"ScriptedLLM has no '{step}' step. The baseline needs a live or replay run.")
