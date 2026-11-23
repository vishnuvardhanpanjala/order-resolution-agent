"""A cheap first filter for obvious attacks. Easy to get past, so it is NOT the safety layer.

Safety comes from the architecture: tools locked to the customer, policy in code,
caps in the action service, and no way to send data out. The eval also runs with
this scanner switched off, to show the hard gates still hold without it.
"""
from __future__ import annotations

import re

INJECTION = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|messages)",
    r"\b(system|admin|developer)\s*(prompt|override|mode)\b",
    r"(^|\n|\.\s*)\s*system\s*:",
    r"<!--|-->|\bassistant\s*:",
    r"\b(issue_refund|cancel_order|update_address|create_reship|create_replacement)\b",
]
PATTERNS = [(r, "injection") for r in INJECTION] + [
    (r"\bpay\s?now\b[^.]{0,20}\d{4}|\b(bank|dbs|ocbc|uob|posb)\b[^.]{0,25}\d{3}"
     r"|refund[^.]{0,30}\b(new|different|another) (account|card|number)", "new_destination"),
    (r"\b(change|update)\b[^.]{0,20}\b(e-?mail|payment method|card on (the )?account)\b", "account_change"),
    (r"\b(store manager|override code|staff override|i am (an? )?(admin|manager|employee))\b", "fake_authority"),
    (r"\b(system prompt|internal (refund )?(rules|policy)|refund cap)\b", "internal_info"),
    (r"\b(friend|wife|husband|sister|brother|mother|father|colleague)'s\b|\b(her|his) (order|address|email|phone)\b"
     r"|\bwhat did (she|he) order\b", "other_person"),
]
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def scan_message(text: str, own_email: str | None = None) -> list[str]:
    t = text.lower()
    flags = [name for pattern, name in PATTERNS if re.search(pattern, t)]
    if any(e.lower() != (own_email or "").lower() for e in EMAIL.findall(text)):
        flags.append("other_email")
    return sorted(set(flags))


def scan_tool_text(text: str | None) -> bool:
    """For free text that comes back from outside systems, like courier notes."""
    t = (text or "").lower()
    return any(re.search(p, t) for p in INJECTION) or bool(EMAIL.search(t))
