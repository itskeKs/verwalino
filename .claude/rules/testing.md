---
description: Executable scenarios and isolated test execution
paths:
  - "backend/tests/**"
  - "features/**"
  - "frontend/**/*.test.*"
  - "scripts/test*.py"
---

# Testing

- User-visible happy paths are English Gherkin in `features/*.feature`, bound by
  `backend/tests/bdd/` through the HTTP API or `@browser` Playwright steps. Follow
  `features/README.md`; keep scenarios deterministic, without network or LLM calls.
- Validation, boundaries and error cases belong in the area's existing unit test
  file using its helpers. Test names are sentences. Agent-hook contracts live in
  `scripts/test_agent_rules.py` and run without the backend or database.
- Red first: run new scenarios and tests without the change, see the intended
  failure, then implement it. Report that evidence in the PR.
- Browser prerequisites reuse the API World; run headless journeys with
  `make test-browser` (locks the shared DB and owns/stops its API and Vite stack).
  Install Chromium once as documented in `features/README.md`.
- Treat the shared test DB as single-writer: never run two backend test processes
  at once. `make test-backend`, `make test-bdd` and `make test-browser` acquire and
  release `/tmp/verwalino-testdb.lock` themselves. Direct pytest runs and dev stacks
  must hold that lock in their shell until finished; release between runs.
  Use only the test database and compose commands in AGENTS.md.
- Iterate selectively (`cd backend && uv run pytest tests/test_x.py -k name`
  or `cd frontend && npm test -- <file>`). Run `make lint` before a push and
  `make test` before a merge; `make test-agent-rules` needs no DB.
