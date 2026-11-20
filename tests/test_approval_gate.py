from datetime import datetime, timedelta, timezone

import pytest
from conftest import FrozenClock

from approval_gate import (
    ActionNotFoundError,
    ApprovalGateway,
    ApprovalState,
    InvalidTransitionError,
    PayloadNotSerializableError,
    SelfApprovalError,
    compute_idempotency_key,
)


def _awaiting(gateway: ApprovalGateway, **payload: object):
    action = gateway.propose("agent-1", "create_contact", payload or {"name": "Acme"})
    return gateway.request_approval(action.action_id)


def test_propose_then_approve_then_execute():
    gateway = ApprovalGateway()
    action = gateway.propose("agent-1", "create_contact", {"name": "Acme"})
    assert action.state is ApprovalState.PROPOSED

    gateway.request_approval(action.action_id)
    assert action.state is ApprovalState.AWAITING_APPROVAL
    assert action.approval_expires_at is not None

    gateway.resolve(
        action.action_id, approved=True, resolved_by="finance-lead", reason="new customer"
    )
    assert action.state is ApprovalState.APPROVED
    assert action.resolved_by == "finance-lead"
    assert action.resolution_reason == "new customer"

    gateway.mark_executed(action.action_id, result_ref="rec-9")
    assert action.state is ApprovalState.EXECUTED
    assert action.result_ref == "rec-9"
    assert [step.to_state for step in action.history] == [
        ApprovalState.PROPOSED,
        ApprovalState.AWAITING_APPROVAL,
        ApprovalState.APPROVED,
        ApprovalState.EXECUTED,
    ]


def test_rejected_action_cannot_be_executed():
    gateway = ApprovalGateway()
    action = _awaiting(gateway)
    gateway.resolve(action.action_id, approved=False, resolved_by="finance-lead")
    assert action.state is ApprovalState.REJECTED

    with pytest.raises(InvalidTransitionError):
        gateway.mark_executed(action.action_id, result_ref="rec-9")


def test_proposer_cannot_approve_their_own_action_but_can_withdraw_it():
    gateway = ApprovalGateway()
    action = _awaiting(gateway)

    with pytest.raises(SelfApprovalError):
        gateway.resolve(action.action_id, approved=True, resolved_by="agent-1")
    assert action.state is ApprovalState.AWAITING_APPROVAL

    gateway.resolve(action.action_id, approved=False, resolved_by="agent-1", reason="withdrawn")
    assert action.state is ApprovalState.REJECTED
    assert action.resolved_by == "agent-1"


def test_duplicate_proposal_returns_the_same_action():
    gateway = ApprovalGateway()
    payload = {"name": "Acme", "amount": 100}
    first = gateway.propose("agent-1", "create_invoice", payload)
    second = gateway.propose("agent-1", "create_invoice", {"amount": 100, "name": "Acme"})
    assert first.action_id == second.action_id
    assert compute_idempotency_key("agent-1", "create_invoice", payload) == first.idempotency_key


def test_different_actor_or_tool_is_a_different_action():
    gateway = ApprovalGateway()
    payload = {"name": "Acme", "amount": 100}
    first = gateway.propose("agent-1", "create_invoice", payload)
    other_actor = gateway.propose("agent-2", "create_invoice", payload)
    other_tool = gateway.propose("agent-1", "create_contact", payload)
    assert len({first.action_id, other_actor.action_id, other_tool.action_id}) == 3


def test_rejected_or_expired_key_can_be_proposed_again_but_executed_cannot():
    clock = FrozenClock()
    gateway = ApprovalGateway(approval_ttl=timedelta(minutes=30), clock=clock)
    payload = {"name": "Acme"}

    rejected = _awaiting(gateway, **payload)
    gateway.resolve(rejected.action_id, approved=False, resolved_by="finance-lead")
    retried = gateway.propose("agent-1", "create_contact", payload)
    assert retried.action_id != rejected.action_id
    assert gateway.get(rejected.action_id).state is ApprovalState.REJECTED

    gateway.request_approval(retried.action_id)
    clock.now += timedelta(minutes=31)
    after_expiry = gateway.propose("agent-1", "create_contact", payload)
    assert gateway.get(retried.action_id).state is ApprovalState.EXPIRED
    assert after_expiry.action_id != retried.action_id

    live = gateway.request_approval(after_expiry.action_id)
    gateway.resolve(live.action_id, approved=True, resolved_by="finance-lead")
    gateway.mark_executed(live.action_id, result_ref="rec-1")
    replay = gateway.propose("agent-1", "create_contact", payload)
    assert replay.action_id == live.action_id
    assert replay.state is ApprovalState.EXECUTED


def test_unanswered_approval_expires_instead_of_being_approved_late():
    clock = FrozenClock()
    gateway = ApprovalGateway(approval_ttl=timedelta(minutes=30), clock=clock)
    action = _awaiting(gateway)
    deadline = action.approval_expires_at

    clock.now += timedelta(minutes=10)
    gateway.request_approval(action.action_id)
    assert action.approval_expires_at == deadline

    clock.now = deadline
    resolved = gateway.resolve(action.action_id, approved=True, resolved_by="finance-lead")
    assert resolved.state is ApprovalState.EXPIRED
    assert resolved.resolution_reason == "approval window elapsed"


def test_expire_stale_sweeps_only_unanswered_approvals():
    clock = FrozenClock()
    gateway = ApprovalGateway(approval_ttl=timedelta(minutes=30), clock=clock)
    waiting = _awaiting(gateway, name="Acme")
    approved = gateway.propose("agent-1", "create_invoice", {"name": "Other"})
    gateway.request_approval(approved.action_id)
    gateway.resolve(approved.action_id, approved=True, resolved_by="finance-lead")

    clock.now += timedelta(minutes=31)
    expired = gateway.expire_stale()
    assert [action.action_id for action in expired] == [waiting.action_id]
    assert approved.state is ApprovalState.APPROVED


def test_payload_and_review_context_are_copied():
    gateway = ApprovalGateway()
    payload = {"name": "Acme"}
    context = {"duplicate_candidates": []}
    action = gateway.propose("agent-1", "create_contact", payload, review_context=context)
    payload["name"] = "Changed"
    context["duplicate_candidates"].append({"record_id": "rec-1"})

    assert action.payload == {"name": "Acme"}
    assert action.review_context == {"duplicate_candidates": []}

    replay = gateway.propose(
        "agent-1",
        "create_contact",
        {"name": "Acme"},
        review_context={"duplicate_candidates": [{"record_id": "rec-2"}]},
    )
    assert replay.action_id == action.action_id
    assert replay.review_context == {"duplicate_candidates": []}


def test_unknown_action_and_unserialisable_payload():
    gateway = ApprovalGateway()
    with pytest.raises(ActionNotFoundError):
        gateway.get("missing")
    with pytest.raises(PayloadNotSerializableError):
        gateway.propose("agent-1", "create_contact", {"when": datetime.now(timezone.utc)})


def test_cannot_skip_the_approval_state():
    gateway = ApprovalGateway()
    action = gateway.propose("agent-1", "create_contact", {"name": "Acme"})
    with pytest.raises(InvalidTransitionError):
        gateway.resolve(action.action_id, approved=True, resolved_by="finance-lead")
    with pytest.raises(InvalidTransitionError):
        gateway.mark_executed(action.action_id, result_ref="rec-1")
    assert action.state is ApprovalState.PROPOSED
