from decimal import Decimal

import pytest

from tool_schemas import (
    CreateInvoiceRequest,
    ToolValidationError,
    UnknownToolError,
    validate_tool_call,
)

CONTACT = {
    "contact_type": "supplier",
    "display_name": "  Zephyr Consulting  ",
    "external_reference": "ZEP-014",
    "contact_detail": "Hello@Zephyr.example.com",
    "group_id": "procurement",
}


def test_contact_arguments_are_normalised():
    parsed = validate_tool_call("create_contact", CONTACT)
    dumped = parsed.model_dump(mode="json")
    assert dumped["display_name"] == "Zephyr Consulting"
    assert dumped["contact_detail"] == "hello@zephyr.example.com"


def test_blank_optional_fields_become_absent():
    arguments = {**CONTACT, "external_reference": "  ", "contact_detail": ""}
    parsed = validate_tool_call("create_contact", arguments)
    assert parsed.external_reference is None
    assert parsed.contact_detail is None


def test_malformed_contact_returns_located_issues_and_unknown_tools_raise():
    with pytest.raises(ToolValidationError) as caught:
        validate_tool_call(
            "create_contact",
            {"contact_type": "customer", "display_name": " ", "group_id": "sales", "notes": "x"},
        )
    locations = {issue["loc"] for issue in caught.value.issues}
    assert "display_name" in locations
    assert "notes" in locations

    with pytest.raises(ToolValidationError) as phone:
        validate_tool_call("create_contact", {**CONTACT, "contact_detail": "call me"})
    assert phone.value.issues[0]["loc"] == "contact_detail"

    with pytest.raises(UnknownToolError):
        validate_tool_call("drop_database", {})


def test_invoice_money_is_decimal_and_floats_are_rejected():
    parsed = validate_tool_call(
        "create_invoice",
        {
            "contact_id": "rec-acme",
            "invoice_date": "2026-04-01",
            "due_date": "2026-04-01",
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
    assert isinstance(parsed, CreateInvoiceRequest)
    assert parsed.currency == "GBP"
    assert parsed.lines[0].tax_code == "VAT"
    assert parsed.lines[0].unit_price == Decimal("19.99")
    assert parsed.total == Decimal("29.99")

    with pytest.raises(ToolValidationError) as caught:
        validate_tool_call(
            "create_invoice",
            {
                "contact_id": "rec-acme",
                "invoice_date": "2026-04-02",
                "due_date": "2026-04-01",
                "currency": "GBP",
                "lines": [
                    {
                        "description": "Retainer",
                        "quantity": 1.5,
                        "unit_price": "10.00",
                        "tax_code": "VAT",
                    }
                ],
            },
        )
    assert any(issue["loc"] == "due_date" for issue in caught.value.issues)
    assert any(issue["loc"] == "lines.0.quantity" for issue in caught.value.issues)
