# Executable specifications

The happy path of a behaviour, and the few scenarios a user would describe, live here as
Gherkin `.feature` files. They are run by pytest-bdd, so a spec that no longer holds fails
the build like any other test. API features and opt-in browser features share the same step library.

## Feature file or unit test?

| Goes into a `.feature` file | Stays an ordinary unit test (next to the code) |
|---|---|
| What a user would tell a colleague: "I preview the statement, then import it" | Validation, boundary values, malformed input |
| The happy path and the one or two scenarios that define the behaviour | Error handling, permissions per role, races, retries |
| Observable through the HTTP API or the real browser UI | Anything that needs a service, a model or the database directly |

Rules for a feature file:

- Plain English, user-level language: no ids, status codes, table names or JSON. Say "is not
  found", not "answers 404"; the step decides what that means.
- Deterministic: no network, no LLM, no clock-dependent data.
- Short: a handful of scenarios. If a scenario needs a long list of cases, it is a unit test.
- One feature per file, named after the behaviour in `snake_case`: `bank_statement_import.feature`.
  The title line is `Feature: <same words>`; the paragraph under it states the rule in prose.
- Scenario names are sentences that read as claims ("Importing the same statement again adds
  nothing"). They become the test names and the names in failure reports.

## Running

```sh
# One shell owns the shared test database for this run.
until mkdir /tmp/verwalino-testdb.lock 2>/dev/null; do sleep 10; done
trap 'rmdir /tmp/verwalino-testdb.lock' EXIT
source scripts/test-env.sh
cd backend && uv run --locked --extra dev --extra ops --extra ocr pytest tests/bdd -q
# Add -k "same_statement" to select one API scenario.
```

The API specs also run as part of the whole suite (`pytest` collects `tests/`).
Browser scenarios are skipped unless `--run-browser` is supplied; use the target below.

## How the step library is organised

- `features/<name>.feature` - the spec.
- `backend/tests/bdd/test_<name>.py` - one module per feature; it only binds the file
  (`scenarios(<absolute path>)`). No logic.
- `backend/tests/bdd/steps/` - the shared step library, one module per area
  (`common.py`, `organization.py`, `password.py`, `setup.py`, `bank_import.py`, plus `records.py` for
  master data created through the API and `browser_*.py` for browser journeys). Each `Given`/`When`/`Then` phrase is defined once
  and may be used by every feature, so phrase patterns must stay unique. Reuse a phrase
  before inventing a new one.
- `backend/tests/bdd/world.py` - `World`, the per-scenario state (who is signed in, the last
  answer, records created so far) and a synchronous facade over the async test clients.
- `backend/tests/bdd/conftest.py` - the `world` fixture, the registration of the step
  modules, and the hook that names scenario and step in a failure.

API steps talk to the app only through the HTTP API with the clients from
`backend/tests/conftest.py` (real FastAPI app, real database, row-level security). They do
not call services directly. Steps are plain functions: pytest-bdd does not await
`async def` steps, so `World` runs requests on the event loop of the test's async fixtures.

## When a scenario fails

The failure names the scenario and the step, for example:

```
E   AssertionError: GET /api/properties/<id> answered 200: {...}
E   Scenario: A property of another organization cannot be opened by its id
E   Step: Then the property is reported as not found  (features/organization_isolation.feature:24)
```

Fix the behaviour or, if the behaviour was changed on purpose, change the sentence in
the feature file in the same commit. Nightly failures become deduplicated issues as
described below.

## Scenario reports and nightly runs

`make test-scenarios-report` runs **all API and browser features** against the local
stack and writes `report/scenarios.json` and `report/summary.md`. It owns the shared
DB lock and stops its API/Vite processes, like `make test-browser`. Install the
browser prerequisites below first. `report/` is ignored by Git. For API-only runs,
hold the lock as shown above and add `--scenario-report /tmp/scenario-report` to
pytest; browser scenarios appear as skipped unless `--run-browser` is supplied.

Compared with pytest-bdd's built-in `--cucumberjson`, this reporter adds a portable
artifact index and run metadata. It also records skip reasons and setup errors
and writes `summary.md` for readable job summaries.

The plugin `backend/tests/bdd/report.py` is loaded by the backend conftest (or
explicitly with `-p tests.bdd.report` in a standalone pytest suite). Its JSON shape is:

```text
run: {started, finished, git_sha, mode, target_url?, runner}
scenarios: [{feature, scenario, tags, outcome, duration_s, error,
             steps: [{keyword, name, outcome, duration_s, error}], artifacts}]
```

`target_url` records the external or local browser origin; API-only local runs
omit it. Times are UTC ISO 8601; durations are seconds. Outcomes are `passed`,
`failed`, or `skipped`. Scenarios are ordered by feature file, source line and example id;
outline names and steps contain the rendered example values. Tags and artifact
paths are sorted. Each scenario occupies one Markdown table row in `summary.md`.
`error` is null or `{type, message}`; scenario errors also record setup/teardown
failures and skip reasons. Step errors provide the stable error kind for the
`feature::scenario::step::error kind` fingerprint. Unexecuted steps are skipped.
Artifacts use paths **relative to the report directory**, so a downloaded artifact
remains portable. Failing API scenarios include `last-response.json` with the last
remembered response body (no request bodies, headers or cookies). Browser failures
also include a screenshot, `trace.zip` and available local backend/Vite logs.
Traces are discarded on success. There is no synthetic Vite log for a built self-host image.

### Failure issues

After uploading the report, the nightly workflow runs
[`scripts/scenario_issues.py`](../scripts/scenario_issues.py). It groups failed steps by
`feature::scenario::step::error kind`, hashes that string with SHA-256 to a stable
16-character token, and stores both the readable fingerprint and the exact
`<!-- scenario-fingerprint: <token> -->` marker in the issue body. The error kind is
the structured exception class; if absent, it is the first error line with ids,
numbers, paths, timestamps and request ids normalized. Setup/teardown failures use
the scenario error and a `setup/teardown` step. Browser/API variants with the same
fingerprint share one lookup and one issue per run; artifact names are combined.

The reporter searches both open and closed issues with label `scenario` and the
token in the body, then checks the complete marker line for an exact match. A new
fingerprint creates an issue from the [failure template](../.github/ISSUE_TEMPLATE/scenario-failure.md),
including the first run, errors, artifact names and a scenario-specific
`make test-scenarios-report` command (an outline selects all examples of its source
template). The labels are `scenario` plus feature-level `@area:<name>` tags, if
present; missing required labels are created, and no milestone is assigned. An open issue receives
one comment with the run URL, date and mode; the step error is included only
when it changed since the previous report. A closed issue is reopened, labelled
`regression`, and receives the run comment. Passing and skipped scenarios do
nothing; issues are never automatically closed. Lookups finish before the write
batch, and searches and writes are sequential with at least two seconds between requests.
Error text in issue bodies and comments retains at most 20,000 original characters,
split between the head and tail with a marker counting omitted characters and pointing to the full run artifact.
Each run creates at most 10 new failure issues plus one summary issue listing any
remaining scenario names and fingerprints; comments and reopenings are not capped.

To silence a known flaky scenario, add `@known-flaky` above its `Scenario:` (or
above `Feature:` to silence the whole feature). It still runs and appears in the
report, but its failures are excluded from issue filing. Remove the tag after
fixing the flakiness. Manual `workflow_dispatch` runs file nothing unless the
boolean input **`file_issues`** is enabled. The job has `issues: write`, uses
`GITHUB_TOKEN` through `gh api`, and serializes runs to avoid concurrent filing.
If scenario execution produces no JSON report, it logs that fact and files nothing.

Preview the plan with read-only GitHub access:

```sh
python3 scripts/scenario_issues.py --report report/scenarios.json \
  --repo itskeKs/verwalino --run-url https://github.com/itskeKs/verwalino/actions/runs/123 \
  --mode selfhost --dry-run
make test-scenario-issues  # stdlib tests with fake GitHub, no network or database
```

The first real filing is unverified until the first nightly run with a failure.

### External disposable targets

Set `VERWALINO_TARGET_URL` to the installation's origin, for example
`http://127.0.0.1:<disposable-port>`, plus `VERWALINO_TARGET_ADMIN_EMAIL` and
`VERWALINO_TARGET_ADMIN_PASSWORD`. Then run:

```sh
cd backend
uv run --locked --extra dev --extra ops --extra ocr pytest tests/bdd \
  --run-browser --scenario-report ../report -q
```

This path starts **no uvicorn/Vite**, runs no migrations or local DB fixtures, and
uses the target URL for both API clients and the browser. Database environment
variables and the local test DB lock are unnecessary for this path. Browser
requests are limited to the selected origin. The feature name `Verwaltung Alpha`
aliases the signed-in admin's organization, regardless of its actual name.
Password scenarios create disposable users through `/api/users`; they never change
the bootstrap admin's password. Use only a dedicated disposable installation:
scenarios create synthetic records and leave them there, and traces/responses can
contain their data and session cookies. Keep credentials out of shell tracing.
Before any scenario runs, the session signs in as the admin and requires
`GET /api/properties` to return an empty list for that organization. A populated
target aborts with "target is not a fresh installation; refusing to write scenario
data"; failed authentication or an unverifiable list also aborts. Start with a
new disposable installation for each run, including reruns after a partial failure.

**Isolation decision:** all nine examples of `organization_isolation.feature` are
skipped on external targets because only one bootstrap organization is configured.
The three `first_run_setup.feature` scenarios are also skipped: they require a
fresh installation, while the external target already has its bootstrap organization.
Each skipped scenario records its reason in the report. Local runs continue to
exercise both organizations, all isolation scenarios and first-run setup.
`make test-bdd`, `make test-browser` and `make test-scenarios-report` explicitly
select the local stack even if target variables are inherited; use direct pytest
for external targets.

### Nightly history

[Nightly scenarios](../.github/workflows/nightly-scenarios.yaml) runs at **03:00 UTC**
and through `workflow_dispatch`, with one run per workflow/ref at a time. It
initializes the installer's own `verwalino` Compose project on port 18080, builds
the checkout on the GitHub runner, starts/migrates it,
redacts the one-time browser setup token from installer logs, bootstraps an admin
through the reviewed CLI alternative `self-host.sh admin --password-stdin`, waits for readiness,
and runs all features against that instance. The job collects installer logs and
the last 2 MiB of backend logs into `report/logs/` after execution, propagates pytest
failures, always attempts normal stop, publishes the Markdown job summary, and
uploads `report/` as `scenarios-selfhost-<UTC date>`.
Download it from the workflow run's **Artifacts** area; retention is **90 days**.
Artifacts are the history store; there is no dashboard or separate history database.
The existing installer has no backup command, so the job only stops the disposable
installation and the GitHub runner's lifecycle removes its storage.

Only `mode: [selfhost]` is implemented. The matrix has a marked slot for hosted
mode after the deployment-mode switch in **#18**; it contains no inactive hosted
execution path. Report metadata defaults to `mode=selfhost`; the workflow sets
`VERWALINO_SCENARIO_MODE` from its matrix. Hosted execution remains pending #18.
**The self-host startup and full nightly integration are unverified until the first
GitHub runner run.** Local checks parse the workflow and test the local runners;
they do not start the self-host stack.

## Browser features

Browser journeys live alongside API specs in `features/`, tagged `@browser`.
Use the UI only for the behaviour under test; prerequisites use the existing API
`World`, with explicit source audiences and accounting grants. No SQL setup, network
services, LLM calls, routing mocks, or clock-dependent business dates are needed.
Scenarios use stable names derived from the scenario name, a 1440 × 1000 viewport,
and an explicit 390 × 844 viewport for the existing phone cancellation journey.
German control names are quoted where the step identifies a UI action.

**Runner decision:** pytest-playwright keeps pytest-bdd, the API World and one Python
step library instead of adding playwright-bdd and a second Node test runner.
Playwright uses a separate session event loop through `browser.py`, so synchronous
BDD steps can drive browser actions without interfering with the API World's
per-scenario async fixtures. It logs in through the real UI once per session, keeps
authentication in memory and opens a fresh authenticated context for each scenario.
Only the selected application origin is allowed; pages are not mocked.

One-time setup (repeat the browser install when the locked Playwright version changes):

```sh
cd backend
uv sync --locked --extra dev --extra ops --extra ocr
uv run --locked --extra dev playwright install chromium
```

From the repository root:

```sh
make test-browser
make test-browser PYTEST_ARGS="-q -k rent_change"  # select a journey
make test-browser PYTEST_ARGS="-q --headed"       # inspect it interactively
```

The `make test-backend`, `make test-bdd` and `make test-browser` targets acquire
and release `/tmp/verwalino-testdb.lock`, load `scripts/test-env.sh`, and run the
locked dependency set. Browser fixtures start this checkout's API on 127.0.0.1:18000
and Vite on 127.0.0.1:13000, refusing occupied ports. They stop both process groups
on success or failure before the lock is released. The target does not start a
worker or use an existing dev stack; start only the disposable database first.
Do not wrap these targets in another acquisition of the same lock. Direct pytest
commands and separately running dev stacks must hold that lock too; release it between runs.

**CI decision:** the separate browser job runs `make test-browser` on PRs. The locked
Chromium download and `playwright install --with-deps chromium` give a deterministic,
short setup, with no host Chrome dependency; the job uses the same disposable
Postgres container and frontend lockfile as local runs. The ordinary backend job
continues to run only API scenarios.

Batch 1 replaces the seven legacy scripts for import/assignment, rent changes,
deposits, interest/PDF, selected drafts and scoped dunning. Parsing/deduplication,
DTO fields, permission matrices, concurrency and calculation edge cases stay in API
or component tests (including `bank_statement_import.feature`). Browser scenarios
retain visible review, confirmation, persistence, navigation and cancellation.
Other scripts in `scripts/acceptance/` remain for later batches; this batch adds no
new product journeys.
