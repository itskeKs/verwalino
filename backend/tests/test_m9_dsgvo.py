"""M9 DSGVO integration tests -- request_deletion / anonymize / export + RBAC.

Proves the ADR-0012 reconciliation against the live DB:
  * A retention-bound person (vendor of a confirmed cost entry) is RESTRICTED,
    never anonymized; the bound Beleg stays byte-for-byte unchanged (invariant).
  * An explicit anonymize on a bound person is refused (409) until retention.
  * An unbound person is anonymized immediately; PII is neutralized; idempotent.
  * A person with a live lease cannot be erased (409).
  * Endpoints are OWNER_ADMIN-only; tenant-isolated; unknown id -> 404.

The pure restrict/anonymize logic is unit-tested in tests/engine (engine.dsgvo);
here we test the service wiring + DB invariants.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from tests.test_rent_changes import rent_db as rent_db, rent_world as rent_world, api_client
from tests.test_organization_export import export_store as export_store
from tests.person_retention_support import repayment_recipient, manual_cost_recipient


@pytest.mark.asyncio
@pytest.mark.parametrize('invoice_year,service_year,until,retained', [
    (2020, 2017, '2028-12-31', True), (2000, 1997, '2008-12-31', False)])
@pytest.mark.parametrize('operation', ['status', 'request-deletion'])
async def test_manual_invoice_and_service_years_share_the_existing_governing_deadline(
        rent_world, export_store, invoice_year, service_year, until, retained, operation):
    async with api_client(rent_world) as api:
        person, cost = await manual_cost_recipient(api, rent_world['property'],
            invoice_year=invoice_year, service_year=service_year)
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['retained']['person'] == {
            'count': 1, 'until': until, 'basis': 'GoBD / ADR-0012; required dependencies'}
        assert preview.json()['retention']['erased']['person'] == (2 if retained else 3)
        url = f"/api/dsgvo/persons/{person['id']}"
        result = await (api.get(url + '/status') if operation == 'status' else api.post(url + '/request-deletion'))
        assert result.status_code == 200, result.text
        if operation == 'request-deletion':
            assert result.json()['applied'] == ('RESTRICTED' if retained else 'ANONYMIZED')
        assert result.json()['governing_retention_until'] == until
        assert result.json()['decision'] == ('RESTRICT' if retained else 'ANONYMIZE_NOW')
        disclosure = await api.get(url + '/export')
        assert disclosure.status_code == 200, disclosure.text
        assert disclosure.json()['retention_bindings'] == [{'kind': 'COST_ENTRY_VENDOR',
            'entity_type': 'cost_entry', 'entity_id': cost['id'], 'retention_until': until}]
        assert disclosure.json()['person']['last_name'] == (
            'Anonymisiert' if operation == 'request-deletion' and not retained else person['last_name'])
        assert (await api.get(f"/api/cost-entries/{cost['id']}")).json() == cost


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['tenant', 'owner'])
async def test_booked_repayment_alone_restricts_person_in_status_command_and_export(rent_world, rent_db, export_store, role):
    async with api_client(rent_world) as api:
        person, payment = await repayment_recipient(api, rent_world['property'], role=role)
        evidence = text('SELECT row_to_json(t)::text FROM payment t WHERE id=CAST(:id AS uuid)')
        original = await rent_db.scalar(evidence, {'id': payment['id']})
        audit = text('SELECT row_to_json(t)::text FROM audit_event t WHERE entity_id=CAST(:id AS uuid) ORDER BY id')
        original_audit = (await rent_db.execute(audit, {'id': payment['id']})).scalars().all()
        await rent_db.rollback()
        url = f"/api/dsgvo/persons/{person['id']}"
        status = await api.get(url + '/status')
        assert status.status_code == 200, status.text
        assert status.json()['decision'] == 'RESTRICT'
        assert status.json()['bindings'] == [{'kind': 'BOOKED_PAYMENT', 'entity_type': 'payment',
            'entity_id': payment['id'], 'retention_until': payment['retention_until']}]
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['retained']['person']['count'] == 1
        assert preview.json()['retention']['retained']['person']['until'] == payment['retention_until']
        assert (await api.post(url + '/anonymize')).status_code == 409
        erased = await api.post(url + '/request-deletion')
        assert erased.status_code == 200, erased.text
        assert erased.json()['applied'] == 'RESTRICTED'
        exported = await api.get(url + '/export')
        assert exported.status_code == 200, exported.text
        assert exported.json()['person']['last_name'] == person['last_name']
        assert exported.json()['retention_bindings'] == status.json()['bindings']
        assert (await api.get(f"/api/payments/{payment['id']}")).json() == payment
        assert await rent_db.scalar(evidence, {'id': payment['id']}) == original
        assert (await rent_db.execute(audit, {'id': payment['id']})).scalars().all() == original_audit
        await rent_db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('role', ['tenant', 'owner'])
@pytest.mark.parametrize('legacy', [False, True], ids=['stored-date', 'missing-date'])
@pytest.mark.parametrize('value_date,retained', [('2026-02-01', True), ('2000-02-01', False)])
async def test_historical_payment_dates_agree_with_organization_preview(rent_world, rent_db, export_store, role, legacy, value_date, retained):
    async with api_client(rent_world) as api:
        person, payment = await repayment_recipient(api, rent_world['property'], role=role,
            value_date=value_date, book=not legacy)
        if legacy:
            # Schema-valid historical booking, not an edit of a frozen row.
            await rent_db.execute(text("UPDATE payment SET status='booked' WHERE id=CAST(:id AS uuid)"),
                                  {'id': payment['id']})
            await rent_db.commit()
        url = f"/api/dsgvo/persons/{person['id']}"
        state = await api.get(url + '/status')
        assert state.status_code == 200, state.text
        assert state.json()['bindings'][0]['retention_until'] == f'{int(value_date[:4]) + 8}-12-31'
        assert state.json()['decision'] == ('RESTRICT' if retained else 'ANONYMIZE_NOW')
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['erased']['person'] == (2 if retained else 3)
        result = await api.post(url + '/request-deletion')
        assert result.status_code == 200, result.text
        assert result.json()['applied'] == ('RESTRICTED' if retained else 'ANONYMIZED')


@pytest.mark.asyncio
async def test_draft_payment_and_unbound_person_are_erasable(rent_world, export_store):
    async with api_client(rent_world) as api:
        draft_person, _ = await repayment_recipient(api, rent_world['property'], book=False)
        solo = await api.post('/api/persons', json={'last_name': 'Unbound comparison'})
        assert solo.status_code == 201, solo.text
        for person in (draft_person, solo.json()):
            url = f"/api/dsgvo/persons/{person['id']}"
            state = await api.get(url + '/status')
            assert state.status_code == 200, state.text
            assert state.json()['bindings'] == []
            assert state.json()['decision'] == 'ANONYMIZE_NOW'
            erased = await api.post(url + '/request-deletion')
            assert erased.status_code == 200, erased.text
            assert erased.json()['applied'] == 'ANONYMIZED'


@pytest.mark.asyncio
async def test_payment_retention_keeps_role_isolation_and_hidden_financial_access(rent_world, rent_db, export_store):
    w = rent_world
    async with api_client(w) as api, api_client(w, actor='clerk') as clerk, api_client(w, foreign=True) as foreign:
        person, payment = await repayment_recipient(api, w['property'])
        url = f"/api/dsgvo/persons/{person['id']}"
        for suffix, method in (('/status', 'get'), ('/export', 'get'), ('/request-deletion', 'post'), ('/anonymize', 'post')):
            assert (await getattr(clerk, method)(url + suffix)).status_code == 403
            assert (await getattr(foreign, method)(url + suffix)).status_code == 404
        scope = await rent_db.scalar(text("""INSERT INTO accounting_scope(organization_id,property_id,scope_type,
            label,valid_from,whole_property,origin,created_by) VALUES (:org,:property,'MIET','Privacy boundary',
            '2025-01-01',true,'REVIEWED',:admin) RETURNING id"""), w)
        params = {**w, 'scope': scope, 'bank': uuid.UUID(payment['bank_account_id']), 'payment': uuid.UUID(payment['id'])}
        await rent_db.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
            bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:bank,'REVIEWED','Privacy test',:admin)"""), params)
        await rent_db.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
            payment_id,payment_bank_account_id,origin,reason,reviewed_by)
            VALUES (:org,:property,:scope,:payment,:bank,'REVIEWED','Privacy test',:admin)"""), params)
        await rent_db.commit()
        for suffix, method in (('/status', 'get'), ('/export', 'get'), ('/request-deletion', 'post'), ('/anonymize', 'post')):
            denied = await getattr(api, method)(url + suffix)
            assert denied.status_code == 409, denied.text
        await rent_db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:admin,ARRAY['read'],:admin,'Privacy read only')"""), params)
        await rent_db.commit()
        state = await api.get(url + '/status')
        assert state.status_code == 200, state.text
        assert state.json()['decision'] == 'RESTRICT'
        assert (await api.get(url + '/export')).status_code == 409


