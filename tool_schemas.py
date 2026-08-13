"""
Example tool-call schema contracts for a generic agentic write-integration.

Every tool the agent can call is defined here as a strict Pydantic model. The
model's tool-call arguments are validated against these schemas *before* the
tool body executes, so malformed or underspecified calls fail fast and legibly
instead of reaching business logic. Contains no client code, data, schemas,
or identifiers.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ContactType(str, Enum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class CreateContactRequest(BaseModel):
    """Tool schema for creating a new contact (customer or supplier) record."""

    contact_type: ContactType
    display_name: str = Field(..., min_length=2, max_length=200)
    external_reference: Optional[str] = Field(
        default=None,
        description="Client-side or source-system reference, if the caller has one.",
        max_length=50,
    )
    contact_detail: Optional[str] = Field(
        default=None, description="Primary email or phone number for this contact."
    )
    group_id: str = Field(
        ..., description="The contact group this record belongs to; determines defaults."
    )

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("display_name cannot be blank after stripping whitespace")
        return stripped


class InvoiceLine(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    quantity: float = Field(..., gt=0)
    unit_price: float = Field(..., ge=0)
    tax_code: str = Field(..., min_length=1, max_length=20)


class CreateInvoiceRequest(BaseModel):
    """Tool schema for creating a new invoice against an existing contact."""

    contact_id: str
    invoice_date: date
    due_date: date
    reference: Optional[str] = Field(default=None, max_length=50)
    lines: list[InvoiceLine] = Field(..., min_length=1)

    @field_validator("due_date")
    @classmethod
    def due_date_not_before_invoice_date(cls, due_date: date, info) -> date:
        invoice_date = info.data.get("invoice_date")
        if invoice_date is not None and due_date < invoice_date:
            raise ValueError("due_date cannot be before invoice_date")
        return due_date