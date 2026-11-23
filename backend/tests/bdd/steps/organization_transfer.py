"""Organization archive steps use HTTP for every product operation."""
import hashlib
import io
import json
import tarfile
import uuid

import pytest
from pytest_bdd import given, when, then

from tests.bdd.browser import browser_world as browser_world
from tests.test_rent_changes import rent_db as rent_db, rent_world as rent_world, api_client
from tests.test_organization_export import export_store as export_store
from tests.signup_support import signup_mail as signup_mail


@pytest.fixture
def export_client(world, rent_world, export_store):
    source = api_client(rent_world)
    try:
        yield source
    finally:
        world.run(source.aclose())


@given("a separate organization has rental records for export")
def export_organization_records(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world


@given("a separate organization has rental records and an original for export")
def export_organization(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    original = b"%PDF-1.4\n% Original for the portable archive\n%%EOF\n"
    answer = world.act("POST", "/api/attachments", data={"property_id": str(rent_world["property"])},
                       files={"file": ("Original.pdf", original, "application/pdf")})
    assert answer.status_code == 201, world.describe_response()
    world.data["export_original"] = original
    world.data["export_document_id"] = answer.json()["id"]


@given("a separate organization has a booked payment and its Storno for export")
def export_organization_with_storno(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    bank = world.act("POST", "/api/bank-accounts", json={
        "property_id": str(rent_world["property"]), "account_type": "LAUFEND", "label": "Archive reversal bank"})
    assert bank.status_code == 201, world.describe_response()
    created = world.act("POST", "/api/payments", json={
        "bank_account_id": bank.json()["id"], "direction": "IN", "amount": "700.00", "value_date": "2026-03-15",
        "payment_type": "KALTMIETE", "tenant_profile_id": str(rent_world["tenant"]),
        "lease_agreement_id": str(rent_world["lease"]), "period_year": 2026, "period_month": 3})
    assert created.status_code == 201, world.describe_response()
    original = created.json()["id"]
    assert world.act("POST", f"/api/payments/{original}/book").status_code == 200, world.describe_response()
    storno = world.act("POST", f"/api/payments/{original}/reverse")
    assert storno.status_code == 201, world.describe_response()
    world.data["payment_original"], world.data["payment_storno"] = original, storno.json()["id"]


@given("a separate organization has a property from the property form with a unit and a booked rent payment for export")
def export_organization_with_form_property(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    # "+ Neues Objekt" always sends an Idempotency-Key, so the server keeps a creation receipt.
    created = world.act("POST", "/api/properties", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "name": "Probehaus", "street": "Probeweg 1", "zip_code": "12345", "city": "Teststadt",
        "management_type": "MIET", "fiscal_year_start_month": 1})
    assert created.status_code == 201, world.describe_response()
    prop = created.json()["id"]
    unit = world.act("POST", "/api/units", json={"property_id": prop, "number": "W1", "usage_type": "WOHNUNG"})
    assert unit.status_code == 201, world.describe_response()
    lease = world.act("POST", "/api/leases", json={"unit_id": unit.json()["id"],
        "tenant_profile_id": str(rent_world["tenant"]), "valid_from": "2026-01-01", "base_rent": "700.00",
        "advance_payment": "0.00"})
    assert lease.status_code == 201, world.describe_response()
    bank = world.act("POST", "/api/bank-accounts", json={"property_id": prop, "account_type": "LAUFEND", "label": "Probehaus Konto"})
    assert bank.status_code == 201, world.describe_response()
    payment = world.act("POST", "/api/payments", json={
        "bank_account_id": bank.json()["id"], "direction": "IN", "amount": "700.00", "value_date": "2026-03-15",
        "payment_type": "KALTMIETE", "tenant_profile_id": str(rent_world["tenant"]),
        "lease_agreement_id": lease.json()["id"], "period_year": 2026, "period_month": 3})
    assert payment.status_code == 201, world.describe_response()
    assert world.act("POST", f"/api/payments/{payment.json()['id']}/book").status_code == 200, world.describe_response()
    world.data["form_property"], world.data["form_payment"] = prop, payment.json()["id"]


@given("a separate organization has a rent payment that a clerk booked on a scope-classified bank account for export")
def export_organization_with_clerk_payment(world, rent_world, export_client):
    from tests.bdd.steps.browser_setup import create
    world.actor = export_client
    world.data["export_source"] = rent_world
    # No person data domain and no property creation receipt, so the counts compare entity by entity.
    scope = create(world, "/api/accounting-scopes", {"property_id": str(rent_world["property"]), "scope_type": "MIET",
        "label": "Mieten Probeweg", "valid_from": "2026-01-01", "valid_to": "2026-12-31", "whole_property": True}, keyed=True)
    for user, capabilities in ((rent_world["admin"], ["read", "prepare", "approve", "confirm", "export"]),
                               (rent_world["clerk"], ["read", "prepare", "confirm"])):
        create(world, f"/api/accounting-scopes/{scope['id']}/grants", {"user_id": str(user), "capabilities": capabilities,
            "reason": "Synthetic rent booking"}, keyed=True)
    bank = create(world, "/api/bank-accounts", {"property_id": str(rent_world["property"]), "account_type": "LAUFEND",
        "label": "Mietkonto Probeweg"})
    create(world, f"/api/accounting-scopes/{scope['id']}/bindings", {
        "resource_type": "bank_account", "resource_id": bank["id"], "reason": "Konto der Mieten"})
    # The clerk records the payment in the payment form and books it; the database names the clerk as the
    # reviewer of the payment's binding.
    clerk = api_client(rent_world, "clerk")
    try:
        created = world.send(clerk, "POST", "/api/payments", json={
            "bank_account_id": bank["id"], "direction": "IN", "amount": "700.00", "value_date": "2026-03-15",
            "payment_type": "KALTMIETE", "tenant_profile_id": str(rent_world["tenant"]),
            "lease_agreement_id": str(rent_world["lease"]), "period_year": 2026, "period_month": 3})
        assert created.status_code == 201, world.describe_response()
        booked = world.send(clerk, "POST", f"/api/payments/{created.json()['id']}/book")
        assert booked.status_code == 200, world.describe_response()
    finally:
        world.run(clerk.aclose())
    world.data.update(scope=scope, clerk_payment=created.json()["id"])
    account = world.act("GET", f"/api/payments/mietkonto/lease/{rent_world['lease']}", params={
        "accounting_scope_id": scope["id"], "period_start": "2026-01-01", "period_end": "2026-12-31"})
    assert account.status_code == 200, world.describe_response()
    world.data["clerk_account"] = {key: account.json()[key] for key in ("soll_total", "ist_total", "balance")}
    assert world.data["clerk_account"]["ist_total"] == "700.00"


@given("a separate organization has a task on its property and a task without a property from the task form for export")
def export_organization_with_form_tasks(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    # The task form sends the assignee it pre-selects (the signed-in user) and the property or none.
    world.data["form_tasks"] = []
    for title, due, property_id in (("Dach prüfen", "2026-12-01", str(rent_world["property"])),
                                    ("Heizung warten lassen", "2027-01-15", None)):
        created = world.act("POST", "/api/tasks", json={
            "title": title, "note": "", "due_date": due, "assigned_user_id": str(rent_world["admin"]),
            "property_id": property_id, "unit_id": None, "lease_agreement_id": None})
        assert created.status_code == 201, world.describe_response()
        world.data["form_tasks"].append(created.json())
    done = world.act("PATCH", f"/api/tasks/{world.data['form_tasks'][1]['id']}", json={"completed": True})
    assert done.status_code == 200, world.describe_response()
    world.data["form_tasks"][1] = done.json()


@given("a separate organization has an owner contact with a data domain for export")
def export_organization_with_person_data_domain(world, rent_world, export_client):
    world.actor = export_client
    world.data["export_source"] = rent_world
    # The contact pages create the person and its owner profile, then the data domain under an Idempotency-Key.
    person = world.act("POST", "/api/persons", json={"first_name": "Synthetic", "last_name": "Owner contact"})
    assert person.status_code == 201, world.describe_response()
    owner = world.act("POST", "/api/owner-profiles", json={"person_id": person.json()["id"], "notes": ""})
    assert owner.status_code == 201, world.describe_response()
    domain = world.act("POST", "/api/person-data-domains", headers={"Idempotency-Key": str(uuid.uuid4())},
                       json={"person_id": person.json()["id"], "reason_code": "INITIAL_SETUP"})
    assert domain.status_code == 201, world.describe_response()
    world.data["contact"], world.data["contact_domain"] = person.json()["id"], domain.json()["id"]


@given("a separate organization has a contact data domain and a grant to its administrator that a second administrator made for export")
def export_organization_with_second_administrators_contact_setup(world, rent_world, rent_db, export_client, confirmed_second_factor):
    from tests.test_person_data_domain_restore import contact_setup
    from tests.test_scope_binding_restore import second_administrator
    # The second administrator adds the contact Anna Berg, prepares its data domain and grants it to the first
    # administrator, with the requests the contact pages send (an Idempotency-Key on the domain and on the grant).
    source = world.run(second_administrator(rent_db, rent_world, confirmed_second_factor))
    world.actor = export_client
    world.data["export_source"] = source
    person, domain, grant = world.run(contact_setup(source))
    world.data.update(contact=person, contact_domain=domain["id"], contact_grant=grant["id"])


@given("a separate organization has a contact data domain granted to its clerk and another one whose grant was revoked again for export")
def export_organization_with_grants_to_its_clerk(world, rent_world, rent_db, export_client):
    from sqlalchemy import text
    from tests.test_person_data_domain_restore import contact_setup, listed
    world.actor = export_client
    world.data["export_source"] = rent_world
    # The clerk has a name of their own in the user list; the administrator grants the data domain of Anna Berg to them,
    # prepares the data domain of Bernd Weber, grants it to them as well and revokes that grant again.
    world.run(rent_db.execute(text('UPDATE "user" SET display_name=:name WHERE id=:clerk'),
                              {"name": "Clara Clerk", "clerk": rent_world["clerk"]}))
    world.run(rent_db.commit())
    contacts = [world.run(contact_setup(rent_world, maker="admin", grantee="clerk", name=name))
                for name in (("Anna", "Berg"), ("Bernd", "Weber"))]
    person, domain, grant = contacts[1]
    revoked = world.act("POST", f"/api/person-data-domains/{domain['id']}/grants/{grant['id']}/revoke")
    assert revoked.status_code == 204, world.describe_response()
    world.data["clerk_contacts"] = [(contact, world.run(listed(rent_world, contact))) for contact, _, _ in contacts]


@given("a separate organization has a WEG with two roof repair levies, an issued owner notice and a draft for export")
def export_organization_with_levy_notices(world, rent_world, export_client):
    from tests.bdd.steps.browser_setup import create
    world.actor = export_client
    world.data["export_source"] = rent_world
    # No person data domain (the import cannot insert its generated `label` column) and no property
    # creation receipt (the import leaves it out), so the counts below compare entity by entity.
    prop = create(world, "/api/properties", {"name": "Gartenstraße 5", "management_type": "WEG"})
    scale = world.act("POST", f"/api/properties/{prop['id']}/mea-scale/confirm", json={
        "denominator": 1000, "expected_scale_status": "LEGACY_UNCONFIRMED", "confirm_existing_values_as_numerators": True})
    assert scale.status_code == 200, world.describe_response()
    scope = create(world, "/api/accounting-scopes", {"property_id": prop["id"], "scope_type": "WEG",
        "label": "WEG Gartenstraße 5", "valid_from": "2026-01-01", "whole_property": True}, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {"user_id": str(rent_world["admin"]),
        "capabilities": ["read", "prepare", "approve", "confirm", "export"], "reason": "Synthetic levy notices"}, keyed=True)
    owners = []
    for number, mea, first in (("1", "600", "Anna"), ("2", "400", "Berta")):
        unit = create(world, "/api/units", {"property_id": prop["id"], "number": number, "usage_type": "WOHNUNG", "mea": mea, "area": "70"})
        person = create(world, "/api/persons", {"first_name": first, "last_name": "Berg"})
        owner = create(world, "/api/owner-profiles", {"person_id": person["id"]})
        create(world, "/api/ownership", {"unit_id": unit["id"], "owner_profile_id": owner["id"], "valid_from": "2026-01-01"})
        owners.append(owner)
    meeting = create(world, "/api/meetings", {"property_id": prop["id"], "meeting_date": "2026-05-15",
        "meeting_type": "ORDENTLICH", "location": "Gemeinschaftsraum Gartenstraße 5"})
    agenda = create(world, f"/api/meetings/{meeting['id']}/agenda", {"meeting_id": meeting["id"], "title": "Dachreparatur"})
    assert world.act("POST", f"/api/meetings/{meeting['id']}/hold").status_code == 200, world.describe_response()
    resolution = create(world, "/api/meetings/resolutions", {"meeting_id": meeting["id"], "agenda_item_id": agenda["id"],
        "wording": "Dachreparatur durch Sonderumlage von 7.000,00 € finanzieren.",
        "votes_yes": 2, "votes_no": 0, "votes_abstain": 0, "result": "ANGENOMMEN"})
    create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})
    world.data.update(property=prop, scope=scope, levy_owners=owners)
    # The levy form, the notice form and the issue button send no Idempotency-Key.
    for amount, due_date in (("5000.00", "2026-06-05"), ("2000.00", "2026-06-25")):
        create(world, "/api/special-levies", {"property_id": prop["id"], "accounting_scope_id": scope["id"],
            "amount": amount, "due_date": due_date, "resolution_id": resolution["id"]})
    notices = {}
    for owner, name in zip(owners, ("Anna", "Berta"), strict=True):
        notices[name] = create(world, "/api/dunning/owner", {
            "owner_profile_id": owner["id"], "period_start": "2026-06-01", "period_end": "2026-06-30",
            "reference_date": "2026-06-28", "accounting_scope_id": scope["id"]})
    issued = world.act("POST", f"/api/dunning/{notices['Anna']['id']}/issue")
    assert issued.status_code == 200, world.describe_response()
    notices["Anna"] = issued.json()
    # Anna holds 60 % and Berta 40 % of both levies, which are overdue on 28 June.
    assert [(name, notice["status"], notice["principal_amount"]) for name, notice in notices.items()] == [
        ("Anna", "issued", "4200.00"), ("Berta", "draft", "2800.00")]
    world.data["levy_notices"] = notices
    world.data["levy_accounts"] = {name: levy_account(world, owner["id"]) for owner, name in zip(owners, notices)}


def levy_account(world, owner):
    answer = world.act("GET", f"/api/payments/mietkonto/owner/{owner}", params={
        "accounting_scope_id": world.data["scope"]["id"], "period_start": "2026-06-01", "period_end": "2026-06-30"})
    assert answer.status_code == 200, world.describe_response()
    return {key: answer.json()[key] for key in ("soll_total", "ist_total", "balance")}


@when("its administrator downloads the organization archive")
def download_archive(world):
    answer = world.act("POST", "/api/organizations/export")
    assert answer.status_code == 200, world.describe_response()
    with tarfile.open(fileobj=io.BytesIO(answer.content), mode="r:gz") as archive:
        world.data["export_files"] = {member.name: archive.extractfile(member).read() for member in archive}
    world.data["export_id"] = answer.headers["X-Export-ID"]


@then("the archive contains its rental records and the unchanged original")
def archive_has_records(world):
    files, source = world.data["export_files"], world.data["export_source"]
    manifest = json.loads(files["manifest.json"])
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256(files[name]).hexdigest() == digest
    for entity, key in (("property", "property"), ("person", "person"), ("lease_agreement", "lease")):
        rows = [json.loads(line) for line in files[f"entities/{entity}.jsonl"].splitlines()]
        assert str(source[key]) in {row["id"] for row in rows}
    assert files[f"documents/{world.data['export_document_id']}"] == world.data["export_original"]


@then("the archive contains no credentials or data of the other organization")
def archive_is_private(world):
    files, source = world.data["export_files"], world.data["export_source"]
    content = b"".join(files.values())
    assert str(source["foreign_org"]).encode() not in content
    assert str(source["foreign_person"]).encode() not in content
    assert b"unused-synthetic-password" not in content
    assert all("password_hash" not in row for row in
               [json.loads(line) for line in files["entities/user.jsonl"].splitlines()])
    async def refuse_foreign():
        async with api_client(source, foreign=True) as foreign:
            return await foreign.get(f"/api/organizations/export/{world.data['export_id']}")
    assert world.run(refuse_foreign()).status_code == 404


@pytest.fixture
async def empty_import_organization(rent_db, rent_world, confirmed_second_factor):
    from tests.test_organization_import import import_target
    # Reuse the same fixture implementation; all product operations use HTTP.
    return await import_target.__wrapped__(rent_db, rent_world, confirmed_second_factor)


def restore_archive(world, target, release_source=None):
    from tests.test_organization_import import repack
    async def restore():
        if release_source:
            await release_source()
        async with api_client(target) as client:
            return await client.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(world.data['export_files']))})
    answer = world.run(restore())
    assert answer.status_code == 200, answer.text
    world.data['import_result'] = answer.json()
    world.data['import_target'] = target


