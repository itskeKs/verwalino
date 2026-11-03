"""Organization isolation sweep over every API route (issue #4).

Every route of the app is either swept or exempt, and a new route fails
``test_every_route_is_swept_or_exempt`` until it is one of the two:

* swept: a ``Probe`` in ``PROBES`` of the area module that owns its router
  (``tests/route_isolation/{core,finance,documents,access}.py``; the probe format is explained in
  ``tests/route_isolation/__init__.py``);
* exempt: an entry with its reason in ``EXEMPT`` below.

The area tests sign in as the administrator of a fresh organization B (fixture ``sweep``), so a
refusal is never a role refusal, and send each probe. How an answer is judged:

| The request ...                            | passes when                                              |
|--------------------------------------------|----------------------------------------------------------|
| carries ids of another organization's      | the status is the probe's (404 unless the table gives a  |
| records (path, query, header or body)      | reason), a write is never 2xx, and status and body equal |
|                                            | the answer for the same request with ids that exist      |
|                                            | nowhere (the answer cannot reveal that the record exists)|
| carries no foreign ids (lists, own creates)| a read answers 2xx; any answer is below 500              |
| any request                                | it reaches the route (no FastAPI 422 for the request     |
|                                            | shape), the answer contains no id of another             |
|                                            | organization's record unless the request sent it, and    |
|                                            | after a write no record of another organization changed  |

"Changed" is measured on every table of ``TENANT_TABLES`` (``test_rls_coverage.py``): per table
the number of rows outside organization B and the newest row version (``xmin``) among them.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.main import app
from app.core.config import settings
from tests.conftest import MIGRATOR_URL, _api_test_client, _make_session_cookie
from tests.route_isolation import (
    PATH_PARAMETER,
    UUID_PATTERN,
    Probe,
    build_request,
    is_request_validation_error,
    is_uuid,
    normalized,
    twin_ids,
)
from tests.route_isolation import access, core, documents, finance
# Each area exports its seed fixture and the shared fixtures that one depends on (``__all__``).
from tests.route_isolation.access import *  # noqa: F401,F403
from tests.route_isolation.core import *  # noqa: F401,F403
from tests.route_isolation.documents import *  # noqa: F401,F403
from tests.route_isolation.finance import *  # noqa: F401,F403
from tests.test_rls_coverage import TENANT_TABLES

AREAS = {"core": core, "finance": finance, "documents": documents, "access": access}

# Routes the sweep does not call, each with the reason. Keys are "METHOD /path" as FastAPI
# registers them.
EXEMPT: dict[str, str] = {
    **{f"POST /api/auth/password-reset{suffix}": "organization-free mailbox proof; tested in password_reset.feature and test_password_reset.py"
       for suffix in ("", "/confirm")},
    **core.EXEMPT,
    "GET /api/health": "organization-free: answers without a session and reads no organization data",
    "GET /api/health/live": "organization-free: answers without a session and reads no organization data",
    "GET /api/health/ready": "organization-free: answers without a session and reads no organization data",
    "POST /api/auth/login": "runs before an organization is known; sign-in rules are tested in test_auth_hardening.py",
    "POST /api/auth/logout": "ends the session the sweep signs in with; tested in test_auth_hardening.py",
    "POST /api/auth/password": "changes the sweeping user's own password and session generation; "
    "tested in features/password_change.feature",
}


def app_routes() -> list[str]:
    """Every API route as "METHOD /path", including those of included routers."""
    keys = []
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            keys += [f"{method} {context.path}" for method in sorted(context.methods)]
    return keys


def all_probes() -> dict[str, Probe]:
    merged: dict[str, Probe] = {}
    for name, area in AREAS.items():
        for key, probe in area.PROBES.items():
            assert key not in merged, f"{key} is listed in more than one area (second: {name})"
            merged[key] = probe
    return merged


def test_every_route_is_swept_or_exempt():
    routes, probes = set(app_routes()), all_probes()
    unclassified = sorted(routes - probes.keys() - EXEMPT.keys())
    assert not unclassified, (
        "Routes that are neither swept nor exempt. Add a Probe to PROBES of the area module that "
        "owns the router (tests/route_isolation/), or an EXEMPT entry with a reason:\n  "
        + "\n  ".join(unclassified)
    )
    assert not probes.keys() & EXEMPT.keys(), f"Both swept and exempt: {sorted(probes.keys() & EXEMPT.keys())}"
    stale = sorted((probes.keys() | EXEMPT.keys()) - routes)
    assert not stale, "Listed routes that the app no longer has:\n  " + "\n  ".join(stale)


# -- the sweep --------------------------------------------------------------------------------


class Sweep:
    """Organization B's administrator calling routes, and what the migrator sees meanwhile."""

    def __init__(self, client: httpx.AsyncClient, b: uuid.UUID, admin: uuid.UUID, engine: AsyncEngine):
        self.client, self.b, self.admin, self.engine = client, b, admin, engine
        self.openapi = app.openapi()
        self.foreign_ids: set[str] = set()
        self.state: dict[str, tuple[int, int]] = {}

    async def scalar_rows(self, sql: str, **params) -> list:
        async with self.engine.connect() as connection:
            return list((await connection.execute(text(sql), {"b": self.b, **params})).mappings())

    async def foreign_state(self) -> dict[str, tuple[int, int]]:
        """Per tenant table: rows outside organization B and their newest row version."""
        union = " UNION ALL ".join(
            f"SELECT '{table}' AS t, count(*) AS n, coalesce(max(x.xmin::text::bigint), 0) AS v "
            f'FROM "{table}" x WHERE x.organization_id IS DISTINCT FROM :b'
            for table in sorted(TENANT_TABLES)
        )
        return {r["t"]: (r["n"], r["v"]) for r in await self.scalar_rows(union)}

    async def collect_foreign_ids(self) -> set[str]:
        tables = [
            r["table_name"]
            for r in await self.scalar_rows(
                "SELECT table_name FROM information_schema.columns WHERE table_schema='public' "
                "AND column_name='id' AND data_type='uuid'"
            )
            if r["table_name"] in TENANT_TABLES
        ]
        union = " UNION ALL ".join(
            f'SELECT x.id::text AS id FROM "{table}" x WHERE x.organization_id IS DISTINCT FROM :b' for table in tables
        )
        rows = await self.scalar_rows(union + " UNION ALL SELECT id::text FROM organization WHERE id <> :b")
        return {r["id"] for r in rows}

    async def send(self, request) -> httpx.Response:
        """The answer; an exception the route raises counts as the 500 a server would send."""
        try:
            return await self.client.request(request.method, request.url, **request.send_kwargs())
        except Exception as error:  # the ASGI test transport re-raises what the app raises
            return httpx.Response(500, text=f"the route raised {type(error).__name__}: {str(error)[:300]}")


