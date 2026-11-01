# Agent notes for Verwalino

Verwalino is a property-management app for rental management, homeowners' associations (WEG) and
individual unit management (SEV): FastAPI + SQLAlchemy async + Postgres with row-level security in
`backend/`, React + Vite + TypeScript in `frontend/`. One image runs in two ways: self-hosted by
anyone, and as a free hosted service run by fogu UG (haftungsbeschränkt), where every
property-management company is one isolated organization. Licence AGPL-3.0; the repository is
public. Never change its visibility without the owner's (Chris) explicit decision.
Where it is going: [docs/roadmap.md](docs/roadmap.md). What exists: [docs/feature-status.md](docs/feature-status.md).
Frontend design contract: [DESIGN.md](DESIGN.md). Human-facing workflow and commands: [CONTRIBUTING.md](CONTRIBUTING.md).

The software is German; documentation, issues, code comments, tests and commits are English
(German costs about twice the tokens).

## What the code does not tell you

- Weight matters: one 12 GB VPS is meant to serve 30–50 organizations; the app container runs with
  768 MiB and one CPU (`WEB_CONCURRENCY=2`). Prefer small dependencies and bounded work; long or
  CPU-heavy work belongs in an existing `backend/app/workers/` process, never in the request path.
  Details in the [architecture rule](.claude/rules/architecture.md).
- Legal correctness is not negotiable: GoBD immutability, GDPR, and organization isolation (RLS,
  composite foreign keys, 404 for foreign IDs). A shortcut there is a defect even when tests pass.
- Product decisions are the owner's and live in issues (#27–#33, decision comments); the
  [domain rule](.claude/rules/domain.md) lists them. Code that still implements an older
  provisional value is debt with its own issue, not licence to keep it.