@pytest.mark.asyncio
async def test_retention_catalog_and_raw_facts_remain_private(rent_db):
    categories = (await rent_db.execute(text('SELECT entity_type,kind FROM person_retention_categories()'))).all()
    assert {kind for _, kind in categories if kind} == {
        'COST_ENTRY_VENDOR', 'SETTLEMENT_ADDRESSEE', 'FINALIZED_OWNERSHIP', 'DUNNING', 'BOOKED_PAYMENT'}
    assert {entity for entity, _ in categories} == {'cost_entry', 'payment', 'settlement_run', 'settlement_line',
        'economic_plan', 'economic_plan_line', 'dunning_notice', 'invoice_document', 'letter_document'}
    for role in ('app_rw', 'app_worker', 'app_review'):
        for signature in ('person_retention_categories()', 'person_retention_bindings(uuid,uuid,text)',
                          'privacy_retention_facts(uuid,uuid)', 'privacy_retention_facts_before0159(uuid,uuid)',
                          'report_retention_recipients(jsonb)', 'retention_base_until(jsonb)', 'retention_source_until(text,jsonb)'):
            assert not await rent_db.scalar(text('SELECT has_function_privilege(:role,:signature,\'EXECUTE\')'),
                                           {'role': role, 'signature': signature})


@pytest.mark.asyncio
async def test_rental_addressee_keeps_the_existing_later_line_deadline(rent_world, rent_db, export_store):
    w = rent_world
    async with api_client(w) as api:
        response = await api.post('/api/persons', json={'last_name': 'Retained rental addressee'})
        assert response.status_code == 201, response.text
        person = response.json()['id']
        tenant = await api.post('/api/tenant-profiles', json={'person_id': person})
        assert tenant.status_code == 201, tenant.text
        run = await rent_db.scalar(text("""INSERT INTO settlement_run(organization_id,property_id,run_type,
            tenant_profile_id,period_start,period_end,status,engine_version)
            VALUES (:org,:property,'BK_MIETER',:tenant,'2017-01-01','2017-12-31','finalized','legacy') RETURNING id"""),
            {**w, 'tenant': uuid.UUID(tenant.json()['id'])})
        await rent_db.execute(text("""INSERT INTO settlement_line(organization_id,settlement_run_id,line_type,label,amount,created_at)
            VALUES (:org,:run,'SUBTOTAL','Retained total',99,'2020-06-01')"""), {**w, 'run': run})
        await rent_db.commit()
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['retained']['person']['until'] == '2028-12-31'
        state = await api.get(f'/api/dsgvo/persons/{person}/status')
        assert state.status_code == 200, state.text
        assert state.json()['bindings'] == [{'kind': 'SETTLEMENT_ADDRESSEE', 'entity_type': 'settlement_run',
            'entity_id': str(run), 'retention_until': '2028-12-31'}]
        erased = await api.post(f'/api/dsgvo/persons/{person}/request-deletion')
        assert erased.status_code == 200, erased.text
        assert erased.json()['applied'] == 'RESTRICTED'


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['weg', 'plan'])
async def test_recipient_lines_use_their_own_evidence_dates_without_a_sibling_extension(
        rent_world, rent_db, export_store, family):
    from app.engine.canonical import canonical_sha256
    w = rent_world
    async with api_client(w) as api:
        people, owners = [], []
        for name in ('Expired line recipient', 'Retained line recipient'):
            person = await api.post('/api/persons', json={'last_name': name})
            assert person.status_code == 201, person.text
            people.append(person.json())
            owner = await api.post('/api/owner-profiles', json={'person_id': person.json()['id']})
            assert owner.status_code == 201, owner.text
            owners.append(uuid.UUID(owner.json()['id']))
        root_table, line_table = ('settlement_run', 'settlement_line') if family == 'weg' else ('economic_plan', 'economic_plan_line')
        fields = "run_type" if family == 'weg' else 'plan_year,reserve_contribution_total'
        values = "'WEG_JAHRESABRECHNUNG'" if family == 'weg' else '2017,0'
        root = await rent_db.scalar(text(f"""INSERT INTO {root_table}(organization_id,property_id,{fields},
            period_start,period_end,status,engine_version,snapshot_payload,snapshot_sha256)
            VALUES (:org,:property,{values},'2017-01-01','2017-12-31','finalized','legacy','{{}}',:sha) RETURNING id"""),
            {**w, 'sha': canonical_sha256({})})
        recipient = 'addressee_owner_profile_id' if family == 'weg' else 'owner_profile_id'
        amount = 'amount' if family == 'weg' else 'planned_amount'
        for owner, year in ((owners[0], 2017), (owners[1], 2019), (owners[1], 2020)):
            await rent_db.execute(text(f"""INSERT INTO {line_table}(organization_id,{root_table}_id,{recipient},
                line_type,label,{amount},created_at) VALUES (:org,:root,:owner,'SUBTOTAL','Retained source',99,:created)"""),
                {**w, 'root': root, 'owner': owner, 'created': date(year, 6, 1)})
        await rent_db.commit()
        statement = text(f'SELECT row_to_json(t)::text FROM {line_table} t WHERE {root_table}_id=:root ORDER BY id')
        before = (await rent_db.execute(statement, {'root': root})).scalars().all()
        planned = (await rent_db.execute(text("SELECT identity,retention_until FROM organization_retention_plan(:org) WHERE entity='person'"), w)).all()
        assert {row.identity['id']: row.retention_until.isoformat() for row in planned} == {
            people[0]['id']: '2025-12-31', people[1]['id']: '2028-12-31'}
        await rent_db.rollback()
        for person, until, outcome in ((people[0], '2025-12-31', 'ANONYMIZED'), (people[1], '2028-12-31', 'RESTRICTED')):
            url = f"/api/dsgvo/persons/{person['id']}"
            status = await api.get(url + '/status')
            assert status.status_code == 200, status.text
            assert status.json()['bindings'] == [{'kind': 'FINALIZED_OWNERSHIP', 'entity_type': root_table,
                'entity_id': str(root), 'retention_until': until}]
            erased = await api.post(url + '/request-deletion')
            assert erased.status_code == 200, erased.text
            assert erased.json()['applied'] == outcome
        assert (await rent_db.execute(statement, {'root': root})).scalars().all() == before
        await rent_db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('document_until,until', [(None, '2030-12-31'), ('2024-12-31', '2028-12-31'), ('2034-12-31', '2034-12-31')])
