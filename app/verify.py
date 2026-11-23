"""Checks on a drafted reply, before it is sent. Code checks run first. They are cheap and exact.
Only if all pass does the LLM judge run (in app/steps.py).

The main idea: the reply may only use facts the policy handed to the drafter
(the reply points). Any amount, date, order number, tracking number, link,
email or phone number that is not in those facts fails the check.
"""
from __future__ import annotations

import re

MONTH = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
         r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b")
DATE_DM = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{MONTH}", re.I)
DATE_MD = re.compile(rf"\b{MONTH}\.?\s+(\d{{1,2}})\b", re.I)
DATE_ISO = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")
MONEY = re.compile(r"(?:S\$|SGD\s?|\$)\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
                   r"|\b(\d+(?:\.\d{1,2})?)\s?(?:SGD|dollars)\b", re.I)
IDS = {"order number": re.compile(r"\bO\d{4}\b"), "tracking number": re.compile(r"\bSG\d{8}\b"),
       "customer id": re.compile(r"\bC\d{3}\b"), "phone number": re.compile(r"(?:\+65\s?)?\b[689]\d{3}\s?\d{4}\b")}
URL = re.compile(r"https?://[^\s)>\]\"']+")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ALLOWED_DOMAIN = "pebblewalk.example"
SUPPORT_EMAIL = "support@pebblewalk.example"
MAX_CHARS = 1200
INTERNAL = ["rule_id", "reason code", "low_confidence", "data_gap", "policy_gap", "knowledge_gap", "system prompt",
            "policy.yaml", "auto_refund_cap", "risk flag", "flagged", "tool call"]
# "You said it was done" patterns. Allowed only if that action really ran, or a reply point says it.
DONE = {
    "issue_refund": r"\b(refunded|(issued|processed|sent|made|approved) (you )?(a |your |the )?(full )?refund|"
                    r"refund (has been|was|is being) (issued|processed|approved|sent|made))\b",
    "cancel_order": r"\b((has been|have been|is now|was|we've|we have|i've|i have) cancel+ed|cancel+ed (your|the) order|"
                    r"order (is|is now) cancel+ed)\b",
    "create_replacement": r"\b(replacement (pair )?(is|has been|will be|was) (sent|shipped|on its way|arranged|dispatched)|"
                          r"(sending|sent|shipping|shipped|arranged|dispatching|dispatched) (you )?(a |your )?(new )?replacement)\b",
    "create_exchange": r"\b(exchange (is|has been|was) (set up|arranged|created|confirmed|processed)|"
                       r"(set up|arranged|created|confirmed|processed) (your|an|the) exchange)\b",
    "create_reship": r"\b(resend|resent|reship\w*|send(ing)? you a new (pair|parcel)|replacement parcel)\b",
    "update_address": r"\b(address (has been|is now|was) (updated|changed)|(updated|changed) (your|the) (delivery )?address)\b",
}


def _amounts(text: str) -> set[float]:
    return {round(float((a or b).replace(",", "")), 2) for a, b in MONEY.findall(text)}


def _dates(text: str) -> set[tuple[str, int]]:
    out = {(m.group(2)[:3].lower(), int(m.group(1))) for m in DATE_DM.finditer(text)}
    out |= {(m.group(1)[:3].lower(), int(m.group(2))) for m in DATE_MD.finditer(text)}
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    out |= {(months[int(mo) - 1], int(d)) for mo, d in DATE_ISO.findall(text) if 1 <= int(mo) <= 12}
    return out


def run_checks(reply: str | None, reply_points: list[str], done_actions: set[str],
               customer_name: str | None = None) -> list[dict]:
    if not reply or not reply.strip():
        return [{"name": "not_empty", "pass": False, "detail": "empty reply"}]
    facts = " ".join(reply_points) + " " + (customer_name or "")
    checks = []

    def add(name, bad, detail):
        checks.append({"name": name, "pass": not bad, "detail": detail if bad else ""})

    extra = _amounts(reply) - _amounts(facts)
    add("amounts_grounded", extra, f"amounts not in facts: {sorted(extra)}")
    extra = _dates(reply) - _dates(facts)
    add("dates_grounded", extra, f"dates not in facts: {sorted(extra)}")
    for label, rx in IDS.items():
        extra = set(rx.findall(reply)) - set(rx.findall(facts))
        add(f"{label.replace(' ', '_')}s_grounded", extra, f"{label} not in facts: {sorted(extra)}")
    bad_urls = [u for u in URL.findall(reply) if not re.match(rf"https?://([\w-]+\.)*{re.escape(ALLOWED_DOMAIN)}(/|$)", u)]
    add("links_allowlisted", bad_urls, f"links outside {ALLOWED_DOMAIN}: {bad_urls}")
    bad_emails = [e for e in EMAIL.findall(reply) if e.lower() != SUPPORT_EMAIL]
    add("emails_allowlisted", bad_emails, f"email addresses not allowed: {bad_emails}")
    low = reply.lower()
    leaks = [w for w in INTERNAL if w in low]
    add("no_internal_terms", leaks, f"internal terms: {leaks}")
    false_claims = [a for a, rx in DONE.items()
                    if re.search(rx, reply, re.I) and a not in done_actions and not re.search(rx, facts, re.I)]
    add("no_false_claims", false_claims, f"says done but did not happen: {false_claims}")
    add("length", len(reply) > MAX_CHARS, f"{len(reply)} chars, max {MAX_CHARS}")
    return checks
