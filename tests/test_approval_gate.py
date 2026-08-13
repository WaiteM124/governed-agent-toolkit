import pytest

from approval_gate import ApprovalGateway, ApprovalState, InvalidTransitionError


def test_propose_then_approve_then_execute():
    gateway = ApprovalGateway()
    action = gateway.propose("user-1", "create_contact", {"name": "Acme"})
    assert action.state == ApprovalState.PROPOSED

    gateway.request_approval(action.action_id)
    assert action.state == ApprovalState.AWAITING_APPROVAL
    assert action.approval_expires_at is not None

    gateway.resolve(action.action_id, approved=True, resolved_by="approver-1")
    assert action.state == ApprovalState.APPROVED
    assert action.resolved_by == "approver-1"

    gateway.mark_executed(action.action_id)
    assert action.state == ApprovalState.EXECUTED


def test_rejected_action_cannot_be_executed():
    gateway = ApprovalGateway()
    action = gateway.propose("user-1", "create_contact", {"name": "Acme"})
    gateway.request_approval(action.action_id)
    gateway.resolve(action.action_id, approved=False, resolved_by="approver-1")
    assert action.state == ApprovalState.REJECTED

    with pytest.raises(InvalidTransitionError):
        gateway.mark_executed(action.action_id)


def test_duplicate_proposal_returns_same_action():
    gateway = ApprovalGateway()
    payload = {"name": "Acme", "amount": 100}
    first = gateway.propose("user-1", "create_invoice", payload)
    second = gateway.propose("user-1", "create_invoice", payload)
    assert first.action_id == second.action_id


def test_different_actor_same_payload_is_a_different_action():
    gateway = ApprovalGateway()
    payload = {"name": "Acme", "amount": 100}
    first = gateway.propose("user-1", "create_invoice", payload)
    second = gateway.propose("user-2", "create_invoice", payload)
    assert first.action_id != second.action_id