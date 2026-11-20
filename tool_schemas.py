"""
Tool-call schema contracts for a generic agentic write integration.

Every write tool is a Pydantic model. Arguments are validated against it before
any governance state is created, so a malformed tool call never opens an
approval and never reaches a downstream system.

Generic reimplementation. Contains no client code, data, schemas, or identifiers.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ContactType(str, Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class ToolModel(BaseModel):
    """Write-tool arguments. Unknown fields are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _reject_float(value: Any) -> Any:
    # 0.1 + 0.2 is not 0.3. Invoice amounts have to arrive as strings or integers.
    if isinstance(value, float):
        raise ValueError(
            "Use a string or integer so the amount does not pick up binary float error"
        )
    return value


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}$")


class CreateContactRequest(ToolModel):
    """Create a customer or supplier. Field names match the duplicate scorer."""

    contact_type: ContactType
    display_name: str = Field(..., min_length=2, max_length=200)
    external_reference: str | None = Field(default=None, max_length=50)
    contact_detail: str | None = Field(
        default=None, description="Primary email or phone number for this contact."
    )
    group_id: str = Field(..., min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)

    @field_validator("external_reference", "contact_detail", mode="before")
    @classmethod
    def blank_optional_strings(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("external_reference")
    @classmethod
    def reference_characters(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,49}", value):
            raise ValueError("external_reference must be letters, digits, and . _ / -")
        return value

    @field_validator("contact_detail")
    @classmethod
    def email_or_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if "@" in value:
            if _EMAIL.fullmatch(value) is None:
                raise ValueError("contact_detail must be an email address or a phone number")
            return value.lower()
        digits = re.sub(r"[^\d+]", "", value)
        national = re.sub(r"\D", "", digits)
        if not 7 <= len(national) <= 15:
            raise ValueError("contact_detail must be an email address or a phone number")
        return digits


class InvoiceLine(ToolModel):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: Decimal = Field(..., gt=0, max_digits=12, decimal_places=4)
    unit_price: Decimal = Field(..., ge=0, max_digits=12, decimal_places=2)
    tax_code: str = Field(..., min_length=1, max_length=20)

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def money_is_not_a_float(cls, value: Any) -> Any:
        return _reject_float(value)

    @field_validator("tax_code", mode="before")
    @classmethod
    def normalise_tax_code(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("tax_code")
    @classmethod
    def tax_code_characters(cls, value: str) -> str:
        if re.fullmatch(r"[A-Z0-9_-]+", value) is None:
            raise ValueError("tax_code must be uppercase letters, digits, '_' or '-'")
        return value

    @property
    def line_total(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class CreateInvoiceRequest(ToolModel):
    """Create an invoice against an existing contact. Amounts are decimal strings."""

    contact_id: str = Field(..., min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    invoice_date: date
    due_date: date
    currency: str = Field(..., min_length=3, max_length=3)
    reference: str | None = Field(default=None, max_length=50)
    lines: list[InvoiceLine] = Field(..., min_length=1, max_length=100)

    @field_validator("currency", mode="before")
    @classmethod
    def normalise_currency(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        if re.fullmatch(r"[A-Z]{3}", value) is None:
            raise ValueError("currency must be a 3-letter ISO code")
        return value

    @field_validator("reference", mode="before")
    @classmethod
    def blank_reference(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("due_date")
    @classmethod
    def due_date_not_before_invoice_date(cls, value: date, info: Any) -> date:
        invoice_date = info.data.get("invoice_date") if hasattr(info, "data") else None
        if invoice_date is not None and value < invoice_date:
            raise ValueError("due_date cannot be before invoice_date")
        return value

    @property
    def total(self) -> Decimal:
        return sum((line.line_total for line in self.lines), Decimal("0.00"))


# Adding a write tool starts here. Record-creating tools also need a duplicate
# policy in governed_write.DUPLICATE_CHECKED_TOOLS.
TOOL_SCHEMAS: dict[str, type[ToolModel]] = {
    "create_contact": CreateContactRequest,
    "create_invoice": CreateInvoiceRequest,
}


class UnknownToolError(Exception):
    """The caller asked for a tool this process does not implement."""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        self.known = sorted(TOOL_SCHEMAS)
        known = ", ".join(self.known)
        super().__init__(f"Unknown tool {tool_name!r}; known tools: {known}")


class ToolValidationError(Exception):
    """
    The model produced arguments that do not match the tool contract.

    `issues` is shaped for an agent retry: each item has a location and a message,
    with no pydantic objects attached.
    """

    def __init__(self, tool_name: str, issues: list[dict[str, Any]]) -> None:
        self.tool_name = tool_name
        self.issues = issues
        detail = "; ".join(f"{issue['loc']}: {issue['message']}" for issue in issues)
        super().__init__(f"{tool_name} rejected: {detail}")


def validate_tool_call(tool_name: str, arguments: dict[str, Any]) -> ToolModel:
    """Validate raw tool arguments. Raises UnknownToolError or ToolValidationError."""
    model = TOOL_SCHEMAS.get(tool_name)
    if model is None:
        raise UnknownToolError(tool_name)
    if not isinstance(arguments, dict):
        raise ToolValidationError(
            tool_name,
            [{"loc": "", "message": "Tool arguments must be an object"}],
        )
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        issues = [
            {"loc": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        raise ToolValidationError(tool_name, issues) from exc
