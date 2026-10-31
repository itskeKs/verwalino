# Contributing to Verwalino

The software is German; everything about it — docs, issues, code comments, commits — is English.
Rules for coding agents, which apply to people too, are in [AGENTS.md](AGENTS.md).

## Development environment

- Python with [uv](https://docs.astral.sh/uv/), Node 22, Docker (for the test database only).
- Backend dependencies: `cd backend && uv sync --extra dev --extra ops --extra ocr`.
  The local launcher `scripts/dev-local.sh` requires Poppler (`pdftoppm`) and
  Tesseract with the German language pack (`deu`); its worker health check aborts
  startup if any is missing.
- Browser setup (once, and after Playwright lock updates):
  `cd backend && uv run --locked --extra dev playwright install chromium`.
- Frontend dependencies: `cd frontend && npm ci` (`make frontend-deps` does it when needed).
- Test database: `docker compose -f infra/docker-compose.declaration-test.yml up -d` starts
  `infra-postgres-1` on `127.0.0.1:55432` (in-memory, recreated on restart);
  `source scripts/test-env.sh` sets the connection variables. Never point tests at the production
  instance.
- Local app: run `scripts/dev-local.sh` from the repository root after installing both
  dependency sets. It starts the API on `:18000`, UI on `:13000`, and invoice worker
  together, plus the inbox worker when its health check passes, using only the test database on `:55432` and a separate
  `/tmp/hv-dev-documents.*` storage folder by default (or an explicitly supplied
  `/tmp` `HV_DOCUMENT_STORAGE_ROOT`). It ensures the local test admin
  `test@example.de` exists and prints its login when ready. Cleanup stops every started process on Ctrl+C or partial startup
  failure without stopping PostgreSQL; originals in the printed folder are
  preserved. Do not run resetting backend tests during a UI acceptance run.
  Worker operation: [invoice worker](DEPLOY.md#invoice-worker-t10-alpha-active-since-2026-10-24)
  and [inbox worker](DEPLOY.md#inbox-worker).
  Inbox worker skipped: check ClamAV (clamscan and signatures), worker database access, document storage and German OCR; API and UI will continue.
- Declarations of division (Teilungserklärungen): the dependency sets above include the
  operations tooling. Imports require **Human Approval** before creating canonical
  building, unit, or rule data; document content remains untrusted. See the
  [declaration-import runbook](docs/operations/declaration-imports.md) for operation,
  data protection, local tests, and the deliberately blocked live evaluation.
- Self-hosting: [DEPLOY.md](DEPLOY.md) and `scripts/self-host.sh`.
- Enable the commit gate once per clone: `make hooks`.

## Agent host setup

Root `AGENTS.md` is the common policy for every host; `CLAUDE.md` is a symlink to it (the
checkout must preserve symlinks, which Git does by default). Claude Code loads `.claude/rules/*.md`
and Coddy reads the same tree. Cursor uses the paired `.cursor/rules/*.mdc`. A Claude rule without
`paths` is always on; otherwise its `paths` match the Cursor `globs`, with `alwaysApply: false`.
Keep bodies and descriptions equivalent and change both trees in the same commit (Rules Sync);
`make test-agent-rules` checks parity, the symlink and the hook.

For Codex, trust this checkout's project configuration and run **`/hooks` once per clone** to
review and approve both handlers in `.codex/hooks.json`. Review approval again after changing
the hook definition or script. Python 3 is required. This is separate from `make hooks`, which
installs the Git pre-commit gate.

The adapter reads Cursor rules directly: `SessionStart` attaches always-on rules and
`PreToolUse` on `apply_patch`, `Edit` or `Write` attaches rules matching the edited paths.
Use quoted block lists for `paths` and `globs`; the adapter supports this repository's flat
frontmatter convention, not arbitrary YAML. It is stateless, so matching rules attach on each
edit. Malformed input or rules fail open and never block work. Rules provide context;
AGENTS.md retains the safety baseline and tests/CI check parity.

## Running the tests

`make help` lists every target.

- **`make test`** — the whole run before a merge: agent-rule, scenario-filing and installer contracts, the backend
  suite (including the BDD scenarios), frontend vitest and typecheck. Needs the test database.
- **`make lint`** — ruff over the backend, eslint and `tsc` over the frontend.
- **`make test-bdd`** — API scenarios in `features/` ([features/README.md](features/README.md)).
- **`make test-browser`** — headless `@browser` scenarios; acquires the shared DB lock and
  owns/stops its local API and Vite stack. See [browser setup and runner](features/README.md#browser-features).
- **`make test-scenarios-report`** — all API/browser features on the local stack with JSON,
  Markdown and failure artifacts in `report/`. See [reports and external targets](features/README.md#scenario-reports-and-nightly-runs).
- **`make test-agent-rules`** — paired bodies/activation, the `CLAUDE.md` symlink and Codex hook
  contracts; Python stdlib, no database or backend dependencies. Run after changing rules or the adapter.
- **`make test-installer`** — release-download, install-scenario reporting and portable
  self-host contracts; fake curl/wget, Docker and cosign, no network downloads or database.
  `python3 -B scripts/test-install.py` and `python3 -B scripts/test-install-scenarios.py`
  also run standalone with stdlib only.
- **`make test-scenario-issues`** — scenario failure fingerprints, deduplication and regression
  filing; Python stdlib, fake GitHub, no network or database.
- **`make docs-check`** — relative links and images in every Markdown file outside `docs/archive/`.
- While iterating, run one file: `cd backend && uv run pytest tests/test_x.py -k name`,
  `cd frontend && npm test -- src/pages/X.test.tsx`; `make test-backend PYTEST_ARGS="…"` does the
  same with the test environment loaded.
- The backend suite truncates tables in the shared test database: never run two suites at once.
  For direct pytest runs or a dev stack, acquire the lock in that same shell with
  `until mkdir /tmp/verwalino-testdb.lock 2>/dev/null; do sleep 10; done` and
  `trap 'rmdir /tmp/verwalino-testdb.lock' EXIT`; stop the stack before releasing it.
  `make test-backend`, `make test-bdd`, `make test-browser` and `make test-scenarios-report`
  acquire/release the lock themselves.

The pre-commit hook (`.githooks/pre-commit` → `scripts/checks.sh`) runs ruff when backend Python
is staged, eslint on staged frontend TypeScript files, and always the docs link check.
If `frontend/node_modules` is missing, eslint is skipped with a message.

## Making a change

1. **Start from an issue.** Every change has one, written as a spec with the
   [task template](.github/ISSUE_TEMPLATE/task.md): What, Why it happens (file references checked
   against `main`), Proposed change, Acceptance, Out of scope. Order: [docs/roadmap.md](docs/roadmap.md).
2. **Branch** from `main`: `<type>/<short-name>`, e.g. `feat/signup`, `fix/bank-import-dedup`.
3. **Red first.** Write the scenario (happy path, `features/`) and the unit tests (edge and error
   cases, in the area's existing test file) and see them fail.
4. **Implement** the smallest change that turns them green. Follow the design rules in
   [AGENTS.md](AGENTS.md#design-rules).
5. **Docs in the same PR**, per the [documentation contract](.claude/rules/docs.md).
   [DESIGN.md](DESIGN.md) is the frontend design contract: new screens follow it, and changes to the
   look or copy update it in the same PR.
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
| `browser` | locked Chromium, real UI journeys (`make test-browser`) |
| `docs` | no broken relative links; paired rule parity and Codex hook contracts |

## Where things live

| Path | Contents |
|---|---|
| `backend/app/` | `api/` routes, `services/`, `engine/` (pure calculations), `models/`, `workers/` |
| `backend/migrations/versions/` | Alembic migrations; committed ones are never edited |
| `backend/tests/` | unit and API tests; `bdd/` binds scenarios, `bdd/browser.py` owns browser fixtures, `bdd/report.py` writes reports, `bdd/steps/browser_*.py` shares browser steps |
| `features/` | API and `@browser` Gherkin scenarios (executable specs) |
| `.github/workflows/nightly-scenarios.yaml` | nightly self-host scenarios, 90-day report artifacts and deduplicated failure issues; hosted mode pending #18 |
| `frontend/src/` | `pages/`, `components/`, `api/`, `lib/` |
| `install.sh` | one-command release installer; checksum/signature checks and digest-pinned portable init/start |
| `scripts/build-release-bundle.sh` | shared flat release payload/checksum assembly for releases and the install matrix |
| `scripts/install-scenarios.sh` | four install journeys on disposable VMs; JSON/Markdown reports and failure artifacts |
| `scripts/test-install.py`, `scripts/test-self-host.py`, `scripts/test-install-scenarios.py` | installer, portable stack and scenario-report contracts; fake downloads/Docker, no database |
| `.github/workflows/release-image.yaml` | publishes/signs both image platforms, then signs checksums and attaches installer assets to a GitHub Release |
| `.github/workflows/install-matrix.yaml` | nightly, manual and tag-triggered native install checks; reports retained for 90 days |
| `scripts/` | dev, self-host, deploy, checks; `acceptance/` holds legacy browser scripts (being migrated, #54) |
| `scripts/scenario_issues.py`, `scripts/test-scenario-issues.py` | stdlib report consumer and fake-GitHub filing contracts; `@known-flaky` failures are excluded |
| `DESIGN.md` | frontend design contract: tokens, layout, components, states, copy and formats; screenshots in `docs/assets/design/` |
| `docs/` | architecture, domain pages (`m*.md`), ADRs, operations, roadmap, feature status |
