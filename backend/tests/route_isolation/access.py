"""Isolation sweep: probes and seed for accounting scopes, access preparation, quarantines, property and person data domains, property source reviews and classifications, declaration imports, property documents and property knowledge."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.conftest import MIGRATOR_URL
from tests.mandate_legacy_fixture import seed_legacy_mandate
from tests.route_isolation import Probe, row
from tests.test_access_disposition import acceptance_body, assessment, retained_fixture, reviewed
from tests.test_access_grant_proposals import change, propose
from tests.test_declaration_import_api import enable_declaration_imports, isolated_document_store  # noqa: F401
from tests.test_declaration_review_api import review_import, review_session  # noqa: F401
from tests.test_financial_scope_boundary import scope_seed, worker_tenant  # noqa: F401
from tests.test_person_data_domains import domain_fixture as person_domain
from tests.test_property_source_admission_review import legacy_source, pending_body
from tests.test_property_source_classification import classification_body, legacy_bound
from tests.test_scope_control import change as change_scope_control
from tests.test_scoped_account_statements import client
from tests.test_scoped_mandate_documents import document_fixture

# Imported by the sweep test with *: the seed fixture and every fixture it requests that
# conftest.py does not provide. The two autouse fixtures switch declaration imports on and
# isolate the document store for every area test of the sweep module.
__all__ = [
    "access_seeded", "scope_seed", "worker_tenant", "review_import", "review_session",
    "enable_declaration_imports", "isolated_document_store",
]


DIGEST = "0" * 64
REASON = "Isolationstest"
FOREIGN_FILTER = "row-level security hides the other organization's rows, so the filter matches nothing, as it does for an unknown id"


async def created(api, path: str, body: dict) -> dict:
    response = await api.post(path, json=body, headers={"Idempotency-Key": str(uuid.uuid4())})
    assert response.status_code == 201, f"{path}: {response.text}"
    return response.json()


@asynccontextmanager
async def migrator():
    """A second migrator session: the helpers commit, and ``migrator_session`` can do that only once."""
    engine = create_async_engine(MIGRATOR_URL, pool_size=1)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def access_seeded(client_a_admin, seed_data, migrator_session, scope_seed, review_import):
    """At least one record of another organization than B for every probe below."""
    # A dedicated organization (as in the access preparation tests) holds scopes, coverage
    # evidence, quarantine and scope control; the acceptance comes first because it needs a
    # coverage snapshot without scopes.
    retained = await retained_fixture(migrator_session, scope_seed)
    async with client(retained, "actor") as admin:
        report = await reviewed(admin)
        state = await assessment(admin, report)
        await created(admin, f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",
                      acceptance_body(report, state))
    async with migrator() as session:
        ids = await document_fixture(session, scope_seed)
    await seed_legacy_mandate(ids)
    async with client(ids, "actor") as admin:
        proposal = await propose(admin, await reviewed(admin), [change(ids)])
        assert proposal.status_code == 201, proposal.text
        # Scope routes need an explicit prepare grant even for an administrator: with it, a foreign
        # scope would be usable if organizations leaked.
        await created(admin, f"/api/accounting-scopes/{ids['scope']}/grants", {
            "user_id": str(ids["actor"]), "capabilities": ["read", "prepare"], "reason": REASON})
    # Pausing and resuming leaves scope control events behind but keeps the prepare grant usable.
    paused = await change_scope_control(ids)
    assert paused.status_code == 201, paused.text
    resumed = await change_scope_control(ids, mode="compatibility", previous=paused.json()["id"])
    assert resumed.status_code == 201, resumed.text

    # Organization A: sources awaiting review, reviewed sources, bound originals awaiting
    # classification and classified ones. Each helper builds property, audience, import and upload.
    await legacy_source(client_a_admin, seed_data)
    reviewed_source = await legacy_source(client_a_admin, seed_data)
    await created(client_a_admin, "/api/property-source-admission-reviews",
                  await pending_body(client_a_admin, reviewed_source))
    await legacy_bound(client_a_admin, seed_data)
    classified = await legacy_bound(client_a_admin, seed_data)
    await created(client_a_admin, "/api/property-source-classifications",
                  await classification_body(client_a_admin, classified))
    _, _, domain = await person_domain(client_a_admin)
    await created(client_a_admin, f"/api/person-data-domains/{domain['id']}/grants", {
        "user_id": str(seed_data["user_a_clerk_id"]), "capabilities": ["read"], "reason_code": "TEAM_ACCESS"})


def foreign(select: str, source: str, where: str = "", order: str = "x.id") -> str:
    """Like ``row()`` for joins and extra conditions: the table in ``source`` that is aliased ``x`` carries the organization."""
    extra = f" AND {where}" if where else ""
    return f"SELECT {select} FROM {source} WHERE x.organization_id IS DISTINCT FROM :b{extra} ORDER BY {order} LIMIT 1"


SNAPSHOT = row("access_coverage_snapshot", snapshot_id="id", manifest_digest="manifest_digest")
# A scope on which an administrator of its organization holds the prepare grant the scope routes demand.
PREPARED_SCOPE = (
    "accounting_scope x JOIN scope_grant g ON g.organization_id = x.organization_id AND g.scope_id = x.id "
    "AND g.revoked_at IS NULL AND 'prepare' = ANY(g.capabilities) "
    "JOIN membership m ON m.organization_id = g.organization_id AND m.user_id = g.user_id AND m.role = 'OWNER_ADMIN'")
SCOPE = foreign("x.id AS scope_id", PREPARED_SCOPE)
PROPERTY_DOMAIN = row("property_data_domain", domain_id="id")
PERSON_DOMAIN = row("person_data_domain", domain_id="id")
PROPERTY = row("property", property_id="id")
# A member of the record's own organization, for bodies that name a team member.
SCOPE_MEMBER = foreign("x.id AS scope_id, m.user_id", "accounting_scope x JOIN membership m ON m.organization_id = x.organization_id",
                       order="x.id, m.user_id")
PROPERTY_DOMAIN_MEMBER = foreign("x.id AS domain_id, m.user_id", "property_data_domain x JOIN membership m ON m.organization_id = x.organization_id",
                                 order="x.id, m.user_id")
PERSON_DOMAIN_MEMBER = foreign("x.id AS domain_id, m.user_id", "person_data_domain x JOIN membership m ON m.organization_id = x.organization_id",
                               order="x.id, m.user_id")
# Imports in the state each route works on, so a missing organization filter would get deep into the route.
DRAFT_IMPORT = foreign("x.id AS import_id", "declaration_import x", order="(x.status = 'DRAFT') DESC, x.id")
REVIEW_IMPORT = foreign("x.id AS import_id", "declaration_import x", order="(x.status = 'NEEDS_REVIEW') DESC, x.id")
IMPORT_DOCUMENT = foreign(
    "x.import_id, x.document_id",
    "declaration_import_document x JOIN declaration_import i ON i.id = x.import_id AND i.organization_id = x.organization_id",
    order="(i.status = 'DRAFT') DESC, x.id")
CANDIDATE = foreign("x.import_id, x.id AS candidate_id, i.draft_revision",
                    "declaration_candidate x JOIN declaration_import i ON i.id = x.import_id AND i.organization_id = x.organization_id")
# An original in the review state the admission route works on: import still a draft, no review yet.
IMPORT_SOURCE = (
    "declaration_import_document x JOIN declaration_import i ON i.id = x.import_id AND i.organization_id = x.organization_id "
    "JOIN property_document d ON d.id = x.document_id AND d.organization_id = x.organization_id")
UNREVIEWED = "i.status = 'DRAFT' AND NOT EXISTS (SELECT 1 FROM property_source_admission_review r WHERE r.document_id = x.document_id)"
ADMISSION_DOCUMENT = foreign("x.document_id", IMPORT_SOURCE, UNREVIEWED)
ADMISSION_SOURCE = foreign("x.document_id, x.import_id, i.draft_revision, d.content_sha256", IMPORT_SOURCE, UNREVIEWED)
CLASSIFICATION_SOURCE = foreign(
    "x.id AS document_id, x.property_id, x.content_sha256", "property_document x",
    "x.property_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM property_source_classification c WHERE c.document_id = x.id)")
BOUND_DOCUMENT = foreign("x.property_id, x.id AS document_id", "property_document x", "x.property_id IS NOT NULL")


PROBES: dict[str, Probe] = {
    # app/api/access_preparation.py
    "GET /api/access-preparation": Probe(),
    "GET /api/access-preparation/runtime-budget": Probe(),
    "GET /api/access-preparation/snapshots": Probe(),
    "GET /api/access-preparation/snapshots/{snapshot_id}/disposition": Probe(SNAPSHOT),
    "GET /api/access-preparation/snapshots/{snapshot_id}/disposition/items": Probe(SNAPSHOT),
    "GET /api/access-preparation/snapshots/{snapshot_id}/grant-proposal": Probe(SNAPSHOT),
    "GET /api/access-preparation/snapshots/{snapshot_id}/items": Probe(SNAPSHOT),
    "GET /api/access-preparation/source-security": Probe(),
    "POST /api/access-preparation/grant-proposals/{proposal_id}/accept": Probe(
        row("access_grant_proposal", proposal_id="id", proposal_digest="proposal_digest")),
    "POST /api/access-preparation/snapshots": Probe(row("access_coverage_snapshot", predecessor_id="id")),
    "POST /api/access-preparation/snapshots/{snapshot_id}/disposition/accept": Probe(SNAPSHOT, body={
        "manifest_digest": "{manifest_digest}", "assessment_digest": DIGEST, "reason": REASON,
        "team_acknowledged": True, "retention_acknowledged": True}),
    "POST /api/access-preparation/snapshots/{snapshot_id}/grant-proposals": Probe(
        foreign("x.id AS snapshot_id, x.manifest_digest, s.id AS audience_id, m.user_id",
                "access_coverage_snapshot x JOIN accounting_scope s ON s.organization_id = x.organization_id "
                "JOIN membership m ON m.organization_id = x.organization_id", order="x.id, s.id, m.user_id"),
        body={"manifest_digest": "{manifest_digest}", "changes": [{
            "domain_kind": "financial", "audience_id": "{audience_id}", "user_id": "{user_id}",
            "expected_grant_id": None, "capabilities": ["read"], "expires_at": None}]}),
    "POST /api/access-preparation/snapshots/{snapshot_id}/review": Probe(SNAPSHOT),
    # app/api/accounting_scopes.py
    "GET /api/accounting-scopes/control": Probe(),
    "GET /api/accounting-scopes/page": Probe(
        foreign("x.property_id, x.management_mandate_id", "accounting_scope x", "x.management_mandate_id IS NOT NULL"),
        status=200, because=FOREIGN_FILTER),
    "GET /api/accounting-scopes/{scope_id}": Probe(SCOPE),
    "GET /api/accounting-scopes/{scope_id}/grants/page": Probe(SCOPE),
    "GET /api/accounting-scopes/{scope_id}/legacy-payments": Probe(SCOPE),
    "POST /api/accounting-scopes": Probe(foreign(
        "x.id AS property_id, m.id AS management_mandate_id",
        "property x JOIN management_mandate m ON m.organization_id = x.organization_id", order="x.id, m.id")),
    "POST /api/accounting-scopes/{scope_id}/bindings": Probe(foreign(
        "x.id AS scope_id, b.id AS resource_id",
        PREPARED_SCOPE + " JOIN bank_account b ON b.organization_id = x.organization_id AND b.property_id = x.property_id",
        order="x.id, b.id")),
    "POST /api/accounting-scopes/{scope_id}/grants": Probe(
        SCOPE_MEMBER, body={"user_id": "{user_id}", "capabilities": ["read"], "reason": REASON}),
    "POST /api/accounting-scopes/{scope_id}/grants/{grant_id}/revoke": Probe(row("scope_grant", scope_id="scope_id", grant_id="id")),
    "POST /api/accounting-scopes/{scope_id}/legacy-payments": Probe(SCOPE, body={
        "items": [{"payment_id": str(uuid.uuid4()), "source_digest": DIGEST}], "reason": REASON}),
    # app/api/declaration_imports.py
    "GET /api/declaration-imports/{import_id}": Probe(DRAFT_IMPORT),
    "GET /api/declaration-imports/{import_id}/approval-preview": Probe(REVIEW_IMPORT),
    "GET /api/declaration-imports/{import_id}/candidates": Probe(REVIEW_IMPORT),
    "GET /api/declaration-imports/{import_id}/documents": Probe(DRAFT_IMPORT),
    "GET /api/declaration-imports/{import_id}/documents/{document_id}/content": Probe(IMPORT_DOCUMENT),
    "PATCH /api/declaration-imports/{import_id}/candidates/{candidate_id}": Probe(CANDIDATE),
    "PATCH /api/declaration-imports/{import_id}/documents/{document_id}": Probe(IMPORT_DOCUMENT, body={"title": REASON}),
    "POST /api/declaration-imports": Probe(
        row("property", target_property_id="id"),
        body={"mode": "EXISTING_PROPERTY", "target_property_id": "{target_property_id}"}),
    "POST /api/declaration-imports/{import_id}/approve": Probe(REVIEW_IMPORT),
    "POST /api/declaration-imports/{import_id}/cancel": Probe(DRAFT_IMPORT),
    "POST /api/declaration-imports/{import_id}/candidates/bulk-review": Probe(CANDIDATE, body={
        "draft_revision": "{draft_revision}", "candidate_ids": ["{candidate_id}"], "decision": "ACCEPTED"}),
    "POST /api/declaration-imports/{import_id}/documents": Probe(
        DRAFT_IMPORT, body={"document_role": "DECLARATION", "sequence_no": 1}),
    "POST /api/declaration-imports/{import_id}/retry": Probe(DRAFT_IMPORT),
    "POST /api/declaration-imports/{import_id}/start": Probe(DRAFT_IMPORT),
    # app/api/mandate_quarantine.py
    "GET /api/mandate-quarantine/page": Probe(),
    "POST /api/mandate-quarantine": Probe(foreign(
        "x.document_id, x.snapshot_digest", "mandate_legacy_observation x", order="x.document_id")),
    # app/api/person_data_domains.py
    "GET /api/person-data-domains/page": Probe(
        row("person_data_domain", person_id="person_id"), status=200, because=FOREIGN_FILTER),
    "GET /api/person-data-domains/{domain_id}": Probe(PERSON_DOMAIN),
    "GET /api/person-data-domains/{domain_id}/grants/page": Probe(PERSON_DOMAIN),
    "POST /api/person-data-domains": Probe(row("person", person_id="id")),
    "POST /api/person-data-domains/{domain_id}/grants": Probe(
        PERSON_DOMAIN_MEMBER, body={"user_id": "{user_id}", "capabilities": ["read"], "reason_code": "TEAM_ACCESS"}),
    "POST /api/person-data-domains/{domain_id}/grants/{grant_id}/revoke": Probe(row("person_data_grant", domain_id="domain_id", grant_id="id")),
    # app/api/property_data_domains.py
    "GET /api/property-data-domains/page": Probe(
        row("property_data_domain", property_id="property_id"), status=200, because=FOREIGN_FILTER),
    "GET /api/property-data-domains/{domain_id}": Probe(PROPERTY_DOMAIN),
    "GET /api/property-data-domains/{domain_id}/grants/page": Probe(PROPERTY_DOMAIN),
    "POST /api/property-data-domains": Probe(row("property", property_id="id")),
    "POST /api/property-data-domains/{domain_id}/grants": Probe(
        PROPERTY_DOMAIN_MEMBER, body={"user_id": "{user_id}", "capabilities": ["read"], "reason": REASON}),
    "POST /api/property-data-domains/{domain_id}/grants/{grant_id}/revoke": Probe(row("property_data_grant", domain_id="domain_id", grant_id="id")),
    # app/api/property_documents.py
    "GET /api/properties/{property_id}/documents": Probe(PROPERTY),
    "GET /api/properties/{property_id}/documents/{document_id}/content": Probe(BOUND_DOCUMENT),
    # app/api/property_knowledge.py
    "GET /api/properties/{property_id}/knowledge": Probe(PROPERTY),
    "GET /api/properties/{property_id}/knowledge/search": Probe(PROPERTY),
    "GET /api/properties/{property_id}/rules": Probe(PROPERTY),
    "POST /api/properties/{property_id}/knowledge/query": Probe(PROPERTY),
    # app/api/property_source_admission_reviews.py
    "GET /api/property-source-admission-reviews/originals/{document_id}/content": Probe(ADMISSION_DOCUMENT),
    "GET /api/property-source-admission-reviews/pending/page": Probe(),
    "GET /api/property-source-admission-reviews/{review_id}": Probe(row("property_source_admission_review", review_id="id")),
    "POST /api/property-source-admission-reviews": Probe(ADMISSION_SOURCE),
    # app/api/property_source_classifications.py
    "GET /api/property-source-classifications/originals/{document_id}/content": Probe(CLASSIFICATION_SOURCE),
    "GET /api/property-source-classifications/pending/page": Probe(),
    "GET /api/property-source-classifications/{classification_id}": Probe(row("property_source_classification", classification_id="id")),
    "POST /api/property-source-classifications": Probe(CLASSIFICATION_SOURCE, body={
        "document_id": "{document_id}", "property_id": "{property_id}", "content_sha256": "{content_sha256}",
        "source_context_digest": DIGEST, "decision": "ADMITTED", "reason": REASON}),
    # Organization-wide modes come last: a wrongly accepted write would change B's state for later probes.
    "POST /api/access-preparation/runtime-budget/review": Probe(
        row("runtime_budget_review", expected_review_id="id", policy_digest="policy_digest"), status=409,
        because="expected_review_id is only compared with B's own latest review, never looked up: any id B has no review for is a stale expectation"),
    "POST /api/access-preparation/source-security/transitions": Probe(
        row("access_disposition_acceptance", acceptance_id="id", manifest_digest="manifest_digest", assessment_digest="assessment_digest"),
        body={"target_mode": "ENFORCED", "expected_mode": "PREPARATION", "expected_changed_at": None,
              "expected_transition_id": None, "acceptance_id": "{acceptance_id}", "manifest_digest": "{manifest_digest}",
              "assessment_digest": "{assessment_digest}", "reason": REASON, "acknowledged": True}),
    "POST /api/accounting-scopes/control": Probe(
        row("scope_control_event", expected_event_id="id"),
        body={"mode": "paused", "expected_event_id": "{expected_event_id}", "reason": REASON}, status=409,
        because="expected_event_id is only compared with B's own latest event, never looked up: any id B has no event for is a stale expectation"),
}
