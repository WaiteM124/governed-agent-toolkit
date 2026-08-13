# Governed Agent Toolkit

Patterns for building **agentic middleware that's allowed to mutate real systems** — ERP, CRM,
finance platforms, anything where a wrong write has a real cost. Extracted and reimplemented from
production experience building governed agent middleware; no client code, data, or identifiers
are included anywhere in this repo.

Agents that only *read* and summarise are easy to trust. The moment an agent can *create* or
*modify* a record — a customer, an invoice, a payment — you need infrastructure the LLM can't
talk its way around. This repo demonstrates three of those pieces.

## 1. Approval-gate state machine (`approval_gate.py`)

A write action doesn't execute the moment the model decides to call a tool. It moves through an
explicit state machine:

```
PROPOSED → AWAITING_APPROVAL → APPROVED → EXECUTED
                ↓
            REJECTED / EXPIRED
```

Key properties:
- **Idempotency key per proposed action** — the same logical request (same actor, same payload
  hash) can't be double-executed even if approval-clicks or retries fire twice.
- **Explicit timeout** — an approval that sits unanswered expires rather than lingering as a
  live, executable action indefinitely.
- **Separation of "the model wants to" from "it happened"** — the state machine, not the LLM's
  own text output, is the source of truth for whether a mutation occurred.

## 2. MDM duplicate-detection (`duplicate_detection.py`)

Before any "create new record" tool call is allowed to execute, candidate matches against
existing records are surfaced using fuzzy string comparison across multiple weighted fields
(name, identifier, contact detail) rather than a single field or exact match — real-world data
entry has typos, abbreviations, and formatting drift that exact-match dedup misses.

Returns ranked candidates with a confidence score and the matched fields, so a human (or a
downstream approval step) can see *why* something was flagged, not just that it was.

## 3. Tool schema contracts (`tool_schemas.py`)

Every write tool the agent can call is defined as a strict Pydantic schema — the model can't
pass free-text into a field that needs to be a valid enum, a properly-shaped identifier, or a
constrained numeric range. Schema validation happens before the tool body ever runs, so malformed
tool calls fail fast and legibly rather than corrupting downstream state.

## Why this matters

None of these three pieces make an agent *smarter*. They make it **safe to give write access to**.
That's a different, and in production, more important, problem than model capability.

## Running the tests

```bash
pip install -r requirements.txt
pytest
```