def leaked(answer, sent: dict[str, str], foreign_ids: set[str]) -> set[str]:
    found = {value.lower() for value in UUID_PATTERN.findall(answer.content.decode("latin-1"))}
    return (found & foreign_ids) - {value.lower() for value in sent.values()}


async def sweep_route(sweep: Sweep, key: str, probe: Probe) -> list[str]:
    method, path = key.split(" ", 1)
    operation = sweep.openapi["paths"][path][method.lower()]
    ids: dict[str, str] = {}
    if probe.ids:
        rows = await sweep.scalar_rows(probe.ids)
        if not rows:
            return [f"{key}: no record of another organization matches the probe; seed one ({probe.ids})"]
        ids = {name: str(value) for name, value in rows[0].items()}
    missing = set(PATH_PARAMETER.findall(path)) - ids.keys()
    if missing:
        return [f"{key}: the probe gives no value for the path parameter(s) {sorted(missing)}"]

    problems = []
    answer = await sweep.send(build_request(method, path, operation, sweep.openapi["components"]["schemas"], probe, ids))
    payload = _json(answer)
    shown = f"{answer.status_code} {answer.text[:200]}"
    carries_foreign = any(is_uuid(value) for value in ids.values())
    if answer.status_code >= 500:
        problems.append(f"server error: {shown}")
    elif is_request_validation_error(answer.status_code, payload):
        problems.append(f"FastAPI refused the request shape before the route ran; fix the probe: {shown}")
    elif carries_foreign:
        if answer.status_code != probe.status:
            problems.append(f"expected {probe.status} for another organization's record, got {shown}")
        if method != "GET" and answer.status_code < 300:
            problems.append(f"accepted a write with another organization's ids: {shown}")
        twin = twin_ids(ids)
        unknown = await sweep.send(build_request(method, path, operation, sweep.openapi["components"]["schemas"], probe, twin))
        if (answer.status_code, normalized(answer.text, ids)) != (unknown.status_code, normalized(unknown.text, twin)):
            problems.append(
                "answers differently for an id that exists nowhere, which reveals the record: "
                f"foreign {shown} / unknown {unknown.status_code} {unknown.text[:200]}"
            )
        if leaks := leaked(unknown, twin, sweep.foreign_ids):
            problems.append(f"the answer for unknown ids contains ids of another organization: {sorted(leaks)[:5]}")
    elif method == "GET" and answer.status_code >= 300:
        problems.append(f"a read without foreign ids must answer to be scanned for leaks, got {shown}")
    required_query = {p["name"] for p in operation.get("parameters", []) if p["in"] == "query" and p.get("required")}
    if method == "GET" and carries_foreign and not PATH_PARAMETER.search(path) and not required_query & ids.keys():
        # A list probed with a foreign filter is also read unfiltered: that is where a list leaks.
        plain = await sweep.send(build_request(method, path, operation, sweep.openapi["components"]["schemas"], Probe(), {}))
        if plain.status_code >= 300:
            problems.append(f"the unfiltered list must answer to be scanned for leaks, got {plain.status_code} {plain.text[:200]}")
        if leaks := leaked(plain, {}, sweep.foreign_ids):
            problems.append(f"the unfiltered list contains ids of another organization's records: {sorted(leaks)[:5]}")
    if leaks := leaked(answer, ids, sweep.foreign_ids):
        problems.append(f"the answer contains ids of another organization's records: {sorted(leaks)[:5]}")
    if method != "GET":
        state = await sweep.foreign_state()
        changed = sorted(table for table in state if state[table] != sweep.state.get(table))
        sweep.state = state
        if changed:
            problems.append(f"changed records of another organization in {changed}")
    return [f"{key}: {problem}" for problem in problems]


