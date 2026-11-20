"""
Walk the governed write path against an in-memory store.

    python demo.py
"""

from __future__ import annotations

from approval_gate import SelfApprovalError
from governed_write import GovernedWriteService, RecordStore


def main() -> None:
    service = GovernedWriteService(
        records=RecordStore(
            [
                {
                    "record_id": "rec-acme",
                    "display_name": "Acme Limited",
                    "external_reference": "ACM-001",
                    "contact_detail": "accounts@acme.example.com",
                },
                {
                    "record_id": "rec-northwind",
                    "display_name": "Northwind Traders",
                    "external_reference": "NWT-002",
                    "contact_detail": "billing@northwind.example.com",
                },
            ]
        )
    )

    print("1. Malformed tool call — no approval is opened")
    rejected = service.submit(
        "agent-1",
        "create_contact",
        {"contact_type": "customer", "display_name": "  ", "group_id": "sales", "notes": "please"},
    )
    print(f"   blocked={rejected.blocked} reason={rejected.block_reason.value}")
    print(f"   {rejected.validation_error}")
    print(f"   actions stored: {len(service.gateway)}")

    print("\n2. Likely duplicate — blocked before an action exists")
    blocked = service.submit(
        "agent-1",
        "create_contact",
        {
            "contact_type": "customer",
            "display_name": "ACME Ltd",
            "external_reference": "ACM-001",
            "contact_detail": "Accounts@Acme.example.com",
            "group_id": "sales",
        },
    )
    match = blocked.duplicates[0]
    print(
        f"   blocked={blocked.blocked} reason={blocked.block_reason.value} action={blocked.action}"
    )
    print(f"   matched {match.record_id} at {match.confidence} ({match.band.value})")
    for note in match.notes:
        print(f"   - {note}")
    print(f"   actions stored: {len(service.gateway)}")

    print("\n3. Possible duplicate — a person still has to decide, and can see why")
    review = service.submit(
        "agent-1",
        "create_contact",
        {"contact_type": "customer", "display_name": "Northwind Trading", "group_id": "sales"},
    )
    assert review.action is not None
    print(f"   state={review.action.state.value} candidates={len(review.duplicates)}")
    print(f"   review context: {review.action.review_context['duplicate_candidates']}")

    print("\n4. Clean create — four eyes, then a single execution")
    zephyr = {
        "contact_type": "supplier",
        "display_name": "Zephyr Consulting",
        "external_reference": "ZEP-014",
        "contact_detail": "hello@zephyr.example.com",
        "group_id": "procurement",
    }
    created = service.submit("agent-1", "create_contact", zephyr)
    assert created.action is not None
    try:
        service.decide(created.action.action_id, approver_id="agent-1", approved=True)
    except SelfApprovalError as exc:
        print(f"   self-approval refused: {exc}")
    service.decide(
        created.action.action_id,
        approver_id="finance-lead",
        approved=True,
        reason="no existing supplier under this name",
    )
    done = service.execute(created.action.action_id)
    again = service.execute(created.action.action_id)
    print(f"   state={done.state.value} result_ref={done.result_ref}")
    print(f"   replay returned the same result: {again.result_ref == done.result_ref}")

    print("\n5. Submitting the same payload again does not open a second write")
    replay = service.submit("agent-1", "create_contact", zephyr)
    assert replay.action is not None
    print(
        f"   same action: {replay.action.action_id == created.action.action_id} state={replay.action.state.value}"
    )

    print("\n6. Invoice — schema normalises money and currency, then waits")
    invoice = service.submit(
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
                    "quantity": "1.5",
                    "unit_price": "19.99",
                    "tax_code": "vat",
                }
            ],
        },
    )
    assert invoice.action is not None
    print(f"   state={invoice.action.state.value} currency={invoice.action.payload['currency']}")
    print(f"   line={invoice.action.payload['lines'][0]}")
    print(f"   not executed yet; records in the store: {len(service.records)}")


if __name__ == "__main__":
    main()