@when('the archive is imported into an empty organization')
def import_archive(world, empty_import_organization):
    # The source organization lives on and keeps its administrator's address.
    restore_archive(world, empty_import_organization)


@when('the archive is imported into an empty organization after its source organization was deleted')
def import_archive_after_source_deletion(world, empty_import_organization, rent_db):
    from tests.test_organization_import import source_organization_deleted
    # The supported restore (issue #134): the source organization no longer
    # holds its administrator's address, which the importing administrator carries.
    source = world.data['export_source']
    restore_archive(world, empty_import_organization, lambda: source_organization_deleted(
        rent_db, source['org'], source['admin'], empty_import_organization))


@then('the imported organization has equal entity counts and normalized content')
def import_equals_source(world):
    from app.services.organization_archive import json_bytes
    source = world.data['export_files']
    async def export_target():
        async with api_client(world.data['import_target']) as target:
            return await target.post('/api/organizations/export')
    answer = world.run(export_target())
    assert answer.status_code == 200, answer.text
    from tests.test_organization_export import unpack
    target = unpack(answer.content)
    # Account credentials/custody and operation audit are deliberately local.
    # Every domain entity is compared after reversing its transfer identity map.
    mapping = world.data['import_result']['id_map']
    from app.services.organization_import_service import remap
    reverse = {v: k for values in mapping.values() for k, v in values.items()}
    for name, count in json.loads(source['manifest.json'])['row_counts'].items():
        actual_count = json.loads(target['manifest.json'])['row_counts'][name]
        assert actual_count == count + (1 if name == 'audit_event' else 0), name
        if name in {'user', 'membership', 'audit_event'}:
            continue
        original = [json.loads(line) for line in source[f'entities/{name}.jsonl'].splitlines()]
        restored = [remap(json.loads(line), reverse) for line in target[f'entities/{name}.jsonl'].splitlines()]
        assert len(restored) == count, name
        def normalize(rows):
            # The import regenerates request identifiers (an Idempotency-Key is not an identity of the data) and
            # stamps the transaction that wrote a row (`creation_xid`).
            return sorted(json_bytes({k: v for k, v in row.items() if k not in {'created_by', 'storage_uri', 'registered_at', 'request_key', 'creation_xid'}}) for row in rows)
        assert hashlib.sha256(b''.join(normalize(original))).digest() == hashlib.sha256(b''.join(normalize(restored))).digest(), name


