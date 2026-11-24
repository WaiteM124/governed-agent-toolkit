## Summary

<!-- What changed and why. State assumptions and out-of-scope items. -->

## Checklist

- [ ] **Principles respected** — proposals are not transactions; schema before governance state; four-eyes on approve; LIKELY blocks / POSSIBLE reviews; intentional idempotency ([AGENTS.md](../AGENTS.md))
- [ ] **Tests added or updated** — known-answer cases where behaviour is specified; existing tests not weakened or deleted to pass
- [ ] **Tests and lint passing** — `ruff check` / `ruff format --check` and pytest (CI green)
- [ ] **No secrets** — no API keys, credentials, or private keys in code or this PR
- [ ] **Docs updated** — README / ADRs / `AGENTS.md` / `CONTRIBUTING.md` updated if behaviour or workflow changed

## Test plan

<!-- Commands run locally, or "rely on CI". -->
