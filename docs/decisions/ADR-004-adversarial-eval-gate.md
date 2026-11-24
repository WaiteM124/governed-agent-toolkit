# ADR-004: Deterministic adversarial eval gate in CI

**Status:** Accepted  
**Date:** 2026-11-24

---

## Context

Unit tests cover known-answer behaviour for the approval gate, schemas, duplicate scorer, and composed write path. They do not systematically exercise a *gullible* agent that obeys injected instructions from tool outputs or documents. Without a cheap, key-free eval suite, regressions that let an attack produce a write (or that false-block legitimate calls) can land unnoticed. Sibling library [agent-guard](https://github.com/WaiteM124/agent-guard) already runs a red-team gate on every PR for the same reason.

## Decision

Add a deterministic adversarial eval suite under `evals/`:

1. **Cases** — JSON files in `evals/cases/` covering prompt injection via tool outputs, tools outside the schema registry, argument tampering, privilege escalation, skipping human approval, audit-log bypass, data exfiltration, and legitimate in-policy calls that must be allowed.
2. **Control** — a scripted `GullibleAgent` that parses an `<attack>` JSON block and drives only the wired harness API (no LLM, no API keys).
3. **Metrics** — report attack **block rate** (no executor side effect) and legitimate **allow rate** (submission enters the gate; optional human path completes).
4. **Baseline** — `evals/baseline.json` sets floors (`min_attack_block_rate`, `min_legitimate_allow_rate`). CI job `eval-gate` runs `python -m evals.runner` on every PR/push and fails if any attack leaks or the allow rate drops below baseline.
5. **Behaviour changes** — the suite does not change product behaviour by itself. If a case finds a real bypass, fix it in a **separate commit** with a regression test and an explanation.

## Alternatives rejected

- **LLM-as-attacker in CI** — non-deterministic, needs API keys, slow, and flaky as a merge gate.
- **Pytest-only red-team flag without baseline rates** — fine for libraries that only care about "all blocked"; this toolkit also needs an explicit false-block floor for legitimate creates.
- **Deferring the gate until a hosted product exists** — the reference implementation is where regressions are cheapest to catch; AGENTS.md already reserved an eval-gate slot for this.

## Consequences

- Contributors add or update cases under `evals/cases/` when governance surface area changes; bump baseline only deliberately (`--update-baseline`).
- CI gains a second job (`eval-gate`) with no secrets.
- Hosts still must not let the agent supply authenticated approver ids — the harness refuses approver impersonation; that boundary remains a host responsibility (see ADR-002).