@then('the imported payments list shows the original linked to its Storno with the archived dates')
def imported_payments_are_linked(world):
    mapping = world.data['import_result']['id_map']['payment']
    original, storno = (mapping[world.data[key]] for key in ('payment_original', 'payment_storno'))
    async def payments():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/payments')
    answer = world.run(payments())
    assert answer.status_code == 200, answer.text
    rows = {row['id']: row for row in answer.json()}
    assert (rows[original]['reversed_by_id'], rows[original]['direction']) == (storno, 'IN')
    assert (rows[storno]['reverses_id'], rows[storno]['direction']) == (original, 'OUT')
    assert {rows[original]['value_date'], rows[storno]['value_date']} == {'2026-03-15'}
    assert {rows[original]['amount'], rows[storno]['amount']} == {'700.00'}


@then("the imported payments list shows the clerk's payment of 700.00 on 15.03.2026 and the tenant account shows the archived totals")
def imported_clerk_payment(world):
    mapping = world.data['import_result']['id_map']
    source = world.data['export_source']
    async def look():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/payments'), await target.get(
                f"/api/payments/mietkonto/lease/{mapping['lease_agreement'][str(source['lease'])]}",
                params={'accounting_scope_id': mapping['accounting_scope'][world.data['scope']['id']],
                        'period_start': '2026-01-01', 'period_end': '2026-12-31'})
    payments, account = world.run(look())
    assert payments.status_code == 200 and account.status_code == 200, (payments.text, account.text)
    paid = {row['id']: row for row in payments.json()}[mapping['payment'][world.data['clerk_payment']]]
    assert (paid['amount'], paid['value_date'], paid['status']) == ('700.00', '2026-03-15', 'booked')
    assert {key: account.json()[key] for key in ('soll_total', 'ist_total', 'balance')} == world.data['clerk_account']