- A production instance with real data runs on the owner's machine (Databases and containers below).
- LLM and OCR work has a provider boundary and cost accounting
  ([AI pipeline rule](.claude/rules/ai-pipeline.md)); provider-neutral runtime (#66) and an OCR
  service (#65) are open decisions, not things to pre-empt in passing.

## Agent host integrations

Claude Code reads `CLAUDE.md` (a symlink to this file) and loads `.claude/rules/*.md`; Coddy is
used with the same tree and runs the blind cross-review (`.coddy/crossreview.json`); its rule
loading is not tested here. Cursor loads the equivalent `.cursor/rules/*.mdc`. Codex reads AGENTS.md and uses
`.codex/hooks.json` to attach always-on rules at session start and scoped rules before
`apply_patch`, `Edit` or `Write`. Approve the hooks with `/hooks` once per clone; see
[CONTRIBUTING.md](CONTRIBUTING.md#agent-host-setup).

Rules Sync: a rule changes in both trees in the same commit, with equivalent bodies; a Claude rule
without `paths` is Cursor `alwaysApply: true`, otherwise `paths` equals `globs` with
`alwaysApply: false`. The Codex hook reads the Cursor tree directly and needs no index.
`make test-agent-rules` checks parity, the symlink and the hook. Scoped rules add context, not
enforcement; every baseline and safety rule here stays in force.

## Work comes from issues

- A task is a GitHub issue written as a spec: What → Why it happens → Proposed change →
  User check → Acceptance → Out of scope (`.github/ISSUE_TEMPLATE/task.md`). Read it with
  `gh issue view N -R itskeKs/verwalino`. There is no task list file.
- Scope is the issue's acceptance list: nothing less, nothing unasked. Do not add request or
  response fields, parameters, configuration keys, endpoints or options the issue does not name,
  and do not refactor neighbours in passing. One issue, one branch, one PR. `Closes #N` only when
  every acceptance box is done; otherwise `Refs #N` plus what remains.
- Red first: the scenario and the unit tests fail without the change, then the smallest change
  turns them green, then refactor with the tests still green.
- A finding outside the scope becomes a new issue (or a line in the PR description), not extra code.
- Implementers push only their own PR branch, open the PR and stop; the maintainer review session
  merges, nobody else. Never push to `main`. A risky PR (migrations, isolation or RLS, money,
  GoBD, auth, backup and restore) gets a parallel,
  blind cross-review by independent reviewers before the merge (`.coddy/crossreview.json`);
  that is recommended, not a chain.
- Whoever creates a git worktree for an issue or a review round removes it and deletes its local
  branch as soon as the PR is merged or closed (`git worktree remove DIR && git branch -D BRANCH`,
  after checking the merge landed, the tree is clean and nothing runs there). Before starting a
  new task, remove your own finished worktrees. Worktrees live outside the checkout under
  `../verwalino-issue-N` or `../verwalino-review-N`.
- Progress is code, tests and a commit. Do not create state, contract, receipt, evidence, handoff,
  hash or audit files, do not build sequential repair or review chains that pass such files
  along, and write no new plan, log or status documents; decisions go into the issue, the PR
  description, or an ADR under `docs/adr/` when they bind future work.

## Session cleanup

- Every session removes what it created when its task is merged, closed or abandoned.
  Before a new task, check for and remove your own leftovers.
- Remove your own worktrees and local branches after the PR is merged or closed; for an
  abandoned task without a PR, first preserve work that must be kept. Check the PR state,
  a clean tree and stopped processes before removal; never discard uncommitted work.
- Remove your own separately authorized acceptance/verifier stacks completely:
  containers, volumes (including anonymous volumes), networks and local images used only
  by that project. Stop your own dev servers, free their ports and terminate every
  background process you started, including installer-harness children.
- Remove scratch files and temporary artefacts you wrote (`/tmp/<prefix>-*`, scratch
  directories and screenshots outside tracked docs); remove only your own inactive lock
  files. Keep committed deliverables.
- Never touch other sessions' resources: worktrees, branches, containers, volumes,
  networks, images, processes or ports (including `verwalino-review-*`,
  `verwalino-abnahme-*`, `.claude/worktrees/*` and other issue worktrees).
  A matching name or label alone does not establish ownership. Never prune Docker globally.
- Leave production (`docker-compose.prod.yml`, project `hausverwaltung` on the owner's Mac,
  `verwalino` on the hosted server) and shared test project `infra` untouched.
  Never remove `/tmp/verwalino-testdb.lock` while another run holds it. The only repair is
  starting the shared test DB with
  `docker compose -f infra/docker-compose.declaration-test.yml up -d postgres`
  when it is down, while holding its otherwise free lock; never restart a running DB.
- Report cleanup in one line in the status/report, never in a repository file.

## Verification

- Follow the [testing rule](.claude/rules/testing.md) and [features/README.md](features/README.md).
- Every new or changed API route keeps organization isolation: covered by
  `features/organization_isolation.feature` and the route sweep
  `backend/tests/test_route_isolation_sweep.py`, which fails until a new route has a probe in
  `backend/tests/route_isolation/` or an exemption with a reason
  ([architecture.md 7.4a](docs/architecture.md#74a-route-sweep-backendteststest_route_isolation_sweeppy)).
- UI work is never blind: a change under `frontend/src/` is opened in a real browser at desktop
  width and 360 px before the PR, with before/after screenshots in the PR body, taken on the test
  database with synthetic data, never on production ([frontend rule](.claude/rules/frontend-ui.md)).
- Done means a user can do it. An issue whose change a user notices carries a „User check“
  written before the change ([docs/acceptance/README.md](docs/acceptance/README.md)); the
  PR is merged only after it passed on the PR's state: by a blind acceptance run that sees only
  the running product (a portable install, the separately authorized portable acceptance of
  "Databases and containers" below), or by the owner where no agent can go. The core journeys in
  [docs/acceptance/journeys.md](docs/acceptance/journeys.md) run blind at every milestone, and
  each has an automated twin in `features/journeys/` that runs in CI; a confirmed acceptance
  finding first fails as a step there, then gets fixed. Expected values come from the domain,
  never from the implementation.
- Run tests selectively, `make lint` before every push, and `make test` before a merge.

## Gates

| Command | Runs |
|---|---|
| `make lint` | ruff (backend), eslint and `tsc -b` (frontend) |
| `make test` | agent rules, installer contracts, backend pytest (with BDD), frontend vitest and typecheck |
| `make test-agent-rules` | paired rule parity, `CLAUDE.md` symlink and Codex hook contracts (no DB) |
| `make test-bdd` | API scenarios in `features/` |
| `make test-browser` | headless `@browser` scenarios; owns the shared DB lock and local stack |
| `make docs-check` | relative links in all Markdown outside `docs/archive/` |
| `make hooks` | enables `.githooks/pre-commit` (ruff and eslint on staged code, docs link check) |

CI (`.github/workflows/tests-on-pr.yaml`) runs the same on every PR. A red job is fixed, not
skipped. Do not bypass the hook to land code.

Update docs in the same PR under the [documentation contract](.claude/rules/docs.md); a change to the
look or copy of the frontend updates [DESIGN.md](DESIGN.md).
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
   to report. Follow the [frontend rule](.claude/rules/frontend-ui.md) and [DESIGN.md](DESIGN.md) for components and formats.
8. Test representative long inputs and many rows.
9. Use what exists and works (libraries, modules, fixtures) before writing new mechanisms.

## Commits

`type(scope): lowercase imperative summary` (~70 chars), e.g. `feat(bank): …`, `fix(auth): …`,
`test(bdd): …`, `docs(roadmap): …`, `ci: …`. Body: cause → change → tests. `Refs #N` or
`Closes #N` at the end. Push only the branch of the PR you work on; never `main`.

No attribution anywhere: no `Co-Authored-By` trailer for an assistant, no "Generated with …" line,
no mention of Claude, Codex or any other AI or agent as author — in commits, PR descriptions,
issues, comments, code comments or docs. Naming a tool the repo supports (`.claude/rules`, the
Codex hook) is subject matter, not attribution.

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
  status/logs, normal stop and final cleanup operations. It must never use alpha containers,
  volumes or networks, or serve as a backend test database. Final cleanup may remove only
  the session's disposable resources labelled `com.docker.compose.project=verwalino` and
  anonymous volumes attached exclusively to its labelled containers, on a host where that
  project is not production and no other session uses it; follow the
  [acceptance cleanup procedure](docs/operations/acceptance-cleanup.md).
  Separately authorized Caddy validation may use only the reviewed Caddyfile as
  a read-only bind mount, with network `none`, no published ports, a read-only
  root filesystem, all capabilities dropped except `NET_BIND_SERVICE` (the caddy
  binary carries that file capability; `--cap-drop ALL --cap-add NET_BIND_SERVICE`)
  and `no-new-privileges`, executing only `caddy validate`. These exceptions do not authorize execution or deployment.
- Never change a committed migration; never empty `downgrade()` to make tests pass. A new
  revision becomes the release target everywhere (`backend/app/deployment.py`, `DEPLOY.md`,
  upgrade tests); production moves only by a release.
