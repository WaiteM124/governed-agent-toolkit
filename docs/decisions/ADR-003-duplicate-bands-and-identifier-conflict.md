# ADR-003: Duplicate match bands and conflicting-identifier cap

**Status:** Accepted  
**Date:** 2026-11-24  
**Note:** Reconstructed from code (`duplicate_detection.py`, `governed_write.py`) and README.

---

## Context

Master-data “create” tools can invent duplicates of existing contacts. Auto-blocking every fuzzy hit is too aggressive (second sites, typos); never blocking is too weak. Identifier mismatches need special handling: fuzzy IDs collide too easily if treated like names.

## Decision

Score candidates across weighted field kinds:

| Kind | Similarity policy |
|---|---|
| NAME | rapidfuzz token-sort after legal-suffix removal |
| IDENTIFIER | exact after normalising separators/case |
| CONTACT | exact email (or high typo tolerance) / last-7 phone digits |

Bucket results:

- **POSSIBLE** — attach as review context; still open approval.
- **LIKELY** — block the create; create **no** approval action.

If an IDENTIFIER-kind field conflicts with the stored value, drop that field from the weighted average and **cap confidence just below the LIKELY threshold** so the row stays reviewable without auto-blocking.

Only tools in `DUPLICATE_CHECKED_TOOLS` (currently `create_contact`) run the scorer; invoices and similar writes still need schema + approval.

## Alternatives rejected

- **Single blended “duplicate score” with auto-merge** — hides policy; unsafe for agent-driven creates.
- **Fuzzy matching on identifiers** — high false-positive / collision risk.
- **Always block on any identifier presence** — cannot express “second site / typo needs a person”.

## Consequences

- Thresholds (`review_threshold`, `block_threshold`) are injectable on `GovernedWriteService` and validated.
- Production should exact-lookup identifiers in an index before O(n) fuzzy scoring.
- Tests encode known-answer pairs for legal suffixes, phones, conflicting references, and ranking stability.
