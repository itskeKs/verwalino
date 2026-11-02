# Contributing to Verwalino

The software is German; everything about it — docs, issues, code comments, commits — is English.
Rules for coding agents, which apply to people too, are in [AGENTS.md](AGENTS.md).

Every session removes its own leftovers when the task ends and checks for them before the next
task. Follow [Session cleanup](AGENTS.md#session-cleanup) for worktrees, disposable stacks,
temporary files and background processes; leave other sessions, shared services and production
untouched. Report cleanup in one line, never in a repository file.

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
  Worker operation: [invoice worker](DEPLOY.md#invoice-worker-t10)
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
- **`make mail-previews`** — synthetic transactional mail gallery and 600/375 px light/dark PNGs;
  no database or SMTP ([workflow](docs/operations/mail-previews.md)).
- **`make test-bdd`** — API scenarios in `features/` ([features/README.md](features/README.md)).
- **`make test-browser`** — headless `@browser` scenarios; acquires the shared DB lock and
  owns/stops its local API and Vite stack. See [browser setup and runner](features/README.md#browser-features).
- **`make test-scenarios-report`** — all API/browser features on the local stack with JSON,
  Markdown and failure artifacts in `report/`. See [reports and external targets](features/README.md#scenario-reports-and-nightly-runs).
- **`make test-agent-rules`** — paired bodies/activation, the `CLAUDE.md` symlink and Codex hook
  contracts; Python stdlib, no database or backend dependencies. Run after changing rules or the adapter.
- **`make test-installer`** — release-download, install-scenario reporting, portable
  self-host/hosted deploy contracts and canary tests; fake curl/wget, Docker and cosign,
  plus a local fake HTTP server, no network downloads or database.
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

Backend tests have a 600-second per-test timeout (including fixture setup and teardown).
`pytest-timeout` uses its thread method to print all thread stacks and terminate a hung
test process; the Make target then releases the shared database lock. PostgreSQL test
connections also use a five-second lock timeout and a 120-second statement timeout.
Ordinary tenant, review and worker sessions retain the database's waiting policy.
Only deletion lifecycle and hosted quota admission cap transaction lock waits at 30 seconds,
preserve stricter caller limits, and reset on commit or rollback. Hosted document directory
admission also waits at most 30 seconds and closes handles on timeout or cancellation.
HTTP database lock timeouts (SQLSTATE `55P03`) and directory timeouts return 409 with
`LOCK_TIMEOUT` and a German retry message, without an error-tracker event. Invoice and
intake workers retry the saved completion output in a fresh transaction, retaining lease fencing,
for at most ten attempts with exponential delays from one second up to 30 seconds; on exhaustion,
the worker loop reports failure and the durable lease can be reclaimed within the job's attempt cap.
The declaration pipeline treats a database lock timeout like an interrupted worker: the run is
marked `RETRY_WAIT` with its charged usage kept and re-runs after five minutes; no output is replayed.
In hosted mode, the operator's `quota-recount` caps database lock waits likewise and refuses an already
held directory lock immediately; either contention exits with code 1 and the same retry message.
Deletion CLI admission uses try-advisory locks and `FOR UPDATE NOWAIT` per organization;
failed organizations roll back their transaction, including open savepoints, and close
their session before the run continues. Contended organizations are reported for retry. Tests pass their
own organization IDs; deletion cleanup touches only those rows and uses `NOWAIT`.

On SQLAlchemy or raw psycopg2 lock timeout, the test-session instrumentation opens a separate
connection and prints the failing PID/statement, `pg_stat_activity` (including blocking PIDs,
queries and transaction ages) and `pg_locks` for open transactions. The timed-out wait has
already left `pg_locks`, so all open transactions are included to retain potential holders.
A pytest report hook retains a fallback and identifies diagnostics already emitted at the error.
On any local pytest timeout, the same independent connection dumps all test-database backends
and their locks with the test ID before the usual Python stacks.
When a test body fails, the session rolls back every database transaction it left open (ORM
sessions, SQLAlchemy connections and raw psycopg2 connections) before fixture finalizers run and
lists them in the failure report under `open transactions`; `HV_TEST_KEEP_OPEN_TRANSACTIONS=1`
disables this to reproduce the raw symptom. Fixtures of a passing test keep owning their
transactions (several commit at teardown).
Pytest includes these stderr diagnostics with the named failure; synthetic contention tests
verify the dump, but do not identify the holder from the historical CI hangs.
Keep targeted runs short (`timeout 1500 make test-backend PYTEST_ARGS="…"`); a legitimately
longer test needs an explicit `@pytest.mark.timeout(...)` with a reason.
CI runs four backend shards with a 30-minute limit each; local `make test` still
runs the whole backend suite in one process.

Hosted signup acceptance sends real SMTP to the local Mailpit inbox. Start it once with
`docker compose -f infra/docker-compose.declaration-test.yml up -d mailpit` and leave it
running for other worktrees. `scripts/test-env.sh` (sourced by the Make targets) exports
`HV_SMTP_HOST=127.0.0.1`, `HV_SMTP_PORT=1025`, `HV_SMTP_SECURITY=plain`,
`HV_SMTP_FROM=verwaltung@example.invalid`, empty SMTP credentials, and
`VERWALINO_MAILPIT_API=http://127.0.0.1:8025`. SMTP and the unauthenticated UI/API bind
only to loopback. Run `make test-bdd-hosted` for signup acceptance; its fixture clears the
shared inbox before each mail scenario, polls briefly, and fails if Mailpit is unavailable.
CI provides the same digest-pinned Mailpit image in each backend shard. The browser scenarios
do not send mail and need no Mailpit container. Mail-service unit tests keep FakeSMTP.

If the shared database holds a migration revision unknown to the checkout, the session fixture
logs that revision, drops and recreates `public` with its runtime schema grants, preserves the
cluster roles, and runs the existing upgrade rehearsals. Known revisions still downgrade to
base first. The reset requires `VERWALINO_TEST_DB=1` (exported by `scripts/test-env.sh`) and the
validated `app_migrator` connection to `hausverwaltung` on `127.0.0.1` or `localhost`, port
`55432`; it refuses every other target before connecting. As a manual alternative, hold the
same lock, recreate only the disposable container with
`docker compose -f infra/docker-compose.declaration-test.yml up -d --force-recreate --wait postgres`,
then run the tests.

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
| `backend-shards` | four disjoint pytest selections, plus hosted BDD selections, each against its own Postgres 16 and Mailpit |
| `backend-checks` | ruff, installer/Caddy checks and the collection contract, alongside the shards without a database |
| `backend` (`Backend (ruff, pytest)`) | required summary: fails unless every shard and `backend-checks` succeeded, including cancellation or skipped jobs |
| `frontend` | eslint, TypeScript, vitest |
| `browser` | locked Chromium, real UI journeys (`make test-browser`) |
| `docs` | no broken relative links; paired rule parity and Codex hook contracts |

Backend CI uses `pytest-split --splits 4 --group N --splitting-algorithm least_duration`
with committed timings in `backend/.test_durations`. Each matrix job runs on a separate
GitHub-hosted VM, so the existing compose project, container name and loopback ports
remain isolated without changing the disposable-database guard. Every process rehearses
migrations and retains the transaction rollback guard from #181. `-p no:randomly` is
explicit: each shard preserves the relative order of its selected tests, but has its own
predecessors. The single full-suite ordered window used to investigate #180 no longer
describes CI; reproduce a shard with its selection and the same committed timings.

The parallel `backend-checks` job collects the full suite once and each shard once with
`pytest --collect-only -q`. `scripts/backend_test_shards.py check 4` compares node-ID
multisets: the union must equal the full collection and no node may occur twice. Collection
loads conftest configuration but does not execute the session migration fixture or connect
to Postgres. To run the contract locally without a database:

```sh
source scripts/test-env.sh
cd backend
uv run --locked --extra dev --extra ops --extra ocr python ../scripts/backend_test_shards.py check 4
```

Every successful CI run, including pushes to `main`, records setup/call/teardown timings
per shard and uploads the merged `backend-test-durations` artifact. Hosted BDD reruns use
the original timings and do not overwrite those measurements. New tests use the mean
stored duration until refreshed. After major suite changes or visible imbalance, download
the artifact from a successful main run and commit it as the refreshed baseline:

```sh
gh run download RUN_ID -R itskeKs/verwalino -n backend-test-durations -D backend
git diff -- backend/.test_durations
```

The workflow has read-only repository permissions and never commits the refresh itself.
Use a temporary download directory if a local timings file already exists, then copy the
downloaded `.test_durations` to `backend/.test_durations` before reviewing the diff.

## Where things live

| Path | Contents |
|---|---|
| `backend/app/` | `api/` routes, `services/`, `engine/` (pure calculations), `models/`, `workers/` |
| `backend/migrations/versions/` | Alembic migrations; committed ones are never edited |
| `backend/tests/` | unit and API tests; `bdd/` binds scenarios, `bdd/browser.py` owns browser fixtures, `bdd/report.py` writes reports, `bdd/steps/browser_*.py` shares browser steps |
| `features/` | API and `@browser` Gherkin scenarios (executable specs) |
| `.github/workflows/nightly-scenarios.yaml` | nightly self-host scenarios, 90-day report artifacts and deduplicated failure issues; hosted mode pending #18 |
| `.github/workflows/build-main-image.yaml` | filtered main pushes: publishes and signs SHA-only staging images without a release |
| `.github/workflows/deploy-staging.yaml` | successful `Build main image` completion via `workflow_run`: deploys that run's main SHA through the existing forced-command SSH key |
| `.github/workflows/canary.yaml`, `scripts/canary.py`, `scripts/test-canary.py` | 15-minute production API journey, sanitized reports, e-mail/issue alerts and fake HTTP contracts |
| `frontend/src/` | `pages/`, `components/`, `api/`, `lib/` |
| `website/` | bilingual static Astro site, checked multi-stage image and local Caddy file server |
| `.github/workflows/website.yml` | website PR checks; main-push image publication/signing and forced-command site deploy |
| `infra/docker-compose.site.yml`, `infra/Caddyfile.site` | optional hosted site network/hardening and public Caddy import; [runbook](docs/operations/website-hosting.md) |
| `install.sh` | one-command release installer; checksum/signature checks and digest-pinned portable init/start |
| `scripts/build-release-bundle.sh` | shared flat release payload/checksum assembly for releases and the install matrix |
| `scripts/install-scenarios.sh` | four install journeys on disposable VMs; JSON/Markdown reports and failure artifacts |
| `scripts/test-install.py`, `scripts/test-self-host.py`, `scripts/test-install-scenarios.py` | installer, portable stack and scenario-report contracts; fake downloads/Docker, no database |
| `.github/workflows/release-image.yaml` | stable tags only: publishes/signs release platforms and installer assets |
| `infra/docker-compose.staging.yml`, `infra/Caddyfile.staging` | hosted staging project and crawler-blocking shared Caddy site; [operator runbook](docs/operations/staging-and-canary.md) |
| `infra/docker-compose.hosted-proxy.yml`, `infra/Caddyfile.hosted` | opt-in shared wrapper; apply with the site overlay to preserve production, staging and website routing |
| `scripts/deploy-server.sh`, `scripts/test-server-deploy.py` | strict production/staging/site forced-command targets and fake server contracts |
| `.github/workflows/install-matrix.yaml` | nightly, manual and tag-triggered native install checks; reports retained for 90 days |
| `scripts/` | dev, self-host, deploy, checks; `acceptance/` holds legacy browser scripts (being migrated, #54) |
| `scripts/scenario_issues.py`, `scripts/test-scenario-issues.py` | stdlib report consumer and fake-GitHub filing contracts; `@known-flaky` failures are excluded |
| `DESIGN.md` | frontend design contract: tokens, layout, components, states, copy and formats; screenshots in `docs/assets/design/` |
| `docs/` | architecture, domain pages (`m*.md`), ADRs, operations, roadmap, feature status |
