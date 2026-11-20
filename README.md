# governed-agent-toolkit

A reference implementation of the governance layer that sits between an AI
agent and a production write path. Every design choice here is about making
unsafe side-effects impossible without human sign-off — not inconvenient, but
impossible by construction.

```
python demo.py          # run the walkthrough
python -m pytest -v     # 32 tests, all green
```

---

## What it does

An LLM can call a write tool. That is a _proposal_, not a transaction. This
toolkit enforces a strict pipeline before anything touches a downstream system:

```
agent tool call
    │
    ▼
[1] Schema validation          ← tool_schemas.py
    │  bad args → blocked, no state created
    ▼
[2] Idempotent replay          ← approval_gate.py
    │  live action exists → return it, skip steps 3-4
    ▼
[3] Duplicate detection        ← duplicate_detection.py
    │  LIKELY match → blocked, no state created
    │  POSSIBLE match → approve with candidates attached
    ▼
[4] Approval gate              ← approval_gate.py
    │  PROPOSED → AWAITING_APPROVAL → APPROVED/REJECTED/EXPIRED
    ▼
[5] Execute                    ← governed_write.py
    │  executor is called, then action is marked EXECUTED
    ▼
  downstream system
```

---

## Modules

### `approval_gate.py` — state machine

`ApprovalGateway` owns the lifecycle of every proposed write. States are
explicit; every transition is validated against an allow-list.

```
         propose()
  None ──────────► PROPOSED
                      │  request_approval()
                      ▼
              AWAITING_APPROVAL ──► EXPIRED  (deadline elapsed)
                 │       │
       approve   │       │  reject / withdraw
                 ▼       ▼
              APPROVED  REJECTED
                 │
         mark_executed()
                 ▼
              EXECUTED
```

Idempotency keys are computed as `sha256(actor_id:action_name:canonical_json(payload))`. Two calls with the same key return the same action. A rejected or expired action releases the key so a genuine retry opens a new decision; an executed action keeps the key so replays are deduplication-safe.

### `duplicate_detection.py` — fuzzy scorer

Before a "create" tool opens an approval, existing records are scored across
three field kinds:

| Kind | Similarity | Why |
|------|-----------|-----|
| `NAME` | rapidfuzz token-sort after legal-suffix removal | "Acme Ltd" == "ACME Limited" |
| `IDENTIFIER` | exact after normalising separators and case | fuzzy IDs collide too easily |
| `CONTACT` | exact email (typo tolerance ≥ 0.92) or last-7-digit phone | partial mailbox match is not a duplicate |

Results are bucketed:
- **POSSIBLE** — shown to the approver as review context.
- **LIKELY** — blocks the create outright; no approval is opened.

### `tool_schemas.py` — Pydantic contracts

Every write tool is a `ToolModel`. Unknown fields are rejected (`extra="forbid"`).
Key normalisation rules: email lowercased, phone stripped to `+` and digits,
tax codes uppercased, invoice amounts rejected if passed as IEEE floats (use
strings or integers to avoid binary rounding error).

### `governed_write.py` — composed write service

`GovernedWriteService` wires the three modules into the pipeline shown above.
`RecordStore` is an in-memory stand-in; a real service would swap it for a
database query. `IdempotentEchoExecutor` is the stand-in ERP/CRM client; it
returns a stable UUID for each `action_id` so a crash-and-retry is safe.

---

## Install and run

```bash
pip install rapidfuzz pydantic
python demo.py
python -m pytest -v
```

Requires Python 3.10+. No external services — everything is in-memory.

---

## Design decisions

**Self-approval guard.** The actor who proposed a write cannot approve it
(`SelfApprovalError`). They _can_ withdraw it (reject with `resolved_by ==
actor_id`), which is treated as a voluntary cancellation, not an authorisation.

**Idempotency key release on rejection.** REJECTED and EXPIRED are non-holding
states: the key is freed so a genuine retry starts a fresh decision cycle. This
matters when a person rejects because of bad data — the agent can fix the
payload and resubmit without being blocked by the old key. EXECUTED holds the
key forever, making replay detection permanent.

**Conflicting-identifier cap.** If a proposed record brings a `IDENTIFIER`-kind
field that doesn't match the stored one (e.g. two different account codes),
the scorer drops the identifier from the weighted average and caps confidence
just below the LIKELY threshold. The row is still surfaced for review without
auto-blocking: the second code could be a second site, or a typo only a person
can resolve.

**Schema errors create no governance state.** A malformed tool call returns a
blocked `Submission` immediately. The approval gateway never sees it. This keeps
the state machine clean and prevents an agent retry loop from filling the
gateway with junk.

**`or` vs `is None` for injected dependencies.** `ApprovalGateway` has
`__len__`, which makes an empty gateway falsy. `gateway or ApprovalGateway()`
would silently discard a valid injected gateway with zero actions (breaking
clock injection in tests). All optional constructor parameters use
`x if x is not None else default` to avoid this trap.

**Executor failure leaves the action APPROVED.** If the downstream call raises,
the action stays in APPROVED so a later `execute()` call retries it. The
executor is responsible for idempotency on its own side (keyed by `action_id`);
double-execution is safe.