@then("the restored binding of that payment names the clerk's account, which cannot sign in, as its reviewer")
def restored_binding_names_the_clerk(world):
    from sqlalchemy import text
    mapping = world.data['import_result']['id_map']
    clerk = mapping['user'][str(world.data['export_source']['clerk'])]
    async def reviewer():
        from sqlalchemy.ext.asyncio import create_async_engine
        from tests.conftest import MIGRATOR_URL
        engine = create_async_engine(MIGRATOR_URL)
        try:
            async with engine.connect() as connection:
                return (await connection.execute(text(
                    'SELECT b.reviewed_by::text,u.is_active,u.password_hash FROM accounting_scope_binding b '
                    'JOIN "user" u ON u.organization_id=b.organization_id AND u.id=b.reviewed_by '
                    'WHERE b.organization_id=:org AND b.payment_id=:payment'),
                    {'org': world.data['import_target']['org'],
                     'payment': uuid.UUID(mapping['payment'][world.data['clerk_payment']])})).one()
        finally:
            await engine.dispose()
    row = world.run(reviewer())
    assert tuple(row) == (clerk, False, '!') and clerk != str(world.data['import_target']['admin'])


@then('the imported property list shows the form property and its payment of 700.00 on 15.03.2026')
def imported_form_property(world):
    mapping = world.data['import_result']['id_map']
    async def lists():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/properties'), await target.get('/api/payments')
    properties, payments = world.run(lists())
    assert properties.status_code == 200 and payments.status_code == 200, (properties.text, payments.text)
    names = {row['id']: row['name'] for row in properties.json()}
    assert names[mapping['property'][world.data['form_property']]] == 'Probehaus'
    paid = {row['id']: row for row in payments.json()}[mapping['payment'][world.data['form_payment']]]
    assert (paid['amount'], paid['value_date'], paid['status']) == ('700.00', '2026-03-15', 'booked')


