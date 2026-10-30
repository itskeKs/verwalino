# Agent notes for Verwalino

Verwalino is a property-management app (rental, WEG, SEV). FastAPI + SQLAlchemy async + Postgres
with row-level security in `backend/`, React + Vite + TypeScript in `frontend/`. It runs from one
image in two ways: self-hosted by anyone, and as a free hosted multi-organization service.
Where it is going: [docs/roadmap.md](docs/roadmap.md). What exists: [docs/feature-status.md](docs/feature-status.md).
Human-facing workflow and commands: [CONTRIBUTING.md](CONTRIBUTING.md).

## Language

- The **software is German**: UI texts, labels, routes shown to users, error messages, generated
  PDFs, letters and emails.
- **Everything else is English**: documentation, issues, code comments, docstrings, test names,
  feature files and commit messages. When English text quotes what the software shows, keep that
  text German, verbatim.

## Work comes from issues

- A task is a GitHub issue written as a spec: What → Why it happens → Proposed change →
  Acceptance → Out of scope (`.github/ISSUE_TEMPLATE/task.md`). Read it with
  `gh issue view N -R itskeKs/verwalino`. There is no task list file.
- Scope is the issue's acceptance list: nothing less, nothing unasked. One issue, one branch, one
  PR. `Closes #N` only when every acceptance box is done; otherwise `Refs #N` plus what remains.
- A finding outside the scope becomes a new issue (or a line in the PR description), not extra code.
- Progress is code, tests and a commit. Do not create state, contract, receipt, evidence, handoff,
  hash or audit files, do not build repair or review chains, and write no new plan, log or status
  documents; decisions go into the issue,
  the PR description, or an ADR under `docs/adr/` when they bind future work.

## Specs and tests

- The happy path of a behaviour is a Gherkin scenario in repo-root `features/*.feature`, run by
  pytest-bdd from `backend/tests/bdd/` through the HTTP API with the shared step library
  ([features/README.md](features/README.md)). Deterministic, no network, no LLM.
- Edge and error cases are unit tests next to the code, in the area's **existing** test file,
  reusing its helpers; test names are sentences.
- Red first: every new scenario and test fails without its change. Show it once in the PR.
- Every new or changed API route keeps organization isolation: covered by
  `features/organization_isolation.feature` and the route sweep (#4).
- Run tests selectively while working (`uv run pytest tests/test_x.py -k ...`,
  `npm test -- <file>`), `make lint` before every push, and the whole suite (`make test`) only
  before a merge; CI runs it on every PR.

## Gates

| Command | Runs |
|---|---|
| `make lint` | ruff (backend), eslint and `tsc --noEmit` (frontend) |
| `make test` | installer contract tests, backend pytest (with BDD scenarios), frontend vitest and typecheck |
| `make test-bdd` | only the `features/` scenarios |
| `make docs-check` | relative links in all Markdown outside `docs/archive/` |
| `make hooks` | enables `.githooks/pre-commit` (ruff and eslint on staged code, docs link check) |

CI (`.github/workflows/tests-on-pr.yaml`) runs the same on every PR. A red job is fixed, not
skipped. Do not bypass the hook to land code.

## Documentation contract

| When you change … | update in the same PR |
|---|---|
| anything a user notices | briefly, the page that owns the area (`README.md`, `docs/architecture.md`, `docs/m*.md`, `docs/operations/`) |
| a feature's status | its row in `docs/feature-status.md` |
| deployment, installation, configuration keys | `DEPLOY.md`, `.env.example`, `scripts/self-host.sh` help text |
| an architectural decision that binds later work | a new ADR in `docs/adr/` |
| a rename of a file, route, key or command | every mention: `git grep -n '<old>'` comes back empty outside `docs/archive/` |

Docs state exactly what the code does. `docs/domain-reference.md` is the reference product's
features and workflows (scope and interaction flow); read it selectively with `rg -n`.
`docs/archive/` is legacy: do not read it or treat it as rules unless the user asks for a
specific question.

## Design rules

1. One source of truth: when several places decide the same thing (mode, permission, status),
   they read one table or function.
2. No configuration keys for tuning numbers or lists; documented constants. A key only for a real
   on/off switch or a deployment fact (host, secret, path).
3. State that must survive a restart lives in the database, not in memory.
4. No field, parameter or interface without a reader in the same PR.
5. Simple checks after the action beat predictive checks before it.
6. Classify errors by structured fields (codes, types), never by message text.
7. UI reports real outcomes only; no placeholder text, no "everything OK" when there is nothing
   to report. New UI elements reuse existing components and styles.
8. Test the long cases: four-digit amounts, long German names, narrow screens, many rows.
9. Use what exists and works (libraries, modules, fixtures) before writing new mechanisms.

## Commits

`type(scope): lowercase imperative summary` (~70 chars), e.g. `feat(bank): …`, `fix(auth): …`,
`test(bdd): …`, `docs(roadmap): …`, `ci: …`. Body: cause → change → tests. `Refs #N` or
`Closes #N` at the end. Never push unless the user asks.

## Context economy

One issue per session, cut to fit a 64k context. Read only the files the issue names; read large
files with `rg -n` and line ranges.

## Databases and containers

- The production instance (`docker-compose.prod.yml`, volumes `pgdata`, `documents`) holds real
  data: never `docker compose down -v`, never delete volumes, never test against that database.
- The test database is exclusively the container `infra-postgres-1` on `127.0.0.1:55432`
  (`infra/docker-compose.declaration-test.yml`, environment via `source scripts/test-env.sh`).
  Do not start a database of your own and do not add another compose file. Backend tests
  truncate tables: never run two test processes against it at the same time.
- Call `docker compose` only with `-f infra/docker-compose.declaration-test.yml`. Never without
  `-f`, never with `docker-compose.prod.yml` or a new file: a `db` service in the project
  `hausverwaltung` would otherwise replace the production database container.
  Exception: separately authorized portable acceptance may use the reviewed
  `infra/docker-compose.yml` from this checkout or a registered Git worktree, with
  project `verwalino` and its explicit private `.env.selfhost`, only for the existing
  installer's build/pull, readiness/start, one-shot migration/admin bootstrap,
  status/logs and normal stop operations. It must never use alpha containers,
  volumes or networks, delete volumes, or serve as a backend test database.
  Separately authorized Caddy validation may use only the reviewed Caddyfile as
  a read-only bind mount, with network `none`, no published ports, a read-only
  root filesystem, all capabilities dropped and `no-new-privileges`, executing
  only `caddy validate`. These exceptions do not authorize execution or deployment.
- Never change a committed migration; never empty `downgrade()` to make tests pass.
