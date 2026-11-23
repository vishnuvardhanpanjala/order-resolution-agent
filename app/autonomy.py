"""Autonomy gate. Runs after the policy.

The policy says what is allowed. This gate says how far we trust the agent to
do it alone, per action class (config/autonomy.yaml). It always takes the
stricter of the two, and it can only make a decision more careful, never less.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.policy import TEAM_REPLY
from app.types import Decision, Disposition as D, Intent, Reason as R

ROOT = Path(__file__).resolve().parent.parent


def load_autonomy_config(path: Path = ROOT / "config" / "autonomy.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text())


def gate(decision: Decision, intent: Intent | None, cfg: dict) -> Decision:
    if decision.disposition != D.AUTO_RESOLVED or not decision.actions:
        return decision  # nothing to do on its own, so nothing to hold back
    level = cfg["levels"].get(decision.action_class, cfg["default_level"])
    if intent is not None and intent.value in (cfg.get("kill_switch") or []):
        level = "L1"
    if level == "L2":
        return decision
    if level == "L0":
        return decision.model_copy(update=dict(
            disposition=D.ESCALATED, reason=R.JUDGEMENT, actions=[], approval_actions=[],
            rule_id=decision.rule_id + "/L0", reply_points=[TEAM_REPLY],
            note=f"autonomy L0 for {decision.action_class}"))
    return decision.model_copy(update=dict(
        disposition=D.PENDING_APPROVAL, actions=[], approval_actions=decision.actions + decision.approval_actions,
        rule_id=decision.rule_id + "/L1",
        reply_points=["We have everything we need. A team member will confirm and reply within 1 business day."],
        note=f"autonomy L1 for {decision.action_class}"))