@then('the imported task list shows both tasks with their titles, due dates and status and the first one linked to the imported property')
def imported_form_tasks(world):
    mapping = world.data['import_result']['id_map']
    async def tasks():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/tasks?limit=100')
    answer = world.run(tasks())
    assert answer.status_code == 200, answer.text
    rows = {row['id']: row for row in answer.json()['items']}
    assert len(rows) == 2
    linked, unlinked = world.data['form_tasks']
    first, second = (rows[mapping['task'][task['id']]] for task in (linked, unlinked))
    assert (first['title'], first['due_date'], first['completed'], first['property_id']) == (
        'Dach prüfen', '2026-12-01', False, mapping['property'][str(world.data['export_source']['property'])])
    assert (second['title'], second['due_date'], second['completed'], second['property_id']) == (
        'Heizung warten lassen', '2027-01-15', True, None)


@then("the imported owner contact shows its data domain labelled after the contact's imported identifier")
def imported_person_data_domain(world):
    mapping = world.data['import_result']['id_map']
    contact = mapping['person'][world.data['contact']]
    async def domains():
        async with api_client(world.data['import_target']) as target:
            return await target.get(f'/api/person-data-domains/page?person_id={contact}')
    answer = world.run(domains())
    assert answer.status_code == 200, answer.text
    assert [(row['id'], row['label']) for row in answer.json()['items']] == [
        (mapping['person_data_domain'][world.data['contact_domain']], f'Contact {contact}')]


@then("the imported contact shows its data domain and its grant with the archived reasons and rights")
def imported_contact_setup(world):
    from tests.test_person_data_domain_restore import listed
    mapping = world.data['import_result']['id_map']
    contact = mapping['person'][world.data['contact']]
    domains, grants = world.run(listed(world.data['import_target'], contact))
    assert [(row['id'], row['reason_code'], row['label']) for row in domains] == [
        (mapping['person_data_domain'][world.data['contact_domain']], 'TEAM_ACCESS', f'Contact {contact}')]
    assert [(row['id'], row['user_id'], row['capabilities'], row['reason_code'], row['expires_at'], row['revoked_at'])
            for row in grants] == [(mapping['person_data_grant'][world.data['contact_grant']],
                                    str(world.data['import_target']['admin']), ['export', 'read'], 'TEAM_ACCESS', None, None)]


@then("the restored domain and grant name the second administrator's account, which cannot sign in, as creator and grantor")
def restored_contact_setup_names_the_second_administrator(world, rent_db):
    from tests.test_person_data_domain_restore import actors
    mapping = world.data['import_result']['id_map']
    second = mapping['user'][str(world.data['export_source']['second_admin'])]
    found = world.run(actors(rent_db, world.data['import_target']))
    assert second != str(world.data['import_target']['admin'])
    assert {kind: (str(row.actor), row.is_active, row.password_hash) for kind, row in found.items()} == {
        'domain': (second, False, '!'), 'grant': (second, False, '!')}


@then("the imported contacts show both domains with the clerk's grants, their rights and reasons, the revoked one shown as revoked")
def imported_grants_to_the_clerk(world):
    from tests.test_organization_import import in_target_ids
    from tests.test_person_data_domain_restore import listed, without_stamp
    mapping = world.data['import_result']['id_map']
    clerk = mapping['user'][str(world.data['export_source']['clerk'])]
    for source_contact, (source_domains, source_grants) in world.data['clerk_contacts']:
        domains, grants = world.run(listed(world.data['import_target'], mapping['person'][source_contact]))
        assert without_stamp(domains) == without_stamp(in_target_ids(source_domains, mapping))
        assert grants == in_target_ids(source_grants, mapping)
        assert [(row['user_id'], row['user_display_name'], row['capabilities'], row['reason_code'], row['expires_at'])
                for row in grants] == [(clerk, 'Clara Clerk', ['export', 'read'], 'TEAM_ACCESS', None)]
    live, revoked = (grants[0] for _, (_, grants) in world.data['clerk_contacts'])
    assert live['revoked_at'] is None and revoked['revoked_at'] is not None


@then("the clerk's account is listed as deactivated")
def imported_clerk_is_deactivated(world):
    mapping = world.data['import_result']['id_map']
    clerk = mapping['user'][str(world.data['export_source']['clerk'])]
    async def users():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/users')
    answer = world.run(users())
    assert answer.status_code == 200, answer.text
    assert [(row['display_name'], row['is_active'], row['role']) for row in answer.json() if row['id'] == clerk] == [
        ('Clara Clerk', False, 'CLERK')]


