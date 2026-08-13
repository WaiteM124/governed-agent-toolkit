"""
Approval-gate state machine for agent-proposed write actions.

Generic reimplementation of a governance pattern for LLM agents that can mutate
external systems. Contains no client code, data, schemas, or identifiers.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional


class ApprovalState(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


class InvalidTransitionError(Exception):
    """Raised when a state transition isn't allowed from the current state."""


# States a proposed action is allowed to move to, from each current state.
_ALLOWED_TRANSITIONS: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.PROPOSED: {ApprovalState.AWAITING_APPROVAL},
    ApprovalState.AWAITING_APPROVAL: {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.EXPIRED,
    },
    ApprovalState.APPROVED: {ApprovalState.EXECUTED},
    ApprovalState.REJECTED: set(),
    ApprovalState.EXPIRED: set(),
    ApprovalState.EXECUTED: set(),
}


def compute_idempotency_key(actor_id: str, action_name: str, payload: dict[str, Any]) -> str:
    """
    Deterministic key for a proposed action, derived from who's asking, what they're
    asking for, and the exact payload. Two requests with the same key are the same
    logical action, even if they arrive as separate HTTP calls (e.g. a retried
    webhook, or a user double-clicking an approval button).
    """
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    raw = f"{actor_id}:{action_name}:{canonical_payload}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class ProposedAction:
    action_id: str
    actor_id: str
    action_name: str
    payload: dict[str, Any]
    idempotency_key: str
    state: ApprovalState = ApprovalState.PROPOSED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    approval_expires_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[str] = None

    def transition(self, new_state: ApprovalState, *, resolved_by: Optional[str] = None) -> None:
        allowed = _ALLOWED_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise InvalidTransitionError(
                f"Cannot move action {self.action_id} from {self.state} to {new_state}"
            )
        self.state = new_state
        if new_state in (ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED):
            self.resolved_at = datetime.now(timezone.utc)
            self.resolved_by = resolved_by

    def is_expired(self, *, now: Optional[datetime] = None) -> bool:
        if self.approval_expires_at is None:
            return False
        now = now or datetime.now(timezone.utc)
        return now > self.approval_expires_at


class ApprovalGateway:
    """
    In-memory reference implementation. A production version backs this with a
    database keyed on idempotency_key, but the state machine logic is identical.
    """

    def __init__(self, *, approval_ttl: timedelta = timedelta(minutes=30)) -> None:
        self._approval_ttl = approval_ttl
        self._actions_by_id: dict[str, ProposedAction] = {}
        self._actions_by_idempotency_key: dict[str, str] = {}

    def propose(self, actor_id: str, action_name: str, payload: dict[str, Any]) -> ProposedAction:
        idempotency_key = compute_idempotency_key(actor_id, action_name, payload)

        existing_id = self._actions_by_idempotency_key.get(idempotency_key)
        if existing_id is not None:
            return self._actions_by_id[existing_id]

        action = ProposedAction(
            action_id=str(uuid.uuid4()),
            actor_id=actor_id,
            action_name=action_name,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        self._actions_by_id[action.action_id] = action
        self._actions_by_idempotency_key[idempotency_key] = action.action_id
        return action

    def request_approval(self, action_id: str) -> ProposedAction:
        action = self._actions_by_id[action_id]
        action.transition(ApprovalState.AWAITING_APPROVAL)
        action.approval_expires_at = datetime.now(timezone.utc) + self._approval_ttl
        return action

    def resolve(self, action_id: str, *, approved: bool, resolved_by: str) -> ProposedAction:
        action = self._actions_by_id[action_id]

        if action.state == ApprovalState.AWAITING_APPROVAL and action.is_expired():
            action.transition(ApprovalState.EXPIRED)
            return action

        action.transition(
            ApprovalState.APPROVED if approved else ApprovalState.REJECTED,
            resolved_by=resolved_by,
        )
        return action

    def mark_executed(self, action_id: str) -> ProposedAction:
        action = self._actions_by_id[action_id]
        action.transition(ApprovalState.EXECUTED)
        return action