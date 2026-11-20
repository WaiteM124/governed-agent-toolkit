"""
Approval-gate state machine for agent-proposed write actions.

A tool call is not a transaction. The model can ask for a write; only this
state machine can say the write is allowed to happen, and only an executor
that then reports back can say it did happen.

Generic reimplementation of a governance pattern. Contains no client code,
data, schemas, or identifiers.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional


class ApprovalState(str, Enum):
    PROPOSED = "proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"


class GovernanceError(Exception):
    """Expected failure of a governance rule, as opposed to a programming error."""


class InvalidTransitionError(GovernanceError):
    """Raised when a state transition is not allowed from the current state."""


class ActionNotFoundError(GovernanceError):
    """Raised when an action id is not in the gateway."""

    def __init__(self, action_id: str) -> None:
        self.action_id = action_id
        super().__init__(f"No action with id {action_id}")


class SelfApprovalError(GovernanceError):
    """Raised when the actor who proposed a write tries to approve it."""

    def __init__(self, action_id: str, actor_id: str) -> None:
        self.action_id = action_id
        self.actor_id = actor_id
        super().__init__(
            f"Actor {actor_id} cannot approve their own action {action_id}"
        )


class PayloadNotSerializableError(GovernanceError):
    """Raised when a payload cannot be canonicalised into a stable idempotency key."""


# States a proposed action is allowed to move to, from each current state.
# Terminal states have nowhere to go: a rejected, expired, or executed action
# stays that way, and a replay has to come back through propose().
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

# A live action consumes the idempotency key. A rejected or expired one does not:
# the business write never happened, so a genuine retry is a new decision.
_KEY_HOLDING_STATES = {
    ApprovalState.PROPOSED,
    ApprovalState.AWAITING_APPROVAL,
    ApprovalState.APPROVED,
    ApprovalState.EXECUTED,
}

Clock = Callable[[], datetime]


def _require_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _canonical_json(payload: dict[str, Any]) -> str:
    """Stable encoding. Key order and insignificant whitespace do not change the key."""
    if not isinstance(payload, dict):
        raise PayloadNotSerializableError("Payload must be a JSON object")
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except TypeError as exc:
        raise PayloadNotSerializableError(
            "Payload must be JSON-serialisable so the idempotency key is stable"
        ) from exc


def compute_idempotency_key(actor_id: str, action_name: str, payload: dict[str, Any]) -> str:
    """
    Key for one logical write: who is asking, which tool, and the exact payload.

    Two calls with the same key are the same action, even when they arrive as a
    retried request or a double-clicked approval. The payload is canonicalised
    before hashing, so {"b": 1, "a": 2} and {"a": 2, "b": 1} collide on purpose.
    """
    raw = f"{actor_id}:{action_name}:{_canonical_json(payload)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TransitionRecord:
    """One row of the append-only history on an action."""

    from_state: Optional[ApprovalState]
    to_state: ApprovalState
    at: datetime
    actor: Optional[str]
    reason: Optional[str] = None


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
    resolution_reason: Optional[str] = None
    executed_at: Optional[datetime] = None
    result_ref: Optional[str] = None
    # Shown to the approver. Deliberately excluded from the idempotency key:
    # a changing candidate list must not look like a different business write.
    review_context: dict[str, Any] = field(default_factory=dict)
    history: list[TransitionRecord] = field(default_factory=list)

    def transition(
        self,
        new_state: ApprovalState,
        *,
        now: datetime,
        actor: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> None:
        allowed = _ALLOWED_TRANSITIONS[self.state]
        if new_state not in allowed:
            allowed_text = ", ".join(sorted(state.value for state in allowed)) or "none"
            raise InvalidTransitionError(
                f"Cannot move action {self.action_id} from {self.state.value} "
                f"to {new_state.value}; allowed: {allowed_text}"
            )
        previous = self.state
        self.state = new_state
        if new_state in (ApprovalState.APPROVED, ApprovalState.REJECTED, ApprovalState.EXPIRED):
            self.resolved_at = now
            self.resolved_by = actor
            self.resolution_reason = reason
        self.history.append(
            TransitionRecord(
                from_state=previous,
                to_state=new_state,
                at=now,
                actor=actor,
                reason=reason,
            )
        )

    def is_expired(self, now: datetime) -> bool:
        """True at and after approval_expires_at. An action with no deadline is open."""
        if self.approval_expires_at is None:
            return False
        return now >= self.approval_expires_at


class ApprovalGateway:
    """
    In-memory reference implementation.

    A production gateway stores the same states and the same key, backed by a
    database with a unique constraint on idempotency_key. The legal transitions
    do not change when the storage does.

    The approval window bounds how long a person has to answer. It does not
    bound execution: once someone has approved, a slow executor must not expire
    that decision out from under them.
    """

    def __init__(
        self,
        *,
        approval_ttl: timedelta = timedelta(minutes=30),
        clock: Optional[Clock] = None,
    ) -> None:
        if approval_ttl <= timedelta(0):
            raise ValueError("approval_ttl must be positive")
        self._approval_ttl = approval_ttl
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._actions_by_id: dict[str, ProposedAction] = {}
        self._actions_by_idempotency_key: dict[str, str] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._actions_by_id)

    def get(self, action_id: str) -> ProposedAction:
        with self._lock:
            return self._require(action_id)

    def find_live(self, actor_id: str, action_name: str, payload: dict[str, Any]) -> Optional[ProposedAction]:
        """
        Return the action already holding this idempotency key, if any.

        Expired approvals are moved to EXPIRED as a side effect, which releases
        the key. Callers that want to block duplicates before creating an action
        (see GovernedWriteService) use this so a replay of an executed write is
        not mistaken for a new one.
        """
        actor_id = _require_text(actor_id, "actor_id")
        action_name = _require_text(action_name, "action_name")
        key = compute_idempotency_key(actor_id, action_name, payload)
        with self._lock:
            return self._live_action(key, self._now())

    def propose(
        self,
        actor_id: str,
        action_name: str,
        payload: dict[str, Any],
        *,
        review_context: Optional[dict[str, Any]] = None,
    ) -> ProposedAction:
        actor_id = _require_text(actor_id, "actor_id")
        action_name = _require_text(action_name, "action_name")
        encoded = _canonical_json(payload)
        frozen_payload = json.loads(encoded)
        key = compute_idempotency_key(actor_id, action_name, frozen_payload)
        context = json.loads(json.dumps(review_context or {}))

        with self._lock:
            now = self._now()
            existing = self._live_action(key, now)
            if existing is not None:
                return existing

            action = ProposedAction(
                action_id=str(uuid.uuid4()),
                actor_id=actor_id,
                action_name=action_name,
                payload=frozen_payload,
                idempotency_key=key,
                created_at=now,
                review_context=context,
            )
            action.history.append(
                TransitionRecord(
                    from_state=None,
                    to_state=ApprovalState.PROPOSED,
                    at=now,
                    actor=actor_id,
                    reason="proposed",
                )
            )
            self._actions_by_id[action.action_id] = action
            self._actions_by_idempotency_key[key] = action.action_id
            return action

    def request_approval(self, action_id: str) -> ProposedAction:
        """
        Move PROPOSED to AWAITING_APPROVAL and start the timeout.

        A second call while the request is still open returns the same action
        and does not extend the deadline. A second call after the deadline
        expires the action and returns it; the caller has to propose again.
        """
        with self._lock:
            now = self._now()
            action = self._require(action_id)
            if action.state == ApprovalState.AWAITING_APPROVAL:
                if action.is_expired(now):
                    action.transition(
                        ApprovalState.EXPIRED,
                        now=now,
                        reason="approval window elapsed",
                    )
                return action
            action.transition(
                ApprovalState.AWAITING_APPROVAL,
                now=now,
                reason="submitted for approval",
            )
            action.approval_expires_at = now + self._approval_ttl
            return action

    def resolve(
        self,
        action_id: str,
        *,
        approved: bool,
        resolved_by: str,
        reason: Optional[str] = None,
    ) -> ProposedAction:
        """
        Record a human decision.

        The proposer may reject their own request (withdraw) but may not approve
        it. A decision that arrives at or after the deadline expires the action
        instead of approving it, whoever sent it.
        """
        resolved_by = _require_text(resolved_by, "resolved_by")
        if reason is not None:
            reason = _require_text(reason, "reason")

        with self._lock:
            now = self._now()
            action = self._require(action_id)
            if action.state == ApprovalState.AWAITING_APPROVAL and action.is_expired(now):
                action.transition(
                    ApprovalState.EXPIRED,
                    now=now,
                    reason="approval window elapsed",
                )
                return action
            if approved and resolved_by == action.actor_id and action.state == ApprovalState.AWAITING_APPROVAL:
                raise SelfApprovalError(action.action_id, action.actor_id)
            action.transition(
                ApprovalState.APPROVED if approved else ApprovalState.REJECTED,
                now=now,
                actor=resolved_by,
                reason=reason,
            )
            return action

    def mark_executed(self, action_id: str, *, result_ref: str) -> ProposedAction:
        """Record that the downstream system accepted the write. result_ref is its id."""
        result_ref = _require_text(result_ref, "result_ref")
        with self._lock:
            now = self._now()
            action = self._require(action_id)
            action.transition(ApprovalState.EXECUTED, now=now, reason="executed")
            action.executed_at = now
            action.result_ref = result_ref
            return action

    def expire_stale(self) -> list[ProposedAction]:
        """Sweep unanswered approvals. Safe to run on a timer; also happens lazily."""
        with self._lock:
            now = self._now()
            expired: list[ProposedAction] = []
            for action in self._actions_by_id.values():
                if action.state == ApprovalState.AWAITING_APPROVAL and action.is_expired(now):
                    action.transition(
                        ApprovalState.EXPIRED,
                        now=now,
                        reason="approval window elapsed",
                    )
                    expired.append(action)
            return expired

    def _live_action(self, key: str, now: datetime) -> Optional[ProposedAction]:
        existing_id = self._actions_by_idempotency_key.get(key)
        if existing_id is None:
            return None
        existing = self._actions_by_id[existing_id]
        if existing.state == ApprovalState.AWAITING_APPROVAL and existing.is_expired(now):
            existing.transition(
                ApprovalState.EXPIRED,
                now=now,
                reason="approval window elapsed",
            )
        if existing.state not in _KEY_HOLDING_STATES:
            return None
        return existing

    def _require(self, action_id: str) -> ProposedAction:
        try:
            return self._actions_by_id[action_id]
        except KeyError:
            raise ActionNotFoundError(action_id) from None

    def _now(self) -> datetime:
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("Clock must return timezone-aware datetimes")
        return current