def _json(answer):
    try:
        return answer.json()
    except ValueError:
        return None


@pytest.fixture
async def sweep():
    """A fresh organization B with one administrator: what the sweep creates as B stays out of the
    seed organizations that other suites count."""
    b, admin = uuid.uuid4(), uuid.uuid4()
    engine = create_async_engine(MIGRATOR_URL, pool_size=2)
    async with engine.begin() as connection:
        await connection.execute(text("INSERT INTO organization (id, name) VALUES (:b, 'Isolationstest B')"), {"b": b})
        await connection.execute(text(
            'INSERT INTO "user" (id, organization_id, email, password_hash, display_name) '
            "VALUES (:admin, :b, :email, 'unused-fixture-password', 'Admin Isolationstest')"
        ), {"admin": admin, "b": b, "email": f"{admin}@isolation-sweep.invalid"})
        await connection.execute(text(
            "INSERT INTO membership (organization_id, user_id, role) VALUES (:b, :admin, 'OWNER_ADMIN')"
        ), {"admin": admin, "b": b})
    try:
        async with _api_test_client(cookies={settings.session_cookie_name: _make_session_cookie(admin, b)}) as client:
            yield Sweep(client, b, admin, engine)
    finally:
        await engine.dispose()


async def run_area(sweep: Sweep, name: str) -> None:
    sweep.foreign_ids = await sweep.collect_foreign_ids()
    sweep.state = before = await sweep.foreign_state()
    problems = []
    for key, probe in AREAS[name].PROBES.items():
        try:
            problems += await sweep_route(sweep, key, probe)
        except Exception as error:  # one broken probe must not hide the others
            problems.append(f"{key}: the probe or the route raised {type(error).__name__}: {str(error)[:300]}")
    after = await sweep.foreign_state()
    changed = sorted(table for table in after if after[table] != sweep.state.get(table))
    if changed:  # only reads ran since the last write check
        problems.append(f"reads of the {name} area changed records of another organization in {changed}")
    assert before, "no tenant table was inspected"
    assert not problems, f"{len(problems)} isolation problem(s) in the {name} area:\n" + "\n".join(problems)


async def test_core_routes_keep_organizations_apart(sweep, core_seeded):
    await run_area(sweep, "core")


async def test_finance_routes_keep_organizations_apart(sweep, finance_seeded):
    await run_area(sweep, "finance")


