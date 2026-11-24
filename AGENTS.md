# AGENTS.md — governed-agent-toolkit contributor & agent guide

Single source of truth for humans and AI agents (including Cursor) working in this repository.

**Product:** A reference governance layer between an AI agent and a production write path. An LLM tool call is a *proposal*, not a transaction. Schema validation, idempotent replay, duplicate detection, and a four-eyes approval gate make unsafe side-effects impossible without human sign-off — not inconvenient, but impossible by construction.

**Owner:** Miles Waite (AI systems architect). Portfolio reference implementation; intentional simplifications are listed in the README (in-memory stores, sync pipeline, O(n) duplicate scorer, single-tenant).

---

## Repository map

| Path | Role |
|---|---|
| `approval_gate.py` | `ApprovalGateway` state machine: propose → await → approve/reject/expire → execute; idempotency keys |
| `duplicate_detection.py` | Fuzzy scorer over NAME / IDENTIFIER / CONTACT fields; POSSIBLE vs LIKELY bands |
| `tool_schemas.py` | Pydantic `ToolModel` contracts for write tools (`extra="forbid"`, normalisation rules) |
| `governed_write.py` | `GovernedWriteService` pipeline wiring the three modules; in-memory `RecordStore` / echo executor |
| `demo.py` | Walkthrough: six scenarios against the in-memory store |
| `evals/` | Deterministic adversarial eval suite (GullibleAgent, no API keys) |
| `evals/cases/` | JSON attack and legitimate-allow cases |
| `evals/runner.py` | Eval runner + baseline gate (`python -m evals.runner`) |
| `evals/baseline.json` | Floors for attack block rate and legitimate allow rate |
| `evals/results/` | Latest gate results JSON written by the runner |
| `tests/` | Known-answer and edge-case coverage for gate, scorer, schemas, and full pipeline |
| `tests/conftest.py` | Shared fixtures (`FrozenClock` for deterministic time) |
| `pyproject.toml` | Package metadata, pinned-ish deps, ruff + pytest config |
| `requirements.txt` | Runtime + test install pins used by contributors and CI |
| `Makefile` | `demo`, `test`, `eval`, `lint`, `format`, `clean` |
| `.github/workflows/ci.yml` | PR/push CI: pinned ruff + pytest + `eval-gate` job |
| `docs/decisions/` | Architecture decision records |

Stack: **Python 3.10+** (CI uses 3.12), Pydantic v2, rapidfuzz, pytest, ruff. No external services — everything runs in-memory. Flat layout: modules live at the repo root (setuptools `packages = []`).

---

## Product principles (non-negotiable)

1. **Proposals are not transactions.** A tool call must not mutate a downstream system until it has passed schema validation, governance state, and human approval (where required). Do not add a bypass path that executes writes from the agent alone.
2. **Schema before state.** Malformed or unknown-argument tool calls return a blocked `Submission` and create **no** approval-gateway state. Keep junk out of the state machine.
3. **Four-eyes on approve.** The actor who proposed a write cannot approve it (`SelfApprovalError`). The proposer *may* withdraw (reject with `resolved_by == actor_id`). Do not weaken this guard.
4. **LIKELY blocks; POSSIBLE reviews.** Duplicate detection: **LIKELY** refuses the create with no action opened; **POSSIBLE** attaches candidates as review context and still requires a person. Conflicting IDENTIFIER fields must not auto-block — they stay reviewable under the confidence cap.
5. **Idempotency is intentional.** Keys are `sha256(actor_id:action_name:canonical_json(payload))`. REJECTED / EXPIRED release the key for a genuine retry; EXECUTED holds it forever. Executor failures leave the action APPROVED for safe retry keyed by `action_id`.

---

## Engineering standards

- **Typed code.** Type hints on public functions and methods. Pydantic models at tool-argument and other I/O boundaries (`ToolModel`, validated payloads).
- **Tests as the spec.** Prefer known-answer / golden cases for state transitions, scoring bands, and normalisation. Cover happy path, edge cases, and block/reject behaviour.
- **Green before PR.** `ruff check` / `ruff format --check`, `pytest`, and `python -m evals.runner` must pass locally. CI must stay green.
- **Small, focused PRs.** One concern per PR; clear title and description; state assumptions and out-of-scope items.
- **No secrets in git.** Use env vars or a secret manager only. Never commit API keys, credentials, private keys, or tokens in code, config, or remotes. Prefer `.env.example` when documenting required vars.
- **Pin versions for production-bound work.** Prefer pinned dependency versions (see `requirements.txt` / `pyproject.toml`). Do not silently bump tooling or libraries in a feature PR.
- **Structured logging.** When adding observability, use structured (preferably JSON) logs with correlation / action ids; never log secrets or full sensitive payloads.
- **AI / LLM outputs are untrusted.** This toolkit governs agent *proposals*; treat model output as input to validate, never as authority to write. Evaluate behaviour with the adversarial eval suite and unit tests; do not trust LLM output blindly.
- **Docs track behaviour.** Update README / ADRs / this file when behaviour, contracts, or run instructions change. Do not invent features in docs.

### Definition of done

- [ ] Product principles above respected
- [ ] Types and Pydantic contracts in place at boundaries
- [ ] Tests added or updated; known-answer cases where behaviour is specified
- [ ] Lint, tests, and eval-gate pass (`make lint` / `make test` / `make eval` or CI equivalents)
- [ ] No secrets introduced
- [ ] Docs updated if behaviour or workflow changed

---

## How to run and test

### Setup

```bash
pip install -e ".[dev]"
# or: pip install -r requirements.txt
```

Requires Python 3.10+. No API keys or external services.

### Run

```bash
make demo
# or: python demo.py
```

### Test and lint

```bash
make lint          # ruff check . && ruff format --check .
make test          # pytest tests/ -v
make eval          # adversarial eval gate (deterministic; no API key)
# or: python -m evals.runner
```

The eval gate loads `evals/cases/`, drives a `GullibleAgent` through the wired harness, writes `evals/results/latest.json`, and compares rates to `evals/baseline.json`. CI job `eval-gate` fails if any attack produces an executor side effect, or if the legitimate allow rate drops below baseline. Cases cover prompt injection via tool outputs, tools outside the schema registry, argument tampering, privilege escalation, skipping human approval, audit-log bypass, data exfiltration, and legitimate in-policy allows. If a case finds a real bypass, fix product code in a **separate commit** with a regression test (see [ADR-004](docs/decisions/ADR-004-adversarial-eval-gate.md)).

Optional local hooks:

```bash
pip install pre-commit
pre-commit install   # ruff + gitleaks on commit
```

---

## Workflow for agents

1. **Read before writing.** Inspect the real modules, neighbouring tests, and ADRs. Prefer matching existing patterns over inventing new ones.
2. **Plan large changes.** For multi-file or cross-cutting work, outline the approach, files touched, and risks before coding. Keep scope to the assigned task.
3. **Do not weaken tests.** Never delete, skip, or loosen assertions solely to make CI pass. Fix the code or add a justified, documented test change.
4. **State assumptions in the PR.** Call out env expectations, intentional limitations, and anything left as follow-up.
5. **Docs/config-only vs product code.** If the task says docs/config only, do not change application behaviour.
6. **Cursor rules.** Follow `.cursor/rules/*.mdc`; they summarise this file for scoped globs. When in conflict, **this file wins**.

---

## Related docs

- [CONTRIBUTING.md](CONTRIBUTING.md) — short human setup and PR conventions
- [README.md](README.md) — product overview, design decisions, limitations
- [docs/decisions/](docs/decisions/) — architecture decision records