async def test_cost_and_original_dates_use_the_same_latest_evidence_deadline(
        rent_world, rent_db, export_store, document_until, until):
    import hashlib
    from app.core.storage import make_storage_key
    w = rent_world
    async with api_client(w) as api:
        person, cost = await manual_cost_recipient(api, w['property'])
        original = b'%PDF-1.4\nSynthetic retained invoice\n%%EOF'
        digest = hashlib.sha256(original).hexdigest()
        stored = export_store.put(make_storage_key(str(w['org']), digest), original, content_type='application/pdf')
        document = uuid.uuid4()
        await rent_db.execute(text("""INSERT INTO invoice_document(id,organization_id,cost_entry_id,uploaded_by,
            storage_backend,storage_uri,original_filename,mime_type,content_byte_size,content_sha256,version,
            chain_root_id,status,released_at,retention_until,created_at) VALUES (:doc,:org,:cost,:admin,'LOCAL_FS',
            :uri,'Synthetic.pdf','application/pdf',:size,:sha,1,:doc,'active',now(),:until,'2022-06-01')"""),
            {**w, 'doc': document, 'cost': uuid.UUID(cost['id']), 'uri': stored.uri, 'size': len(original),
             'sha': digest, 'until': date.fromisoformat(document_until) if document_until else None})
        await rent_db.commit()
        original_row = await rent_db.scalar(text('SELECT row_to_json(d)::text FROM invoice_document d WHERE id=:doc'), {'doc': document})
        await rent_db.rollback()
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['retained']['person']['until'] == until
        url = f"/api/dsgvo/persons/{person['id']}"
        status = await api.get(url + '/status')
        assert status.status_code == 200, status.text
        assert status.json()['governing_retention_until'] == until
        erased = await api.post(url + '/request-deletion')
        assert erased.status_code == 200, erased.text
        assert erased.json()['applied'] == 'RESTRICTED'
        assert (await api.get(f"/api/cost-entries/{cost['id']}")).json() == cost
        assert await rent_db.scalar(text('SELECT row_to_json(d)::text FROM invoice_document d WHERE id=:doc'), {'doc': document}) == original_row
        assert hashlib.sha256(export_store.get(stored.uri)).hexdigest() == digest
        await rent_db.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize('category,kind,entity', [
    ('cost', 'COST_ENTRY_VENDOR', 'cost_entry'),
    ('rental', 'SETTLEMENT_ADDRESSEE', 'settlement_run'),
    ('weg', 'FINALIZED_OWNERSHIP', 'settlement_run'),
    ('plan', 'FINALIZED_OWNERSHIP', 'economic_plan'),
    ('dunning', 'DUNNING', 'dunning_notice'),
])
async def test_original_categories_agree_in_disclosure_command_and_organization_plan(rent_world, rent_db, export_store, category, kind, entity):
    w = rent_world
    async with api_client(w) as api:
        person = await api.post('/api/persons', json={'first_name': 'Retained', 'last_name': 'Original category'})
        assert person.status_code == 201, person.text
        person_id = person.json()['id']
        tenant = await api.post('/api/tenant-profiles', json={'person_id': person_id})
        owner = await api.post('/api/owner-profiles', json={'person_id': person_id})
        assert tenant.status_code == owner.status_code == 201
        values = {**w, 'subject': uuid.UUID(person_id), 'recipient': uuid.UUID(tenant.json()['id']),
                  'owner': uuid.UUID(owner.json()['id'])}
        if category == 'cost':
            vendor = await rent_db.scalar(text('INSERT INTO vendor(organization_id,person_id) VALUES (:org,:subject) RETURNING id'), values)
            cost_category = await rent_db.scalar(text('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY code LIMIT 1'))
            bound = await rent_db.scalar(text("""INSERT INTO cost_entry(organization_id,property_id,cost_category_id,
                cost_category_plan_version,vendor_id,invoice_date,service_period_from,service_period_to,
                gross_amount,tenant_apportionable,status,source) VALUES (:org,:property,:category,1,:vendor,
                '2026-01-01','2026-01-01','2026-12-31',99,'UMLAGEFAEHIG','confirmed','MANUAL') RETURNING id"""),
                {**values, 'category': cost_category, 'vendor': vendor})
        elif category in ('rental', 'weg'):
            bound = await rent_db.scalar(text("""INSERT INTO settlement_run(organization_id,property_id,run_type,
                period_start,period_end,tenant_profile_id,status,engine_version,snapshot_payload,snapshot_sha256)
                VALUES (:org,:property,:type,'2026-01-01','2026-12-31',:tenant,'finalized','legacy','{}',repeat('a',64)) RETURNING id"""),
                {**values, 'type': 'BK_MIETER' if category == 'rental' else 'WEG_JAHRESABRECHNUNG',
                 'tenant': values['recipient'] if category == 'rental' else None})
            if category == 'weg':
                await rent_db.execute(text("""INSERT INTO settlement_line(organization_id,settlement_run_id,
                    addressee_owner_profile_id,line_type,label,amount) VALUES (:org,:bound,:owner,'SUBTOTAL','Original owner',99)"""),
                    {**values, 'bound': bound})
        elif category == 'plan':
            bound = await rent_db.scalar(text("""INSERT INTO economic_plan(organization_id,property_id,plan_year,
                period_start,period_end,reserve_contribution_total,status,engine_version,snapshot_payload,snapshot_sha256)
                VALUES (:org,:property,2026,'2026-01-01','2026-12-31',0,'finalized','legacy','{}',repeat('b',64)) RETURNING id"""), values)
            await rent_db.execute(text("""INSERT INTO economic_plan_line(organization_id,economic_plan_id,
                owner_profile_id,line_type,label,planned_amount) VALUES (:org,:bound,:owner,'SUBTOTAL','Original plan',99)"""),
                {**values, 'bound': bound})
        else:
            bound = await rent_db.scalar(text("""INSERT INTO dunning_notice(organization_id,tenant_profile_id,
                level,issue_date,due_date,principal_amount,fee_amount,total_amount,status,issued_at,issued_by)
                VALUES (:org,:recipient,1,'2026-01-01','2026-02-01',99,0,99,'issued',now(),:admin) RETURNING id"""), values)
        await rent_db.commit()
        original_sql = text(f'SELECT row_to_json(t)::text FROM {entity} t WHERE id=:bound')
        original = await rent_db.scalar(original_sql, {'bound': bound})
        facts = (await rent_db.execute(text('SELECT * FROM privacy_retention_facts(:org,:subject)'), values)).all()
        assert {(f.kind, f.entity, f.until_date) for f in facts} == {(entity, bound, date(2034, 12, 31))}
        org_person = (await rent_db.execute(text("SELECT * FROM organization_retention_plan(:org) WHERE entity='person'"), values)).mappings().all()
        assert any(row['identity']['id'] == person_id and row['retention_until'] == date(2034, 12, 31) for row in org_person)
        await rent_db.rollback()
        url = f'/api/dsgvo/persons/{person_id}'
        status = await api.get(url + '/status')
        assert status.status_code == 200, status.text
        assert status.json()['bindings'] == [{'kind': kind, 'entity_type': entity,
            'entity_id': str(bound), 'retention_until': '2034-12-31'}]
        erased = await api.post(url + '/request-deletion')
        assert erased.status_code == 200, erased.text
        assert erased.json()['applied'] == 'RESTRICTED'
        assert await rent_db.scalar(original_sql, {'bound': bound}) == original
        await rent_db.rollback()

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.fixture(scope="session")
def m9_dsgvo_seed(seed_data):
    """A bound vendor person, an unbound person, and a tenant with a live lease."""
    import psycopg2

    org_a = str(seed_data["org_a_id"])
    ids = {
        "prop": uuid.uuid4(),
        "unit": uuid.uuid4(),
        "person_vendor": uuid.uuid4(),
        "vendor": uuid.uuid4(),
        "cost_entry": uuid.uuid4(),
        "person_solo": uuid.uuid4(),
        "person_tenant": uuid.uuid4(),
        "tenant_profile": uuid.uuid4(),
        "lease": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"
        )
        cost_category_id = cur.fetchone()[0]

        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop"]), org_a, "DSGVO Haus", "MIET"),
        )
        cur.execute(
            "INSERT INTO unit (id, organization_id, property_id, number, usage_type, area, heated_area, mea) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["unit"]), org_a, str(ids["prop"]), "D-1", "WOHNUNG", 70, 70, 400),
        )

        # --- Bound vendor: person -> vendor -> confirmed cost entry (2024) ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, email, street, zip_code, city, iban) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_vendor"]), org_a, "Vera", "Vendor", "vera@vendor.de",
             "Lieferweg 3", "10115", "Berlin", "DE89370400440532013000"),
        )
        cur.execute(
            "INSERT INTO vendor (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["vendor"]), org_a, str(ids["person_vendor"])),
        )
        cur.execute(
            "INSERT INTO cost_entry (id, organization_id, property_id, cost_category_id, "
            "cost_category_plan_version, vendor_id, invoice_date, service_period_from, "
            "service_period_to, gross_amount, tenant_apportionable, status, source, "
            "confirmed_at, confirmed_by) "
            "VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s,%s,'NICHT_UMLAGEFAEHIG','confirmed','MANUAL',now(),%s)",
            (str(ids["cost_entry"]), org_a, str(ids["prop"]), str(cost_category_id),
             str(ids["vendor"]), date(2024, 6, 1), date(2024, 1, 1), date(2024, 12, 31),
             Decimal("1190.00"), str(seed_data["user_a_admin_id"])),
        )

        # --- Unbound person (no roles, no records) ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name, email, street, zip_code, city) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["person_solo"]), org_a, "Solo", "Solitaer", "solo@x.de",
             "Einzelweg 1", "20095", "Hamburg"),
        )

        # --- Tenant with a LIVE (open-ended) lease ---
        cur.execute(
            "INSERT INTO person (id, organization_id, first_name, last_name) VALUES (%s,%s,%s,%s)",
            (str(ids["person_tenant"]), org_a, "Tim", "Tenant"),
        )
        cur.execute(
            "INSERT INTO tenant_profile (id, organization_id, person_id) VALUES (%s,%s,%s)",
            (str(ids["tenant_profile"]), org_a, str(ids["person_tenant"])),
        )
        cur.execute(
            "INSERT INTO lease_agreement (id, organization_id, unit_id, tenant_profile_id, "
            "valid_from, valid_to, base_rent, advance_payment) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (str(ids["lease"]), org_a, str(ids["unit"]), str(ids["tenant_profile"]),
             date(2025, 1, 1), None, Decimal("700.00"), Decimal("150.00")),
        )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**seed_data, **{k: str(v) for k, v in ids.items()}}


