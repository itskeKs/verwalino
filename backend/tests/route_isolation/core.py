"""Isolation sweep: probes and seed for master data, users, mandates, tasks, attachments, deadlines, GDPR, dunning, mail, letters and meetings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.route_isolation import Probe, row
from tests.test_attachments import attachment_pdf, attachment_store, created_attachment  # noqa: F401
from tests.test_letters import create_template, issue_body
from tests.test_m7_dunning import isolated_batch_seed  # noqa: F401
from tests.test_m_mandate_documents import _pdf, mandate_doc_seed  # noqa: F401
from tests.test_mail_service import issued_notice
from tests.test_master_import import body as import_body
from tests.test_meters_api import new_meter
from tests.test_rent_changes import rent_db, rent_world  # noqa: F401

EXEMPT = {
    "GET /api/meta": "organization-free deployment facts and file safety cap only; covered by test_deployment_policy.py",
    **{f"{method} /api/signup{suffix}": "organization-free hosted signup; pending identity and atomic new-tenant creation covered by self_service_signup.feature and test_signup.py"
       for suffix in ("", "/confirm", "/resend")
       for method in ("POST", "GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")},
    "POST /api/admin/diagnostics": "binary download: no client-supplied IDs; decoded bundle, current-org counts, process log isolation and roles covered in test_admin_diagnostics.py and organization_isolation.feature",
    "GET /api/setup": "organization-free boolean only; fresh/closed states covered by first_run_setup.feature and test_setup.py",
    "POST /api/setup": "exists only before any organization; token-gated creation covered by first_run_setup.feature and test_setup.py",
    **{f"{method} /api/setup": "organization-free method refusal; closed setup returns 404, tested in test_setup.py"
       for method in ("HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")},
}

# Imported by the sweep test with *: the seed fixture and every fixture it requests that
# conftest.py does not provide.
__all__ = ["core_seeded", "isolated_batch_seed", "rent_db", "rent_world", "attachment_store", "attachment_pdf", "mandate_doc_seed"]


async def created(api, path: str, **request) -> dict:
    answer = await api.post(path, **request)
    assert answer.status_code in (200, 201), f"seed {path}: {answer.status_code} {answer.text[:300]}"
    return answer.json()


@pytest.fixture
async def core_seeded(isolated_batch_seed, rent_world, attachment_store, attachment_pdf, mandate_doc_seed, client_a_admin, sweep):
    """At least one record of another organization than B for every probe below.

    ``rent_world`` and ``isolated_batch_seed`` build two fresh organizations (neither is B) with
    property, unit, person, tenant profile, lease and the dunning prerequisites; the rest is
    created through the administrator's API of that organization.
    """
    api, m7 = isolated_batch_seed
    parents = {"property": str(rent_world["property"]), "unit": str(rent_world["unit"])}

    owner_person = await created(api, "/api/persons", json={"first_name": "Olga", "last_name": "Eigentuemer"})
    vendor_person = await created(api, "/api/persons", json={"last_name": "Handwerker"})
    owner = await created(api, "/api/owner-profiles", json={"person_id": owner_person["id"]})
    await created(api, "/api/vendors", json={"person_id": vendor_person["id"]})
    await created(api, "/api/units", json={"property_id": parents["property"], "number": "S-1", "usage_type": "WOHNUNG"})
    await created(api, "/api/ownership", json={
        "unit_id": parents["unit"], "owner_profile_id": owner["id"], "valid_from": "2026-01-01"})
    await created(api, "/api/tasks", json={
        "title": "Isolationstest", "property_id": parents["property"], "unit_id": parents["unit"],
        "lease_agreement_id": str(rent_world["lease"])})
    await new_meter(api, parents, unit_id=parents["unit"])
    await created_attachment(api, parents, attachment_pdf)

    await issued_notice(api, m7)
    template = await create_template(api)
    await created(api, "/api/letters/issue", json=issue_body(rent_world, template))

    # Unscoped mandate uploads are refused; this seed gives organization A a mandate with a granted scope.
    await created(client_a_admin, f"/api/mandates/{mandate_doc_seed['mandate_a']}/documents", **_pdf(
        "vertrag.pdf", b"%PDF-1.4 isolation", "VERWALTERVERTRAG", "Vertrag", scope=mandate_doc_seed["scope"]))

    weg = await created(api, "/api/properties", json={"name": "WEG Isolationstest", "management_type": "WEG"})
    meeting = await created(api, "/api/meetings", json={
        "property_id": weg["id"], "meeting_date": "2026-09-01", "meeting_type": "ORDENTLICH", "location": "Buero"})
    item = await created(api, f"/api/meetings/{meeting['id']}/agenda", json={"meeting_id": meeting["id"], "title": "Begruessung"})
    resolution = await created(api, "/api/meetings/resolutions", json={
        "meeting_id": meeting["id"], "agenda_item_id": item["id"], "wording": "Der Plan wird genehmigt.",
        "votes_yes": 3, "votes_no": 0, "votes_abstain": 0, "result": "ANGENOMMEN"})
    await created(api, f"/api/meetings/resolutions/{resolution['id']}/register")

    exported = await api.post("/api/organizations/export")
    assert exported.status_code == 200, exported.text

    # The two apply bodies carry the digest of organization B's own preview, known only at run time.
    BATCH_APPLY["expected_source_digest"] = (await created(sweep.client, "/api/dunning/batch/preview", json=BATCH))["source_digest"]
    IMPORT_APPLY["expected_digest"] = (await created(sweep.client, "/api/properties/import/preview", json=import_body()))["source_digest"]
    try:
        yield
    finally:
        # This sweep owns a throwaway B organization. Its successful own-org
        # deletion probe must not leave a decision for later migration rehearsals.
        async with sweep.engine.begin() as connection:
            await connection.execute(text('DELETE FROM organization_deletion WHERE organization_id=:b'), {'b': sweep.b})


# Raw SQL for probes whose ids must come from one organization (the sweep only guarantees "not B").
# The units have no lease and no owner yet: an overlap constraint would answer 409 before anything else.
UNIT_AND_TENANT = (
    "SELECT u.id AS unit_id, t.id AS tenant_profile_id FROM unit u JOIN tenant_profile t "
    "ON t.organization_id = u.organization_id WHERE u.organization_id IS DISTINCT FROM :b "
    "AND NOT EXISTS (SELECT 1 FROM lease_agreement l WHERE l.unit_id = u.id) ORDER BY u.id, t.id LIMIT 1"
)
UNIT_AND_OWNER = (
    "SELECT u.id AS unit_id, o.id AS owner_profile_id FROM unit u JOIN owner_profile o "
    "ON o.organization_id = u.organization_id WHERE u.organization_id IS DISTINCT FROM :b "
    "AND NOT EXISTS (SELECT 1 FROM ownership_history h WHERE h.unit_id = u.id) ORDER BY u.id, o.id LIMIT 1"
)
TENANT_AND_LEASE = (
    "SELECT l.tenant_profile_id, l.id AS lease_agreement_id FROM lease_agreement l "
    "WHERE l.organization_id IS DISTINCT FROM :b ORDER BY l.id LIMIT 1"
)
TEMPLATE_AND_PROPERTY = (
    "SELECT t.id AS template_id, p.id AS property_id FROM letter_template t JOIN property p "
    "ON p.organization_id = t.organization_id WHERE t.organization_id IS DISTINCT FROM :b ORDER BY t.id, p.id LIMIT 1"
)
MEETING_AND_ITEM = (
    "SELECT a.meeting_id, a.id AS agenda_item_id FROM agenda_item a "
    "WHERE a.organization_id IS DISTINCT FROM :b ORDER BY a.id LIMIT 1"
)
TASK_PARENTS = (
    "SELECT l.id AS lease_agreement_id, l.unit_id, u.property_id FROM lease_agreement l JOIN unit u "
    "ON u.id = l.unit_id WHERE l.organization_id IS DISTINCT FROM :b ORDER BY l.id LIMIT 1"
)
# The three mail routes share one resolver with a branch per kind; the info route takes the meeting branch.
INVITATION_MAIL = (
    "SELECT 'invitation' AS kind, x.id AS document_id FROM owners_meeting x "
    "WHERE x.organization_id IS DISTINCT FROM :b ORDER BY x.id LIMIT 1"
)
DUNNING_MAIL = (
    "SELECT 'dunning' AS kind, x.id AS document_id, gen_random_uuid() AS attempt_id FROM dunning_notice x "
    "WHERE x.organization_id IS DISTINCT FROM :b ORDER BY x.id LIMIT 1"
)
DUNNING_MAIL_PERSON = (
    "SELECT 'dunning' AS kind, x.id AS document_id, p.id AS person_id FROM dunning_notice x JOIN person p "
    "ON p.organization_id = x.organization_id WHERE x.organization_id IS DISTINCT FROM :b ORDER BY x.id, p.id LIMIT 1"
)
EMPTY_FILTER = "the list is filtered by the id under row-level security, so a foreign id matches nothing: 200 and an empty list, as for an unknown id"
EMPTY_BY_PARENT = "lists the rows of the parent id under row-level security: an empty list for any id the organization cannot see"
MANDATE_DOCUMENT = row("mandate_document", mandate_id="management_mandate_id", doc_id="id")

BATCH = {"period_start": "2025-01-01", "period_end": "2025-06-30", "reference_date": "2025-07-01", "threshold": "0.00"}
BATCH_APPLY = {**BATCH, "lease_agreement_ids": ["{lease_agreement_id}"], "expected_source_digest": "", "request_id": str(uuid.uuid4())}
IMPORT_APPLY = {**import_body(), "expected_digest": "", "request_id": str(uuid.uuid4())}


PROBES: dict[str, Probe] = {
    "POST /api/organizations/export": Probe(),
    "POST /api/organizations/import": Probe(),
    "POST /api/organizations/deletion": Probe(),
    "POST /api/organizations/deletion/confirm": Probe(body={"organization_name": "Wrong organization"}),
    "POST /api/organizations/deletion/cancel": Probe(),
    "GET /api/organizations/export/{export_id}": Probe(
        "SELECT id AS export_id FROM audit_event WHERE organization_id IS DISTINCT FROM :b "
        "AND action='organization.export.created' ORDER BY id LIMIT 1"),
    "GET /api/properties/{property_id}/settlement-periods": Probe(row("property", property_id="id"), query={"year": 2025}),
    "GET /api/admin/diagnostics": Probe(),
    # app/api/attachments.py
    "GET /api/attachments": Probe(row("property", property_id="id")),
    "GET /api/attachments/{attachment_id}": Probe(row("attachment", attachment_id="id")),
    "GET /api/attachments/{attachment_id}/download": Probe(row("attachment", attachment_id="id")),
    "POST /api/attachments": Probe(row("property", property_id="id")),
    "POST /api/attachments/{attachment_id}/archive": Probe(row("attachment", attachment_id="id")),
    # app/api/auth.py
    "GET /api/auth/session": Probe(),
    # app/api/cost_categories.py
    "GET /api/cost-categories": Probe(),
    # app/api/dsgvo.py
    "GET /api/dsgvo/persons/{person_id}/export": Probe(row("person", person_id="id")),
    "GET /api/dsgvo/persons/{person_id}/status": Probe(row("person", person_id="id")),
    "POST /api/dsgvo/persons/{person_id}/anonymize": Probe(row("person", person_id="id")),
    "POST /api/dsgvo/persons/{person_id}/request-deletion": Probe(row("person", person_id="id")),
    # app/api/dunning.py
    "DELETE /api/dunning/{dunning_id}": Probe(row("dunning_notice", dunning_id="id")),
    "GET /api/dunning": Probe(
        row("tenant_profile", tenant_profile_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/dunning/settings": Probe(),
    "GET /api/dunning/{dunning_id}": Probe(row("dunning_notice", dunning_id="id")),
    "GET /api/dunning/{dunning_id}/pdf": Probe(row("dunning_notice", dunning_id="id")),
    "POST /api/dunning/batch": Probe(row("lease_agreement", lease_agreement_id="id"), body=BATCH_APPLY),
    "POST /api/dunning/batch/preview": Probe(
        row("accounting_scope", accounting_scope_id="id"), body={**BATCH, "accounting_scope_id": "{accounting_scope_id}"}),
    "POST /api/dunning/owner": Probe(row("owner_profile", owner_profile_id="id")),
    "POST /api/dunning/tenant": Probe(TENANT_AND_LEASE),
    "POST /api/dunning/{dunning_id}/issue": Probe(row("dunning_notice", dunning_id="id")),
    # app/api/dunning_quarantine.py
    "GET /api/dunning-quarantine/page": Probe(),
    "POST /api/dunning-quarantine": Probe(row("dunning_notice", notice_id="id"), body={"notice_id": "{notice_id}", "snapshot_digest": "0" * 64, "reason": "SOURCE_CONTEXT_UNVERIFIED"}),
    # app/api/fristen.py
    "GET /api/fristen": Probe(row("accounting_scope", accounting_scope_id="id")),
    "GET /api/fristen/context": Probe(),
    # app/api/leases.py
    "DELETE /api/leases/{lease_id}": Probe(row("lease_agreement", lease_id="id")),
    "GET /api/leases": Probe(row("unit", unit_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/leases/{lease_id}/rent-changes": Probe(row("lease_agreement", lease_id="id")),
    "GET /api/leases/{lease_id}/documents": Probe(row("lease_agreement", lease_id="id")),
    "POST /api/leases/{lease_id}/contract": Probe(row("lease_agreement", lease_id="id"), body={
        'request_id': str(uuid.uuid4()), 'as_of': '2026-01-01', 'handover_date': '2026-01-02', 'agreements': ''}),
    "POST /api/leases/{lease_id}/handover": Probe(row("lease_agreement", lease_id="id"), body={
        'request_id': str(uuid.uuid4()), 'date': '2026-01-02', 'type': 'Einzug', 'parties': 'Synthetic parties',
        'signatures': 'Synthetic names', 'readings': [], 'keys': [], 'notes': ''}),
    "PATCH /api/leases/{lease_id}": Probe(row("lease_agreement", lease_id="id"), body={"heating_advance_payment": "90.00"}),
    "POST /api/leases": Probe(UNIT_AND_TENANT, body={
        "unit_id": "{unit_id}", "tenant_profile_id": "{tenant_profile_id}", "valid_from": "2026-01-01",
        "base_rent": "700.00", "advance_payment": "150.00", "heating_advance_payment": "90.00"}),
    "POST /api/leases/{lease_id}/rent-changes": Probe(row("lease_agreement", lease_id="id"), body={"valid_from": "2026-03-01", "base_rent": "750.00", "advance_payment": "150.00", "heating_advance_payment": "90.00"}),
    "POST /api/leases/{lease_id}/rent-changes/index": Probe(row("lease_agreement", lease_id="id")),
    "POST /api/leases/{lease_id}/rent-changes/index/preview": Probe(row("lease_agreement", lease_id="id")),
    "POST /api/leases/{lease_id}/rent-changes/staffel": Probe(row("lease_agreement", lease_id="id"), body={
        "staffel_start_date": "2027-01-01", "staffel_increase_amount": "10.00", "staffel_interval_months": 12, "staffel_steps": 2}),
    # app/api/letters.py
    "GET /api/letters/documents": Probe(row("property", property_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/letters/documents/{document_id}/pdf": Probe(row("letter_document", document_id="id")),
    "GET /api/letters/templates": Probe(),
    "POST /api/letters/issue": Probe(TEMPLATE_AND_PROPERTY),
    "POST /api/letters/templates": Probe(),
    "PUT /api/letters/templates/{template_id}": Probe(row("letter_template", template_id="id")),
    # app/api/mail.py
    "GET /api/mail/{kind}/{document_id}": Probe(INVITATION_MAIL),
    "GET /api/mail/{kind}/{document_id}/{attempt_id}/pdf": Probe(DUNNING_MAIL),
    "POST /api/mail/{kind}/{document_id}": Probe(DUNNING_MAIL_PERSON),
    # app/api/mandates.py
    "DELETE /api/mandates/{mandate_id}/documents/{doc_id}": Probe(MANDATE_DOCUMENT),
    "GET /api/mandates": Probe(),
    "GET /api/mandates/page": Probe(),
    "GET /api/mandates/{mandate_id}": Probe(row("management_mandate", mandate_id="id")),
    "GET /api/mandates/{mandate_id}/documents": Probe(row("management_mandate", mandate_id="id")),
    "GET /api/mandates/{mandate_id}/documents/{doc_id}/download": Probe(MANDATE_DOCUMENT),
    "PATCH /api/mandates/{mandate_id}": Probe(row("management_mandate", mandate_id="id")),
    "POST /api/mandates": Probe(),
    "POST /api/mandates/{mandate_id}/documents": Probe(row("management_mandate", mandate_id="id"), body={"doc_type": "SONSTIGES"}),
    "POST /api/mandates/{mandate_id}/documents/{doc_id}/archive": Probe(MANDATE_DOCUMENT),
    # app/api/meetings.py
    "DELETE /api/meetings/agenda/{item_id}": Probe(row("agenda_item", item_id="id")),
    "DELETE /api/meetings/resolutions/{resolution_id}": Probe(row("resolution", resolution_id="id")),
    "DELETE /api/meetings/{meeting_id}": Probe(row("owners_meeting", meeting_id="id")),
    "GET /api/meetings": Probe(row("property", property_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/meetings/register/{property_id}": Probe(
        row("property", property_id="id"), status=200,
        because="the register is read by property id under row-level security: an empty list for any id the organization cannot see"),
    "GET /api/meetings/register/{property_id}/pdf": Probe(
        row("property", property_id="id"), status=200,
        because="renders the empty register for any id the organization cannot see; the PDF equals the one for an unknown id"),
    "GET /api/meetings/{meeting_id}": Probe(row("owners_meeting", meeting_id="id")),
    "GET /api/meetings/{meeting_id}/agenda": Probe(row("owners_meeting", meeting_id="id"), status=200, because=EMPTY_BY_PARENT),
    "GET /api/meetings/{meeting_id}/invitation.pdf": Probe(row("owners_meeting", meeting_id="id")),
    "GET /api/meetings/{meeting_id}/pdf": Probe(row("owners_meeting", meeting_id="id")),
    "GET /api/meetings/{meeting_id}/resolutions": Probe(row("owners_meeting", meeting_id="id"), status=200, because=EMPTY_BY_PARENT),
    "POST /api/meetings": Probe(row("property", property_id="id")),
    "POST /api/meetings/resolutions": Probe(MEETING_AND_ITEM),
    "POST /api/meetings/resolutions/{resolution_id}/register": Probe(row("resolution", resolution_id="id")),
    "POST /api/meetings/{meeting_id}/agenda": Probe(row("owners_meeting", meeting_id="id")),
    "POST /api/meetings/{meeting_id}/hold": Probe(row("owners_meeting", meeting_id="id")),
    "PUT /api/meetings/agenda/{item_id}": Probe(row("agenda_item", item_id="id")),
    "PUT /api/meetings/register/entry/{entry_id}/challenge": Probe(row("resolution_register_entry", entry_id="id")),
    "PUT /api/meetings/{meeting_id}": Probe(row("owners_meeting", meeting_id="id")),
    # app/api/meters.py
    "GET /api/meters": Probe(row("unit", unit_id="id", property_id="property_id")),
    "GET /api/meters/{meter_id}": Probe(row("meter", meter_id="id")),
    "GET /api/meters/{meter_id}/readings": Probe(row("meter", meter_id="id")),
    "PATCH /api/meters/{meter_id}": Probe(row("meter", meter_id="id")),
    "POST /api/meters": Probe(row("unit", unit_id="id", property_id="property_id")),
    "POST /api/meters/{meter_id}/readings": Probe(row("meter", meter_id="id"), body={"reading_date": "2026-01-01", "value": "1.0000"}),
    # app/api/organizations.py
    "GET /api/organizations/me": Probe(),
    "GET /api/organizations/quota": Probe(),
    # app/api/owner_profiles.py
    "GET /api/owner-profiles": Probe(row("person", person_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/owner-profiles/page": Probe(),
    "POST /api/owner-profiles": Probe(row("person", person_id="id")),
    # app/api/ownership.py
    "GET /api/ownership": Probe(row("unit", unit_id="id"), status=200, because=EMPTY_FILTER),
    "PATCH /api/ownership/{ownership_id}": Probe(row("ownership_history", ownership_id="id")),
    "POST /api/ownership": Probe(UNIT_AND_OWNER),
    # app/api/persons.py
    "DELETE /api/persons/{person_id}": Probe(row("person", person_id="id")),
    "GET /api/persons": Probe(),
    "GET /api/persons/page": Probe(),
    "GET /api/persons/{person_id}": Probe(row("person", person_id="id")),
    "POST /api/persons": Probe(),
    "PUT /api/persons/{person_id}": Probe(row("person", person_id="id")),
    # app/api/properties.py
    "DELETE /api/properties/{property_id}": Probe(row("property", property_id="id")),
    "GET /api/properties": Probe(),
    "GET /api/properties/import/template.csv": Probe(),
    "GET /api/properties/page": Probe(),
    "GET /api/properties/{property_id}": Probe(row("property", property_id="id")),
    "POST /api/properties": Probe(),
    "POST /api/properties/import/apply": Probe(body=IMPORT_APPLY),
    "POST /api/properties/import/preview": Probe(body=import_body()),
    "POST /api/properties/{property_id}/mea-scale/confirm": Probe(row("property", property_id="id")),
    "PUT /api/properties/{property_id}": Probe(row("property", property_id="id")),
    # app/api/tasks.py
    "DELETE /api/tasks/{task_id}": Probe(row("task", task_id="id")),
    "GET /api/tasks": Probe(row("property", property_id="id")),
    "GET /api/tasks/{task_id}": Probe(row("task", task_id="id")),
    "PATCH /api/tasks/{task_id}": Probe(row("task", task_id="id")),
    "POST /api/tasks": Probe(TASK_PARENTS),
    # app/api/tenant_profiles.py
    "GET /api/tenant-profiles": Probe(row("person", person_id="id"), status=200, because=EMPTY_FILTER),
    "POST /api/tenant-profiles": Probe(row("person", person_id="id")),
    # app/api/units.py
    "DELETE /api/units/{unit_id}": Probe(row("unit", unit_id="id")),
    "GET /api/units": Probe(row("property", property_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/units/page": Probe(row("property", property_id="id"), status=200, because=EMPTY_FILTER),
    "GET /api/units/{unit_id}": Probe(row("unit", unit_id="id")),
    "POST /api/units": Probe(row("property", property_id="id"), body={"property_id": "{property_id}", "number": "1", "usage_type": "WOHNUNG"}),
    "PUT /api/units/{unit_id}": Probe(row("unit", unit_id="id")),
    # app/api/users.py
    "GET /api/users": Probe(),
    "GET /api/users/page": Probe(),
    "GET /api/users/{user_id}": Probe(row("user", user_id="id")),
    "PATCH /api/users/{user_id}": Probe(row("user", user_id="id"), body={"is_active": True}),
    "POST /api/users": Probe(body={"email": f"sweep-{uuid.uuid4().hex}@example.invalid", "password": "isolation-sweep-1234"}),
    "POST /api/users/{user_id}/reset-password": Probe(row("user", user_id="id")),
    # app/api/vendors.py
    "GET /api/vendors": Probe(row("person", person_id="id"), status=200, because=EMPTY_FILTER),
    "POST /api/vendors": Probe(row("person", person_id="id")),
    "PUT /api/vendors/{vendor_id}": Probe(row("vendor", vendor_id="id")),
}
