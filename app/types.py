"""Shared data shapes.

Pydantic checks every value that crosses a boundary: model output, policy input,
policy output. If a model returns something that does not fit, we find out here,
not three steps later.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SGT = timezone(timedelta(hours=8))
# A fixed clock. All synthetic dates are relative to it, so evals give the
# same answer no matter what day you run them.
NOW = datetime(2026, 11, 20, 10, 0, tzinfo=SGT)


class Intent(str, Enum):
    WISMO = "WISMO"
    CANCEL = "CANCEL"
    RETURN_EXCHANGE = "RETURN_EXCHANGE"
    DAMAGED_WRONG_ITEM = "DAMAGED_WRONG_ITEM"
    REFUND_STATUS = "REFUND_STATUS"
    ADDRESS_CHANGE = "ADDRESS_CHANGE"
    OTHER = "OTHER"


class Disposition(str, Enum):
    AUTO_RESOLVED = "AUTO_RESOLVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ESCALATED = "ESCALATED"
    CLARIFY = "CLARIFY"


class Reason(str, Enum):
    KNOWLEDGE_GAP = "KNOWLEDGE_GAP"
    DATA_GAP = "DATA_GAP"
    POLICY_GAP = "POLICY_GAP"
    JUDGEMENT = "JUDGEMENT"
    RISK = "RISK"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"


_NULLS = {"", "null", "none", "n/a", "unknown_value"}


class Extraction(BaseModel):
    """What the classifier model must return. Anything that does not fit is rejected."""

    model_config = ConfigDict(extra="ignore")

    intent: Intent
    other_intents: list[Intent] = []
    confidence: float = Field(ge=0, le=1)
    order_id: Optional[str] = None
    item_hint: Optional[str] = None
    target_size: Optional[str] = None
    claim_type: Optional[Literal["wrong_item", "damaged", "defect"]] = None
    unworn: Optional[bool] = None
    delivered_not_received: bool = False
    wants_refund: bool = False
    new_address: Optional[str] = Field(default=None, max_length=160)
    new_postal_code: Optional[str] = None
    new_country: Optional[str] = None
    other_kind: Optional[Literal["question", "complaint", "injury", "legal", "chargeback", "unknown"]] = None
    suspicious: bool = False

    @field_validator("*", mode="before")
    @classmethod
    def _null_strings(cls, v):
        # Small models often write "null" as a string. Treat it as empty.
        if isinstance(v, str) and v.strip().lower() in _NULLS:
            return None
        return v

    @field_validator("order_id", mode="before")
    @classmethod
    def _order_id(cls, v):
        if v is None:
            return None
        v = str(v).strip().upper().lstrip("#")
        if v.isdigit():
            v = "O" + v
        if not re.fullmatch(r"O\d{4}", v):
            raise ValueError("order_id must look like O1234")
        return v

    @field_validator("target_size", "new_postal_code", mode="before")
    @classmethod
    def _to_str(cls, v):
        return None if v is None else str(v).strip()

    @model_validator(mode="after")
    def _clean_other_intents(self):
        seen = []
        for i in self.other_intents:
            if i != self.intent and i not in seen:
                seen.append(i)
        self.other_intents = seen
        return self


class Action(BaseModel):
    """One thing to do in an outside system. Built by policy code, never by a model."""

    name: str
    order_id: Optional[str] = None
    line_no: Optional[int] = None
    amount: Optional[float] = None
    args: dict = {}


class Decision(BaseModel):
    rule_id: str
    disposition: Disposition
    reason: Optional[Reason] = None
    action_class: str = "info"
    actions: list[Action] = []           # run now
    approval_actions: list[Action] = []  # wait for a human
    reply_points: list[str] = []         # the only facts the reply may use
    note: str = ""                       # short reason, for the human reading the case


class PolicyInput(BaseModel):
    """Everything the policy needs. Built by code from scoped tool results."""

    verified: bool
    extraction: Optional[Extraction]
    order: Optional[dict] = None
    order_ref_given: bool = False
    shipment: Optional[dict] = None
    stock: dict[str, int] = {}
    refunds: list[dict] = []
    refunds_90d: int = 0
    lost_claims_180d: int = 0
    risk_flags: list[str] = []
    data_error: Optional[str] = None
    photo_attached: bool = False
    now: datetime = NOW
