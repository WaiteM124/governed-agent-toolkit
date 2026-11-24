# ADR-002: Four-eyes approval and idempotency key release

**Status:** Accepted  
**Date:** 2026-11-24  
**Note:** Reconstructed from code (`approval_gate.py`, `governed_write.py`) and README.

---

## Context

Write-capable agents can propose the same mutation many times (retries, crashes, duplicate tool calls). Humans must authorise execution, but the proposer must not be able to self-approve. After a reject or expire for bad data, a corrected resubmit must be allowed.

## Decision

1. **Self-approval guard.** `ApprovalGateway.resolve(..., approved=True)` raises `SelfApprovalError` when `resolved_by == actor_id`. Withdrawal (reject by the same actor) is allowed.
2. **Idempotency keys.** `sha256(actor_id:action_name:canonical_json(payload))`. Live and EXECUTED actions hold the key; REJECTED and EXPIRED release it so a genuine retry opens a new decision. EXECUTED holds forever so replays stay safe.
3. **Executor failure stays APPROVED.** Downstream errors do not mark EXECUTED; a later `execute()` retries. Executors must be idempotent on `action_id`.

## Alternatives rejected

- **Allow self-approval for “trusted” agents** — defeats the product invariant that side-effects require a distinct human.
- **Hold idempotency keys after reject** — blocks legitimate retries after bad data is fixed.
- **Mark EXECUTED before the downstream call** — loses crash recovery; a failure would look finished when nothing landed.

## Consequences

- Tests inject clocks and distinct actor/approver ids; empty gateways must be injected with `is not None` checks because `__len__` makes them falsy.
- Production persistence needs a unique constraint on the idempotency key and a sweeper for `expire_stale()`.
- Approver identity and reason become part of the audit trail of every write.
