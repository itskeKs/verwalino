# Contributing to Verwalino

The software is German; everything about it — docs, issues, code comments, commits — is English.
Rules for coding agents, which apply to people too, are in [AGENTS.md](AGENTS.md).

## Development environment

- Python with [uv](https://docs.astral.sh/uv/), Node 22, Docker (for the test database only).
- Backend dependencies: `cd backend && uv sync --extra dev --extra ops --extra ocr`.
- Frontend dependencies: `cd frontend && npm ci` (`make frontend-deps` does it when needed).
- Test database: `docker compose -f infra/docker-compose.declaration-test.yml up -d` starts
  `infra-postgres-1` on `127.0.0.1:55432` (in-memory, recreated on restart);
  `source scripts/test-env.sh` sets the connection variables. Never point tests at the production
  instance.
- Local app: `scripts/dev-local.sh`. Self-hosting: [DEPLOY.md](DEPLOY.md) and `scripts/self-host.sh`.
- Enable the commit gate once per clone: `make hooks`.

## Running the tests

`make help` lists every target.

- **`make test`** — the whole run before a merge: installer contract tests, the backend
  suite (including the BDD scenarios), frontend vitest and typecheck. Needs the test database.
- **`make lint`** — ruff over the backend, eslint and `tsc` over the frontend.
- **`make test-bdd`** — only the scenarios in `features/` ([features/README.md](features/README.md)).
- **`make docs-check`** — relative links and images in every Markdown file outside `docs/archive/`.
- While iterating, run one file: `cd backend && uv run pytest tests/test_x.py -k name`,
  `cd frontend && npm test -- src/pages/X.test.tsx`; `make test-backend PYTEST_ARGS="…"` does the
  same with the test environment loaded.
- The backend suite truncates tables in the shared test database: never run two suites at once.

The pre-commit hook (`.githooks/pre-commit` → `scripts/checks.sh`) runs ruff, eslint on staged
frontend files and the docs check for staged Markdown — seconds, not minutes. Tests are opt-in:
`VERWALINO_HOOK_TESTS=fast` (staged test modules, frontend typecheck and vitest) or `full`
(`make test`). `VERWALINO_HOOK_LINT=0` and `VERWALINO_HOOK_DOCS=0` switch one check off,
`VERWALINO_HOOK_SKIP=1` skips the hook.

## Making a change

1. **Start from an issue.** Every change has one, written as a spec with the
   [task template](.github/ISSUE_TEMPLATE/task.md): What, Why it happens (file references checked
   against `main`), Proposed change, Acceptance, Out of scope. Order: [docs/roadmap.md](docs/roadmap.md).
2. **Branch** from `main`: `<type>/<short-name>`, e.g. `feat/signup`, `fix/bank-import-dedup`.
3. **Red first.** Write the scenario (happy path, `features/`) and the unit tests (edge and error
   cases, in the area's existing test file) and see them fail.
4. **Implement** the smallest change that turns them green. Follow the design rules in
   [AGENTS.md](AGENTS.md#design-rules).
5. **Docs in the same PR**, per the [documentation contract](AGENTS.md#documentation-contract).
6. `make lint` green before every push; `make test` (the whole suite) before the merge — CI runs it on
   every PR.

## Pull requests

Commit subjects follow `type(scope): summary` — one lower-case line in the imperative, scope is
the area (`bank`, `auth`, `selfhost`, `bdd`, `ci`, …). The body says cause → change → tests and
ends with `Refs #N` or `Closes #N`. `Closes` only when every acceptance box of the issue is done.

The PR description states what the change is for, which scenarios and tests were added (and that
they were red first), how `make test` and `make lint` went, and carries screenshots when the UI
changed. CI (`.github/workflows/tests-on-pr.yaml`) must be green:

| Job | What it proves |
|---|---|
| `backend` | ruff and the full backend suite with BDD scenarios against Postgres 16 with RLS |
| `frontend` | eslint, TypeScript, vitest |
| `docs` | no broken relative links |

## Where things live

| Path | Contents |
|---|---|
| `backend/app/` | `api/` routes, `services/`, `engine/` (pure calculations), `models/`, `workers/` |
| `backend/migrations/versions/` | Alembic migrations; committed ones are never edited |
| `backend/tests/` | unit and API tests; `bdd/` binds the scenarios |
| `features/` | Gherkin scenarios (executable specs) |
| `frontend/src/` | `pages/`, `components/`, `api/`, `lib/` |
| `scripts/` | dev, self-host, deploy, checks; `acceptance/` holds legacy browser scripts (being migrated, #5) |
| `docs/` | architecture, domain pages (`m*.md`), ADRs, operations, roadmap, feature status |