class TestDsgvoStatusAndExport:
    @pytest.mark.asyncio
    async def test_status_bound_vendor_is_restrict(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["is_bound"] is True
        assert data["decision"] == "RESTRICT"
        assert data["purgeable_now"] is False
        # Retention = end of 2024 + 8 years -> 2032-12-31 (provisional rule).
        assert data["governing_retention_until"] == "2032-12-31"
        assert any(b["entity_type"] == "cost_entry" for b in data["bindings"])

    @pytest.mark.asyncio
    async def test_export_returns_roles_and_bindings(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/export"
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["person"]["last_name"] == "Vendor"
        assert m9_dsgvo_seed["vendor"] in data["roles"]["vendor_ids"]
        assert len(data["retention_bindings"]) >= 1

    @pytest.mark.asyncio
    async def test_unknown_person_404(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.get(f"/api/dsgvo/persons/{uuid.uuid4()}/status")
        assert r.status_code == 404


class TestDsgvoReconciliation:
    @pytest.mark.asyncio
    async def test_request_deletion_bound_restricts_and_keeps_beleg(
        self, client_a_admin, m9_dsgvo_seed, app_rw_session
    ):
        pid = m9_dsgvo_seed["person_vendor"]
        r = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["applied"] == "RESTRICTED"
        assert data["processing_restricted"] is True
        assert data["already_anonymized"] is False

        # Person PII is NOT anonymized (still bound).
        r = await client_a_admin.get(f"/api/dsgvo/persons/{pid}/export")
        assert r.json()["person"]["last_name"] == "Vendor"

        # INVARIANT: the bound cost entry is byte-for-byte unchanged.
        await app_rw_session.execute(
            text("SELECT set_config('app.current_org_id', :o, true)"),
            {"o": str(m9_dsgvo_seed["org_a_id"])},
        )
        row = await app_rw_session.execute(
            text("SELECT gross_amount, status, vendor_id FROM cost_entry WHERE id = :id"),
            {"id": m9_dsgvo_seed["cost_entry"]},
        )
        gross, status, vendor_id = row.one()
        assert gross == Decimal("1190.00")
        assert status == "confirmed"
        assert str(vendor_id) == m9_dsgvo_seed["vendor"]

    @pytest.mark.asyncio
    async def test_anonymize_bound_rejected(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/anonymize"
        )
        assert r.status_code == 409
        assert "2032-12-31" in r.json()["detail"]

    @pytest.mark.asyncio
    async def test_request_deletion_unbound_anonymizes_and_idempotent(
        self, client_a_admin, m9_dsgvo_seed
    ):
        pid = m9_dsgvo_seed["person_solo"]
        r = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["applied"] == "ANONYMIZED"
        assert data["already_anonymized"] is True

        # PII neutralized.
        exp = (await client_a_admin.get(f"/api/dsgvo/persons/{pid}/export")).json()
        assert exp["person"]["last_name"] == "Anonymisiert"
        assert exp["person"]["first_name"] == ""
        assert exp["person"]["email"] is None

        # Idempotent: a second request reports already-anonymized, no error.
        r2 = await client_a_admin.post(f"/api/dsgvo/persons/{pid}/request-deletion")
        assert r2.status_code == 200
        assert r2.json()["applied"] == "NONE_ALREADY_ANONYMIZED"

    @pytest.mark.asyncio
    async def test_active_relationship_blocks_erasure(self, client_a_admin, m9_dsgvo_seed):
        r = await client_a_admin.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_tenant']}/request-deletion"
        )
        assert r.status_code == 409
        assert "vertragliche" in r.json()["detail"].lower()


class TestDsgvoRbacAndIsolation:
    @pytest.mark.asyncio
    async def test_clerk_forbidden(self, client_a_clerk, m9_dsgvo_seed):
        r = await client_a_clerk.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_readonly_forbidden(self, client_a_readonly, m9_dsgvo_seed):
        r = await client_a_readonly.post(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/request-deletion"
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_org_404(self, client_b_admin, m9_dsgvo_seed):
        r = await client_b_admin.get(
            f"/api/dsgvo/persons/{m9_dsgvo_seed['person_vendor']}/status"
        )
        assert r.status_code == 404