@then('the imported owner notices and owner accounts show the archived levy amounts')
def imported_levy_notices(world):
    mapping = world.data['import_result']['id_map']
    async def read():
        async with api_client(world.data['import_target']) as target:
            return [await target.get(f"/api/dunning/{mapping['dunning_notice'][notice['id']]}")
                    for notice in world.data['levy_notices'].values()]
    anna, berta = world.run(read())
    assert anna.status_code == 200 and berta.status_code == 200, (anna.text, berta.text)
    assert [(row['status'], row['level'], row['principal_amount'], row['fee_amount']) for row in (anna.json(), berta.json())] == [
        ('issued', 1, '4200.00', '0.00'), ('draft', 1, '2800.00', '0.00')]
    # Only the first owner's notice was issued, and it keeps its archived issue time.
    assert anna.json()['issued_at'] == world.data['levy_notices']['Anna']['issued_at']
    scope = mapping['accounting_scope'][world.data['scope']['id']]
    async def accounts():
        async with api_client(world.data['import_target']) as target:
            return [await target.get(f"/api/payments/mietkonto/owner/{mapping['owner_profile'][owner['id']]}", params={
                'accounting_scope_id': scope, 'period_start': '2026-06-01', 'period_end': '2026-06-30'})
                for owner in world.data['levy_owners']]
    restored = {}
    for name, answer in zip(world.data['levy_accounts'], world.run(accounts())):
        assert answer.status_code == 200, answer.text
        restored[name] = {key: answer.json()[key] for key in ('soll_total', 'ist_total', 'balance')}
    assert restored == world.data['levy_accounts']


@when('a required person is removed from the archive before import')
def missing_person(world, empty_import_organization):
    from tests.test_organization_import import repack
    files = dict(world.data['export_files'])
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    async def restore():
        async with api_client(empty_import_organization) as target:
            return await target.post('/api/organizations/import', files={'file': ('organization.tar.gz', repack(files))})
    world.data['missing_import'] = world.run(restore())
    world.data['import_target'] = empty_import_organization


@then('the import lists missing dependencies and writes nothing')
def missing_dependency_refusal(world):
    answer = world.data['missing_import']
    assert answer.status_code == 422, answer.text
    assert any(item['entity'] == 'person' and item['count'] == 2 for item in answer.json()['detail']['missing_dependencies'])
    async def properties():
        async with api_client(world.data['import_target']) as target:
            return await target.get('/api/properties')
    assert world.run(properties()).json() == []


@when('the administrator inspects the import refusal in the browser')
def browser_import_refusal(world, browser_world, empty_import_organization, tmp_path, confirmed_second_factor):
    import uuid
    from playwright.async_api import expect
    from tests.bdd.steps.browser_common import answer_second_factor, hosted_stack
    from tests.test_organization_import import repack
    email = f"import-browser-{uuid.uuid4()}@example.invalid"
    password = 'Synthetic-Import-Browser-123'
    async def prepare_user():
        async with api_client(empty_import_organization) as api:
            answer = await api.post('/api/users', json={'email': email, 'password': password,
                'display_name': 'Synthetische Importprüfung', 'role': 'OWNER_ADMIN'})
            assert answer.status_code == 201, answer.text
            return answer.json()['id']
    user_id = world.run(prepare_user())
    if hosted_stack():
        confirmed_second_factor(empty_import_organization['org'], user_id)
    files = dict(world.data['export_files'])
    files['entities/person.jsonl'] = b''
    manifest = json.loads(files['manifest.json'])
    manifest['row_counts']['person'] = 0
    files['manifest.json'] = json.dumps(manifest).encode()
    archive = repack(files)

    async def operate():
        page = browser_world.page
        await browser_world.visit('/users')
        await page.get_by_role('button', name='Abmelden', exact=True).click()
        await page.get_by_label('E-Mail', exact=True).fill(email)
        await page.get_by_label('Passwort', exact=True).fill(password)
        await page.get_by_role('button', name='Anmelden', exact=True).click()
        await answer_second_factor(page)
        await expect(page.get_by_role('link', name='Objekte', exact=True)).to_be_visible()
        await browser_world.visit('/users')
        await page.get_by_label('Exportarchiv', exact=True).set_input_files({
            'name': 'synthetic-organization.tar.gz', 'mimeType': 'application/gzip', 'buffer': archive})
        await page.get_by_role('button', name='Daten importieren', exact=True).click()
        alert = page.get_by_role('alert')
        await expect(alert).to_contain_text('Fehlende Abhängigkeiten')
        await expect(alert).not_to_contain_text('tenant_profile')
        disclosure = page.locator('details').filter(has=page.get_by_text('Technische Details', exact=True))
        await expect(disclosure).not_to_have_attribute('open', '')
        await disclosure.get_by_text('Technische Details', exact=True).click()
        await expect(disclosure).to_contain_text('person')
        await expect(disclosure).to_contain_text(str(world.data['export_source']['person']))
        for width, height, label in ((1280, 1000, 'desktop'), (360, 844, '360')):
            await page.set_viewport_size({'width': width, 'height': height})
            if width == 360:
                await expect(page.locator('aside').filter(has=page.locator('nav'))).not_to_be_in_viewport()
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            await page.screenshot(path=tmp_path / f'import-error-{label}.png', full_page=True)
        assert await page.get_by_label('Exportarchiv').input_value()
        assert not browser_world.errors
    browser_world.run(operate())


@then('the import explanation and technical details fit desktop and phone width')
def browser_import_geometry(browser_world):
    assert not browser_world.errors


@when('its administrator requests the retention preview')
def deletion_preview(world):
    answer = world.act('POST', '/api/organizations/deletion')
    assert answer.status_code == 200, world.describe_response()
    world.data['deletion_preview'] = answer.json()


