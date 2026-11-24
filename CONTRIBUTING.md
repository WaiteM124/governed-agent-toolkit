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
```

There is no separate eval suite yet; pytest is the quality gate (including known-answer scorer and pipeline cases).

## Branch and PR conventions

- Branch from `master` with a short, descriptive name.
- Keep PRs **small and focused** on one concern.
- Fill in the pull request template checklist.
- State assumptions, intentional limitations, and follow-ups in the description.
- Do not commit secrets; use env vars or a secret manager.
- Do not delete or weaken tests solely to make CI pass.

## Definition of done

- Product principles in `AGENTS.md` respected (proposals ≠ transactions; schema before state; four-eyes; LIKELY/POSSIBLE policy; intentional idempotency)
- Tests added/updated and passing; lint clean
- No secrets in the change
- Docs updated when behaviour or workflow changes
- CI green on the PR

## Follow-ups (tooling / process)

- Add a deterministic eval / adversarial fixture suite and an `eval-gate` CI job once it exists and is cheap enough for every PR.
- Stop tracking `governed_agent_toolkit.egg-info/` (now gitignored); remove from history if desired.
- Persist gateway / record store, identifier index, async executor, multi-tenancy — see README “Production version would add”.
