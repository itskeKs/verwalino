# Executable specifications

The happy path of a behaviour, and the few scenarios a user would describe, live here as
Gherkin `.feature` files. They are run by pytest-bdd, so a spec that no longer holds fails
the build like any other test. This is a pilot: three features, grown on demand.

## Feature file or unit test?

| Goes into a `.feature` file | Stays an ordinary unit test (next to the code) |
|---|---|
| What a user would tell a colleague: "I preview the statement, then import it" | Validation, boundary values, malformed input |
| The happy path and the one or two scenarios that define the behaviour | Error handling, permissions per role, races, retries |
| Observable through the HTTP API | Anything that needs a service, a model or the database directly |

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
source scripts/test-env.sh                 # test database (never the production one)
cd backend && uv run pytest tests/bdd      # all features
uv run pytest tests/bdd -k "same_statement"  # one scenario
```

The specs also run as part of the whole suite (`pytest` collects `tests/`).

## How the step library is organised

- `features/<name>.feature` - the spec.
- `backend/tests/bdd/test_<name>.py` - one module per feature; it only binds the file
  (`scenarios(<absolute path>)`). No logic.
- `backend/tests/bdd/steps/` - the shared step library, one module per area
  (`common.py`, `organization.py`, `password.py`, `bank_import.py`, plus `records.py` for
  master data created through the API). Each `Given`/`When`/`Then` phrase is defined once
  and may be used by every feature, so phrase patterns must stay unique. Reuse a phrase
  before inventing a new one.
- `backend/tests/bdd/world.py` - `World`, the per-scenario state (who is signed in, the last
  answer, records created so far) and a synchronous facade over the async test clients.
- `backend/tests/bdd/conftest.py` - the `world` fixture, the registration of the step
  modules, and the hook that names scenario and step in a failure.

Steps talk to the app only through the HTTP API with the clients from
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