@pytest.mark.parametrize("route", [
    "POST /api/weg-settlements",
    "PATCH /api/weg-settlements/{run_id}/economic-plan",
])
async def test_weg_plan_probes_skip_planless_runs(migrator_session, route):
    # Allocation-correction fixtures can leave a smaller UUID without a plan.
    # Shadow the lookup tables without persisting another planless run.
    ids = {name: uuid.UUID(int=value) for name, value in {
        "planless": 1, "org": 4, "run": 11, "property": 12, "plan": 13, "b": 20,
    }.items()}
    lookup = """WITH settlement_run(id,organization_id,run_type,economic_plan_id,property_id) AS (
        VALUES (CAST(:planless AS uuid),CAST(:org AS uuid),'WEG_JAHRESABRECHNUNG',NULL::uuid,CAST(:property AS uuid)),
               (:run,:org,'WEG_JAHRESABRECHNUNG',:plan,:property)
      ), economic_plan(id,organization_id) AS (
        VALUES (CAST(:plan AS uuid),CAST(:org AS uuid))
      ) """ + finance.PROBES[route].ids
    selected = (await migrator_session.execute(text(lookup), ids)).mappings().one()
    if route.startswith("POST"):
        assert selected["economic_plan_id"] == ids["plan"]
    else:
        assert selected["run_id"] == ids["run"]
        assert selected["expected_plan_id"] == selected["successor_id"] == ids["plan"]


async def test_document_routes_keep_organizations_apart(sweep, documents_seeded):
    await run_area(sweep, "documents")


@pytest.mark.parametrize("route", [
    "GET /api/allocation-treatments/{review_id}/visual-adoption-preview",
    "POST /api/logical-documents/{logical_id}/visual-allocation-adoptions",
])
async def test_visual_adoption_probe_skips_organizations_without_disclosures(migrator_session, route):
    # Earlier tests can leave smaller UUIDs in an organization without disclosures.
    # Shadow only the lookup tables so this ordering is reproducible without OCR.
    ids = {name: uuid.UUID(int=value) for name, value in {
        "unrelated_review": 1, "unrelated_set": 2, "unrelated_logical": 3,
        "other_org": 4, "review": 11, "set": 12, "logical": 13,
        "org": 14, "disclosure": 15, "b": 20,
    }.items()}
    lookup = """WITH allocation_treatment_review(id,organization_id) AS (
        VALUES (CAST(:unrelated_review AS uuid),CAST(:other_org AS uuid)),(:review,:org)
      ), allocation_set(id,organization_id,logical_document_id,treatment_review_id) AS (
        VALUES (CAST(:unrelated_set AS uuid),CAST(:other_org AS uuid),
                CAST(:unrelated_logical AS uuid),CAST(:unrelated_review AS uuid)),
               (:set,:org,:logical,:review)
      ), visual_disclosure_event(id,organization_id) AS (
        VALUES (CAST(:disclosure AS uuid),CAST(:org AS uuid))
      ) """ + documents.PROBES[route].ids
    selected = (await migrator_session.execute(text(lookup), ids)).mappings().one()
    assert selected["disclosure_id"] == ids["disclosure"]
    if "review_id" in selected:
        assert selected["review_id"] == ids["review"]
    else:
        assert selected["logical_id"] == ids["logical"]
        assert selected["treatment_review_id"] == ids["review"]


async def test_extraction_history_probe_skips_revisions_without_logical_documents(migrator_session):
    ids = {name: uuid.UUID(int=value) for name, value in {
        "unlinked": 1, "linked": 2, "ref": 3, "logical": 4, "org": 5, "b": 6,
    }.items()}
    lookup = """WITH extraction_revision(id,organization_id,document_ref_id,logical_document_id) AS (
        VALUES (CAST(:unlinked AS uuid),CAST(:org AS uuid),CAST(:ref AS uuid),NULL::uuid),
               (:linked,:org,:ref,:logical)
      ) """ + documents.PROBES["GET /api/document-refs/{ref_id}/extractions"].ids
    selected = (await migrator_session.execute(text(lookup), ids)).mappings().one()
    assert dict(selected) == {"ref_id": ids["ref"], "logical_document_id": ids["logical"]}


async def test_access_routes_keep_organizations_apart(sweep, access_seeded):
    await run_area(sweep, "access")
