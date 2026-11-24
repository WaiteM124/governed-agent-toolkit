# Architecture decision records

Lightweight records of significant design choices in this repository.

## Format

Each record is a Markdown file named `ADR-NNN-short-slug.md` and uses these sections:

1. **Context** — what problem or force required a choice.
2. **Decision** — what we chose to do.
3. **Alternatives rejected** — options considered and why they were not taken.
4. **Consequences** — what becomes easier, harder, or constrained as a result.

Optional metadata at the top: **Status** (Accepted / Superseded / Proposed), **Date**, and whether the record was **reconstructed from code** when reasoning was not previously written down.

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-schema-before-governance-state.md) | Schema validation before governance state | Accepted (reconstructed) |
| [ADR-002](ADR-002-four-eyes-and-idempotency-keys.md) | Four-eyes approval and idempotency key release | Accepted (reconstructed) |
| [ADR-003](ADR-003-duplicate-bands-and-identifier-conflict.md) | Duplicate match bands and conflicting-identifier cap | Accepted (reconstructed) |
| [ADR-004](ADR-004-adversarial-eval-gate.md) | Deterministic adversarial eval gate in CI | Accepted |

When behaviour changes, update or supersede the relevant ADR and keep [AGENTS.md](../../AGENTS.md) aligned.
