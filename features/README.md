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
  (`common.py`, `organization.py`, `password.py`, `bank_import.py`, plus `records.py` for
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

A failing scenario in CI or the nightly run is reported as an issue titled with the scenario
name. Fix the behaviour or, if the behaviour was changed on purpose, change the sentence in
the feature file in the same commit.

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
Only the local Vite origin is allowed; pages are not mocked.

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
