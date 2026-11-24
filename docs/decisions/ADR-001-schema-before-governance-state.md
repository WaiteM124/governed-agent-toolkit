# ADR-001: Schema validation before governance state

**Status:** Accepted  
**Date:** 2026-11-24  
**Note:** Reconstructed from code (`tool_schemas.py`, `governed_write.py`) and README.

---

## Context

Agent tool calls arrive as unstructured argument dicts. If invalid calls entered the approval gateway, retries and repair loops would fill the state machine with junk actions that can never execute cleanly.

## Decision

Validate every write tool call against a Pydantic `ToolModel` (`extra="forbid"`, field normalisation) **before** any `ApprovalGateway` state is created. `GovernedWriteService.submit()` catches `ToolValidationError` and returns a blocked `Submission` with `BlockReason.SCHEMA_INVALID` and no action.

Unknown tool names raise (wiring error); malformed arguments for a known tool return a blocked submission so the agent can repair and retry.

## Alternatives rejected

- **Validate inside the gateway on propose** — couples schema concerns to the state machine and still creates action ids for bad payloads.
- **Coerce / ignore unknown fields** — hides agent mistakes; rejected fields must fail closed (`extra="forbid"`).
- **Open an approval marked invalid for human triage** — pollutes the four-eyes queue with noise that should never reach a person.

## Consequences

- The gateway only stores structurally valid, canonicalised payloads — idempotency keys stay stable.
- Agents get located validation issues without side effects.
- New write tools must register a `ToolModel` and be wired through `validate_tool_call` before they can enter governance.
