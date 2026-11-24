# Contributing

Short human guide. Agents and detailed product rules: see [AGENTS.md](AGENTS.md).

## Setup

**Prerequisites:** Python 3.10+ (3.12 recommended; matches CI), pip.

```bash
pip install -e ".[dev]"
# or: pip install -r requirements.txt
```

No API keys or external services — the toolkit is fully in-memory.

### Pre-commit (recommended)

```bash
pip install pre-commit
pre-commit install
```

This runs the pinned **ruff** linter/formatter and **gitleaks** secret scan on commit. Matches CI lint expectations; see `.pre-commit-config.yaml`.

## Running

```bash
make demo                 # or: python demo.py
```

## Tests and lint

```bash
make lint                 # ruff check . && ruff format --check .
make test                 # pytest tests/ -v
make eval                 # adversarial eval gate (no API key)
```

Pytest covers known-answer scorer and pipeline cases. `make eval` runs the deterministic adversarial suite against `evals/baseline.json`; CI job `eval-gate` enforces it on every PR.

## Branch and PR conventions

- Branch from `master` with a short, descriptive name.
- Keep PRs **small and focused** on one concern.
- Fill in the pull request template checklist.
- State assumptions, intentional limitations, and follow-ups in the description.
- Do not commit secrets; use env vars or a secret manager.
- Do not delete or weaken tests solely to make CI pass.

## Definition of done

- Product principles in `AGENTS.md` respected (proposals ≠ transactions; schema before state; four-eyes; LIKELY/POSSIBLE policy; intentional idempotency)
- Tests added/updated and passing; lint clean; eval-gate green
- No secrets in the change
- Docs updated when behaviour or workflow changes
- CI green on the PR (including `eval-gate`)

## Follow-ups (tooling / process)

- Stop tracking `governed_agent_toolkit.egg-info/` (now gitignored); remove from history if desired.
- Persist gateway / record store, identifier index, async executor, multi-tenancy — see README “Production version would add”.
