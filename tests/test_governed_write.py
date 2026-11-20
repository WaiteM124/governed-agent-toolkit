from datetime import timedelta

import pytest
from conftest import FrozenClock

from approval_gate import ApprovalGateway, ApprovalState, InvalidTransitionError, SelfApprovalError
from duplicate_detection import MatchBand
from governed_write import BlockReason, GovernedWriteService, IdempotentEchoExecutor, RecordStore

ACME = {
    "record_id": "rec-acme",
    "display_name": "Acme Limited",
    "external_reference": "ACM-001",
    "contact_detail": "accounts@acme.example.com",
}

ZEPHYR = {
    "contact_type": "supplier",
    "display_name": "Zephyr Consulting",
    "external_reference": "ZEP-014",
    "contact_detail": "hello@zephyr.example.com",
    "group_id": "procurement",
}


def service_with_acme() -> GovernedWriteService:
    return GovernedWriteService(records=RecordStore([ACME]))


def test_invalid_arguments_create_no_action():
    service = service_with_acme()
    submission = service.submit(
        "agent-1",
        "create_contact",
        {"contact_type": "customer", "display_name": "A", "group_id": "sales"},
    )
    assert submission.blocked
    assert submission.block_reason is BlockReason.SCHEMA_INVALID
    assert submission.action is None
    assert len(service.gateway) == 0


def test_likely_duplicate_is_blocked_before_an_action_exists():
    service = service_with_acme()
    submission = service.submit(
        "agent-1",
        "create_contact",
        {
            "contact_type": "customer",
            "display_name": "ACME Ltd",
            "external_reference": "ACM-001",
            "contact_detail": "accounts@acme.example.com",
            "group_id": "sales",
        },
    )
    assert submission.blocked
    assert submission.block_reason is BlockReason.LIKELY_DUPLICATE
    assert submission.action is None
    assert submission.duplicates[0].band is MatchBand.LIKELY
    assert len(service.gateway) == 0


def test_possible_duplicate_is_sent_for_approval_with_the_reason_attached():
    service = GovernedWriteService(
        records=RecordStore(
            [
                {
                    "record_id": "rec-1",
                    "display_name": "Northwind Traders",
                    "external_reference": "NWT-1",
                }
            ]
        )
    )
    submission = service.submit(
        "agent-1",
        "create_contact",
        {"contact_type": "customer", "display_name": "Northwind Trading", "group_id": "sales"},
    )
    assert not submission.blocked
    assert submission.action is not None
    assert submission.action.state is ApprovalState.AWAITING_APPROVAL
    assert submission.duplicates[0].band is MatchBand.POSSIBLE
    assert submission.action.review_context["duplicate_candidates"][0]["record_id"] == "rec-1"


def test_four_eyes_then_one_execution_and_replays_do_not_write_again():
    executor = IdempotentEchoExecutor()
    service = GovernedWriteService(records=RecordStore([ACME]), executor=executor)
    submission = service.submit("agent-1", "create_contact", ZEPHYR)
    assert submission.action is not None
    action_id = submission.action.action_id

    with pytest.raises(SelfApprovalError):
        service.decide(action_id, approver_id="agent-1", approved=True)
    with pytest.raises(InvalidTransitionError):
        service.execute(action_id)

    service.decide(action_id, approver_id="finance-lead", approved=True, reason="new supplier")
    first = service.execute(action_id)
    second = service.execute(action_id)
    assert first.state is ApprovalState.EXECUTED
    assert second.result_ref == first.result_ref
    assert executor.calls == [action_id]
    assert len(service.records) == 2

    replay = service.submit("agent-1", "create_contact", ZEPHYR)
    assert replay.action is not None
    assert replay.action.action_id == action_id
    assert replay.blocked is False
    assert len(service.records) == 2


def test_executor_failure_leaves_the_action_approved_for_a_retry():
    class Flaky:
        def __init__(self) -> None:
            self.attempts = 0

        def __call__(self, action) -> str:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("erp timeout")
            return "rec-remote"

    executor = Flaky()
    service = GovernedWriteService(executor=executor)
    submission = service.submit("agent-1", "create_contact", ZEPHYR)
    assert submission.action is not None
    service.decide(submission.action.action_id, approver_id="finance-lead", approved=True)

    with pytest.raises(RuntimeError):
        service.execute(submission.action.action_id)
    assert service.gateway.get(submission.action.action_id).state is ApprovalState.APPROVED
    assert len(service.records) == 0

    done = service.execute(submission.action.action_id)
    assert done.result_ref == "rec-remote"
    assert executor.attempts == 2
    assert len(service.records) == 1


def test_invoice_waits_for_approval_and_does_not_need_a_duplicate_search():
    service = service_with_acme()
    submission = service.submit(
        "agent-1",
        "create_invoice",
        {
            "contact_id": "rec-acme",
            "invoice_date": "2026-04-01",
            "due_date": "2026-04-15",
            "currency": "gbp",
            "lines": [
                {
                    "description": "Retainer",
                    "quantity": "2",
                    "unit_price": "10.00",
                    "tax_code": "vat",
                }
            ],
        },
    )
    assert submission.action is not None
    assert submission.duplicates == ()
    assert submission.action.payload["currency"] == "GBP"
    assert len(service.records) == 1


def test_a_replay_after_the_window_opens_a_new_decision():
    clock = FrozenClock()
    gateway = ApprovalGateway(approval_ttl=timedelta(minutes=30), clock=clock)
    service = GovernedWriteService(gateway=gateway, records=RecordStore([ACME]))
    first = service.submit("agent-1", "create_contact", ZEPHYR)
    assert first.action is not None

    clock.now += timedelta(minutes=30)
    retried = service.submit("agent-1", "create_contact", ZEPHYR)
    assert retried.action is not None
    assert retried.action.action_id != first.action.action_id
    assert gateway.get(first.action.action_id).state is ApprovalState.EXPIRED
