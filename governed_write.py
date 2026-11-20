"""
Composed write path: validate, replay, deduplicate, approve, execute.

Schema checks, the approval gate, and duplicate detection each enforce one
rule. This module is the order those rules run in. That order is what keeps a
write from landing twice, or landing without a person having seen it.

Generic reimplementation. Contains no client code, data, schemas, or identifiers.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from approval_gate import (
    ApprovalGateway,
    ApprovalState,
    InvalidTransitionError,
    ProposedAction,
)
from duplicate_detection import MatchBand, MatchCandidate, find_candidates
from tool_schemas import ToolValidationError, validate_tool_call

# Tools that insert master data. Everything else still needs a schema and an
# approval; it does not need a duplicate search.
DUPLICATE_CHECKED_TOOLS = frozenset({"create_contact"})


class BlockReason(str, Enum):
    SCHEMA_INVALID = "schema_invalid"
    LIKELY_DUPLICATE = "likely_duplicate"


@dataclass(frozen=True)
class Submission:
    """
    What submit() decided.

    A blocked submission has no action. There is nothing for a later approval
    click to execute. A submission with an action is inside the state machine,
    including when this call was an idempotent replay of one already there.
    """

    action: ProposedAction | None
    duplicates: tuple[MatchCandidate, ...]
    blocked: bool
    block_reason: BlockReason | None
    validation_error: ToolValidationError | None = None


class RecordStore:
    """In-memory stand-in for the records duplicate detection reads."""

    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self._records: list[dict[str, Any]] = []
        for record in records or []:
            self.add(record)

    def all(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records]

    def add(self, record: dict[str, Any]) -> None:
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError("record_id is required")
        if any(existing["record_id"] == record_id for existing in self._records):
            return
        self._records.append(dict(record))

    def __len__(self) -> int:
        return len(self._records)


class IdempotentEchoExecutor:
    """
    Stand-in for the ERP / CRM client.

    The contract a real client has to meet: the side effect is keyed by
    action.action_id, and a second call returns the original remote id. The
    service calls the executor before it marks the action executed, so a crash
    in between is recovered by retrying execute(), not by creating a new action.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._results: dict[str, str] = {}

    def __call__(self, action: ProposedAction) -> str:
        self.calls.append(action.action_id)
        cached = self._results.get(action.action_id)
        if cached is None:
            cached = str(uuid.uuid4())
            self._results[action.action_id] = cached
        return cached


def _candidate_context(candidate: MatchCandidate) -> dict[str, Any]:
    return {
        "record_id": candidate.record_id,
        "confidence": candidate.confidence,
        "band": candidate.band.value,
        "matched_fields": dict(candidate.matched_fields),
        "notes": list(candidate.notes),
    }


class GovernedWriteService:
    """
    One write, in the only order that holds:

    1. Schema. Invalid arguments create no governance state.
    2. Idempotent replay. An action already in flight or already executed is
       returned before duplicate detection runs. Otherwise the record this
       action just created would look like a duplicate of itself on retry.
    3. Duplicates, for record-creating tools. LIKELY blocks, and no action is
       created. POSSIBLE is attached as review context and still needs a person.
    4. Approval. The proposer cannot be the approver.
    5. Execute. The downstream call happens, then the action is marked executed.
    """

    def __init__(
        self,
        *,
        gateway: ApprovalGateway | None = None,
        records: RecordStore | None = None,
        executor: Callable[[ProposedAction], str] | None = None,
        review_threshold: float = 0.72,
        block_threshold: float = 0.90,
    ) -> None:
        if not 0 <= review_threshold <= block_threshold <= 1:
            raise ValueError(
                "Thresholds must satisfy 0 <= review_threshold <= block_threshold <= 1"
            )
        self.gateway = gateway if gateway is not None else ApprovalGateway()
        self.records = records if records is not None else RecordStore()
        self._executor = executor if executor is not None else IdempotentEchoExecutor()
        self._review_threshold = review_threshold
        self._block_threshold = block_threshold
        self._lock = threading.RLock()

    def submit(self, actor_id: str, tool_name: str, arguments: dict[str, Any]) -> Submission:
        # Unknown tools raise: that is a wiring error. Invalid arguments return
        # a blocked submission so the agent loop can repair the call and retry.
        try:
            validated = validate_tool_call(tool_name, arguments)
        except ToolValidationError as exc:
            return Submission(None, (), True, BlockReason.SCHEMA_INVALID, exc)

        payload = validated.model_dump(mode="json")

        with self._lock:
            existing = self.gateway.find_live(actor_id, tool_name, payload)
            if existing is not None:
                if existing.state == ApprovalState.PROPOSED:
                    self.gateway.request_approval(existing.action_id)
                return Submission(existing, (), False, None, None)

            duplicates: list[MatchCandidate] = []
            if tool_name in DUPLICATE_CHECKED_TOOLS:
                duplicates = find_candidates(
                    payload,
                    self.records.all(),
                    threshold=self._review_threshold,
                    likely_threshold=self._block_threshold,
                )
                if any(candidate.band is MatchBand.LIKELY for candidate in duplicates):
                    return Submission(
                        None, tuple(duplicates), True, BlockReason.LIKELY_DUPLICATE, None
                    )

            action = self.gateway.propose(
                actor_id,
                tool_name,
                payload,
                review_context={
                    "duplicate_candidates": [_candidate_context(c) for c in duplicates]
                },
            )
            if action.state == ApprovalState.PROPOSED:
                self.gateway.request_approval(action.action_id)
            return Submission(action, tuple(duplicates), False, None, None)

    def decide(
        self,
        action_id: str,
        *,
        approver_id: str,
        approved: bool,
        reason: str | None = None,
    ) -> ProposedAction:
        return self.gateway.resolve(
            action_id,
            approved=approved,
            resolved_by=approver_id,
            reason=reason,
        )

    def execute(self, action_id: str) -> ProposedAction:
        """
        Run an approved action.

        Already-executed actions return immediately, without calling the executor
        again. Anything else that is not APPROVED is refused. If the executor
        raises, the action stays APPROVED and a later execute() retries it.
        """
        with self._lock:
            action = self.gateway.get(action_id)
            if action.state is ApprovalState.EXECUTED:
                return action
            if action.state is not ApprovalState.APPROVED:
                raise InvalidTransitionError(
                    f"Cannot execute action {action_id} from {action.state.value}; "
                    "only an approved action can be executed"
                )
            result_ref = self._executor(action)
            if action.action_name == "create_contact":
                self.records.add({"record_id": result_ref, **action.payload})
            return self.gateway.mark_executed(action_id, result_ref=result_ref)
