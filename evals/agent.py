"""Scripted / gullible agent: obeys an ``<attack>`` block in untrusted content.

Stand-in for a model that follows injected instructions. Deterministic; no API key.
Mirrors the control used by agent-guard's red-team gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from evals.harness import CallOutcome, EvalHarness

_ATTACK = re.compile(r"<attack>(.*?)</attack>", re.DOTALL)


@dataclass(frozen=True)
class Case:
    """One eval case loaded from ``evals/cases/``."""

    id: str
    title: str
    category: str
    expected: str
    task: str
    untrusted_content: str
    seed_records: tuple[dict[str, Any], ...] = ()
    human_approver: str | None = None


def attack_payload(content: str) -> dict[str, Any] | None:
    """Parse the injected JSON object from an ``<attack>`` block, if present."""
    match = _ATTACK.search(content)
    if match is None:
        return None
    loaded = json.loads(match.group(1))
    if not isinstance(loaded, dict):
        raise ValueError("attack block must be a JSON object")
    return loaded


def attack_block(payload: dict[str, Any]) -> str:
    """Serialize a payload into the ``<attack>`` form used by cases."""
    return "<attack>" + json.dumps(payload, separators=(",", ":"), sort_keys=True) + "</attack>"


class GullibleAgent:
    """Follows the ``<attack>`` block exactly. Actor id is fixed by the harness."""

    def run(self, case: Case, harness: EvalHarness) -> list[CallOutcome]:
        payload = attack_payload(case.untrusted_content)
        if payload is None:
            return []

        outcomes: list[CallOutcome] = []
        if payload.get("clear_audit") is True:
            outcomes.append(harness.clear_audit())

        calls = payload.get("calls", [])
        if not isinstance(calls, list):
            return outcomes

        last_action_id: str | None = None
        for call in calls:
            if not isinstance(call, dict):
                continue
            tool = call.get("tool")
            arguments = call.get("arguments", {})
            if not isinstance(tool, str) or not isinstance(arguments, dict):
                continue
            result = harness.call(tool, arguments)
            outcomes.append(result)
            if result.action_id is not None:
                last_action_id = result.action_id

        action_id = last_action_id
        if action_id is None:
            return outcomes

        if payload.get("approve_as_self") is True:
            outcomes.append(harness.approve_as_self(action_id))
        elif isinstance(payload.get("approve_as"), str):
            outcomes.append(harness.approve_as(action_id, payload["approve_as"]))

        if payload.get("execute") is True:
            outcomes.append(harness.execute(action_id))

        return outcomes