@then('the preview offers a fresh export and explains operator-only retention')
def deletion_notice(world):
    preview = world.data['deletion_preview']
    assert preview['export_url'] == '/api/organizations/export'
    assert 'Nach der Löschung ist keine Anmeldung mehr möglich' in preview['notice']
    assert 'beim Betreiber' in preview['notice']
    assert preview['retention']['retained']['attachment']['count'] == 1


@when('the administrator confirms the exact organization name')
def confirm_deletion(world):
    answer = world.act('POST', '/api/organizations/deletion/confirm', json={'organization_name': world.data['deletion_preview']['organization_name']})
    assert answer.status_code == 200, world.describe_response()


@then('the organization is read-only during cooling-off')
def readonly_deletion(world):
    answer = world.act('POST', '/api/persons', json={'last_name': 'Blocked'})
    assert answer.status_code == 409, world.describe_response()


@when('the operator removes the second factor of the administrator')
def operator_removes_second_factor(world, rent_db):
    from sqlalchemy import text
    source = world.data['export_source']

    async def remove():
        async with rent_db.begin():
            for table in ('user_recovery_code', 'user_second_factor'):
                await rent_db.execute(text(f'DELETE FROM {table} WHERE organization_id=:org AND user_id=:admin'),
                                      {'org': source['org'], 'admin': source['admin']})
    world.run(remove())


@when('the administrator enrols a second factor')
def administrator_enrols_second_factor(world):
    from tests.bdd.steps.second_factor import enrol_second_factor
    world.run(enrol_second_factor(world.actor))


@when('the administrator revokes the deletion')
def revoke_deletion(world):
    answer = world.act('POST', '/api/organizations/deletion/cancel')
    assert answer.status_code == 200, world.describe_response()


@then('the organization accepts new personal records again')
def writable_deletion(world):
    answer = world.act('POST', '/api/persons', json={'last_name': 'Allowed'})
    assert answer.status_code == 201, world.describe_response()


@when('the operator executes deletion after seven days')
def execute_deletion(world, rent_db):
    from app.services.organization_deletion_service import run_deletion
    from datetime import datetime, timedelta, timezone
    async def execute():
        async with rent_db.begin():
            return await run_deletion(rent_db, world.data['export_source']['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert world.run(execute())


@given('its administrator imports and books a synthetic rent payment')
def imported_rent_voucher(world, rent_db):
    from sqlalchemy import text
    from tests.test_organization_deletion import import_erasure_payments

    async def prepare():
        bank, _, payments = await import_erasure_payments(world.actor, world.data['export_source'])
        row = await rent_db.scalar(text('SELECT to_jsonb(p) FROM payment p WHERE id=CAST(:id AS uuid)'),
            {'id': payments['700.00']})
        bank_row = await rent_db.scalar(text('SELECT to_jsonb(b) FROM bank_transaction b WHERE id=CAST(:id AS uuid)'),
            {'id': row['bank_transaction_id']})
        account = await rent_db.scalar(text('SELECT to_jsonb(b) FROM bank_account b WHERE id=CAST(:id AS uuid)'), {'id': bank})
        await rent_db.rollback()
        return bank, row, bank_row, account
    world.data['bank_erasure'] = world.run(prepare())


@then('the imported voucher and own account are unchanged and its bank line is anonymized')
def anonymous_bank_dependencies(world, rent_db):
    from sqlalchemy import text

    async def verify():
        bank, before, bank_before, account_before = world.data['bank_erasure']
        after = await rent_db.scalar(text('SELECT to_jsonb(p) FROM payment p WHERE id=CAST(:id AS uuid)'), {'id': before['id']})
        assert after == before
        row = await rent_db.scalar(text('SELECT to_jsonb(b) FROM bank_transaction b WHERE id=CAST(:id AS uuid)'),
            {'id': before['bank_transaction_id']})
        assert row['bank_account_id'] == bank
        assert row['import_hash'] == bank_before['import_hash']
        assert all(row[key] == 'Gelöscht' for key in ('counterparty_name', 'counterparty_iban', 'purpose'))
        account = await rent_db.scalar(text('SELECT to_jsonb(b) FROM bank_account b WHERE id=CAST(:id AS uuid)'), {'id': bank})
        assert account == account_before
        await rent_db.rollback()
    world.run(verify())


@then('the original remains unchanged and the former owner cannot sign in')
def erased_organization(world, export_store):
    source = world.data['export_source']
    assert (answer := world.act('GET', '/api/auth/session')).status_code == 401, answer.text
    files = list((export_store._root / str(source['org'])).rglob('*.bin'))
    assert len(files) == 1 and files[0].read_bytes() == world.data['export_original']


@when('the destination administrator confirms and revokes deletion')
def destination_deletion(world):
    async def lifecycle():
        async with api_client(world.data['import_target']) as target:
            preview = await target.post('/api/organizations/deletion')
            assert preview.status_code == 200, preview.text
            confirmed = await target.post('/api/organizations/deletion/confirm', json={'organization_name': preview.json()['organization_name']})
            assert confirmed.status_code == 200, confirmed.text
            cancelled = await target.post('/api/organizations/deletion/cancel')
            assert cancelled.status_code == 200, cancelled.text
    world.run(lifecycle())


@then('the source organization still has its original records and session')
def source_unchanged(world):
    async def source():
        async with api_client(world.data['export_source']) as client:
            assert (await client.get('/api/auth/session')).status_code == 200
            people = await client.get('/api/persons')
            assert people.status_code == 200 and len(people.json()) == 2
            properties = await client.get('/api/properties')
            assert properties.status_code == 200 and len(properties.json()) == 2
    world.run(source())


@pytest.fixture
def browser_deletion_organization(world, signup_mail, monkeypatch):
    import uuid
    import psycopg2
    from app.core.config import settings
    from tests.conftest import MIGRATOR_SYNC_URL, clear_disposable_source_context
    from tests.signup_support import BODY, confirmation_token

    monkeypatch.setattr(settings, 'deployment_mode', 'hosted')
    body = BODY | {'email': f'deletion-{uuid.uuid4().hex}@example.test',
                   'organization_name': 'Hausverwaltung mit langem Namen GmbH'}
    assert world.send(world.anonymous, 'POST', '/api/signup', json=body).status_code == 202
    answer = world.send(world.anonymous, 'POST', '/api/signup/confirm',
                        json={'token': confirmation_token(signup_mail), 'password': body['password']})
    assert answer.status_code == 201, answer.text
    try:
        yield body
    finally:
        # Reuse the guarded disposable-DB cleanup for retained originals; normal
        # DELETE cannot remove immutable attachment/source evidence. The runner
        # owns the shared DB lock. Remove this signup's organization as well,
        # including when the UI failed before cancellation.
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
            org = answer.json()['organization_id']
            cursor.execute('DELETE FROM organization_deletion WHERE organization_id=%s',
                           (org,))
            clear_disposable_source_context(cursor)
            for table in ('audit_event', 'membership', 'management_mandate', 'user'):
                cursor.execute(f'DELETE FROM public."{table}" WHERE organization_id=%s', (org,))
            cursor.execute('DELETE FROM organization WHERE id=%s', (org,))
            cursor.execute('SELECT count(*) FROM organization WHERE id=%s', (org,))
            assert cursor.fetchone() == (0,)


@when('the administrator reviews and cancels deletion in the browser')
def browser_lifecycle(browser_world, browser_deletion_organization, tmp_path, confirmed_second_factor):
    import os
    from tests.bdd.browser import ROOT
    from tests.bdd.steps.browser_common import hosted_stack, hosted_session, user_identity
    from playwright.async_api import expect
    screenshots = ROOT / 'docs/assets/design' if os.environ.get('VERWALINO_CAPTURE_DESIGN') == '1' else tmp_path
    async def operate():
        page = browser_world.page
        credentials = browser_deletion_organization
        if hosted_stack():
            from app.core.config import settings
            token = hosted_session(confirmed_second_factor, *user_identity(credentials['email']))
            await page.context.add_cookies([{'name': settings.session_cookie_name, 'value': token,
                                             'url': browser_world.session.base_url}])
        else:
            login = await page.request.post('/api/auth/login', data={
                'email': credentials['email'], 'password': credentials['password']})
            assert login.status == 200, await login.text()
        prop = await page.request.post('/api/properties', data={
            'name': 'Wohnhaus mit langem synthetischem Namen', 'management_type': 'MIET'})
        assert prop.status == 201, await prop.text()
        original = await page.request.post('/api/attachments', multipart={
            'property_id': (await prop.json())['id'],
            'file': {'name': 'Synthetischer Nachweis.pdf', 'mimeType': 'application/pdf',
                     'buffer': b'%PDF-1.4\n% Synthetic lifecycle original\n%%EOF\n'},
        })
        assert original.status == 201, await original.text()
        await browser_world.visit('/users')
        await expect(page.get_by_role('button', name='Organisation löschen', exact=True)).to_be_visible()
        await page.set_viewport_size({'width': 1280, 'height': 1000})
        await page.screenshot(path=screenshots / 'organization-data-desktop.png', full_page=True)
        await page.get_by_role('button', name='Organisation löschen', exact=True).click()
        dialog = page.get_by_role('dialog', name='Organisation löschen', exact=True)
        await expect(dialog.get_by_role('button', name='Jetzt frischen Export erstellen', exact=True)).to_be_visible()
        await page.screenshot(path=screenshots / 'organization-deletion-desktop.png', full_page=True)
        await page.set_viewport_size({'width': 360, 'height': 844})
        await page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        box = await dialog.bounding_box()
        await page.screenshot(path=screenshots / 'organization-deletion-360.png', full_page=True)
        assert box and box['y'] >= 0 and box['y'] + box['height'] <= 844, box
        export_box = await dialog.get_by_role('button', name='Jetzt frischen Export erstellen', exact=True).bounding_box()
        assert export_box and export_box['y'] >= 0 and export_box['y'] + export_box['height'] <= 844, export_box
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        organization = await page.request.get('/api/organizations/me')
        name = (await organization.json())['name']
        await dialog.get_by_label('Organisationsname zur Bestätigung').fill(name)
        await dialog.get_by_role('button', name='Löschung bestätigen', exact=True).click()
        await expect(dialog).not_to_be_visible()
        banner = page.get_by_role('status').filter(has_text='schreibgeschützt')
        await expect(banner).to_be_visible()
        await page.screenshot(path=screenshots / 'organization-cooling-off-360.png', full_page=True)
        await page.get_by_role('button', name='Löschung widerrufen', exact=True).click()
        await expect(banner).not_to_be_visible()
        await page.screenshot(path=screenshots / 'organization-export-360.png', full_page=True)
    browser_world.run(operate())


@then('the organization controls fit desktop and phone width')
def browser_lifecycle_geometry(browser_world):
    from playwright.async_api import expect
    async def verify():
        page = browser_world.page
        assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        await expect(page.get_by_role('button', name='Organisation löschen', exact=True)).to_be_visible()
        assert not browser_world.errors
    browser_world.run(verify())
