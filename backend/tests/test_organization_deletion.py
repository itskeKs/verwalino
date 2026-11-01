"""Organization erasure preserves immutable records and ends all access."""
from datetime import datetime, timedelta, timezone
import json
import asyncio
import uuid

import pytest
import pytest_asyncio

from sqlalchemy import text

from app.services.organization_deletion_service import ERASURE_SCRUB_COLUMNS
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_organization_export import export_store  # noqa: F401


@pytest_asyncio.fixture(autouse=True)
async def isolate_erasure_decisions(rent_db):
    # Other migration rehearsals must still refuse genuine retained decisions.
    # Remove only this module's new synthetic lifecycle rows at teardown.
    baseline = (await rent_db.execute(text('SELECT organization_id FROM organization_deletion'))).scalars().all()
    await rent_db.rollback()
    try:
        yield
    finally:
        await rent_db.rollback()
        async with rent_db.begin():
            await rent_db.execute(text('DELETE FROM organization_deletion WHERE NOT(organization_id=ANY(CAST(:baseline AS uuid[])))'),
                                 {'baseline': baseline})


async def assert_no_person_marker(db, org, marker):
    tables = (await db.execute(text('''SELECT c.relname FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'
      AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
        AND a.attname='organization_id' AND NOT a.attisdropped)'''))).scalars().all()
    for name in tables:
        rows = (await db.execute(text(f'SELECT to_jsonb(t)::text FROM public."{name}" t WHERE organization_id=:org'),
                                 {'org': org})).scalars().all()
        assert all(marker not in row for row in rows), name


@pytest_asyncio.fixture
async def reserve_erasure_db(rent_db, export_store):
    # Release test transactions before export_store's synchronous cleanup, even
    # when an assertion fails; its TRUNCATE must not wait on this connection.
    try:
        yield rent_db
    finally:
        await rent_db.rollback()


async def test_operator_erasure_guard_accepts_released_and_bound_imported_drafts(rent_world, reserve_erasure_db):
    from tests.test_reserve_ledger import imported_reserve_draft
    from app.services.organization_deletion_service import run_deletion
    rent_db = reserve_erasure_db
    w = rent_world
    async with api_client(w) as api:
        prop = await api.post('/api/properties', json={'name': 'Disposable reserve property', 'management_type': 'WEG'})
        assert prop.status_code == 201, prop.text
        bank = await api.post('/api/bank-accounts', json={'property_id': prop.json()['id'], 'account_type': 'LAUFEND', 'label': 'Reserve erasure bank'})
        assert bank.status_code == 201, bank.text
        scope = await rent_db.scalar(text("""INSERT INTO accounting_scope(organization_id,property_id,scope_type,
            label,valid_from,whole_property,origin,created_by) VALUES (:org,:property,'WEG',
            'Reserve erasure scope','2025-01-01',true,'REVIEWED',:admin) RETURNING id"""), {**w, 'property': prop.json()['id']})
        await rent_db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:admin,ARRAY['read','prepare','confirm','approve','export'],:admin,'Reviewed reserve erasure scope')"""),
            {**w, 'scope': scope})
        await rent_db.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
            bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:bank,
            'REVIEWED','Scoped reserve bank',:admin)"""),
            {**w, 'property': prop.json()['id'], 'scope': scope, 'bank': bank.json()['id']})
        await rent_db.commit()
        world = {'bank_giro': bank.json()['id']}
        transaction, released = await imported_reserve_draft(api, world)
        response = await api.delete(f'/api/payments/{released}')
        assert response.status_code == 204, response.text
        retained = await api.get(f'/api/payments/{released}')
        assert retained.status_code == 200, retained.text
        assert retained.json()['status'] == 'cancelled'
        assigned = await api.post(f'/api/bank-transactions/{transaction}/assign', json={'lines': [{
            'payment_type': 'RUECKLAGENENTNAHME', 'amount': '1200.00', 'period_year': 2025, 'period_month': 2}]})
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()['payments'][0]['status'] == 'draft'
        assert await rent_db.scalar(text('SELECT count(*) FROM payment WHERE organization_id=:org'), w) == 2
        await rent_db.rollback()
        # Probe the operator-only guard independently of retention selection.
        # Scope bindings remain retained, so roll back before deferred FK checks.
        async with rent_db.begin_nested() as probe:
            await rent_db.execute(text("SET LOCAL app.organization_erasure='on'"))
            await rent_db.execute(text('DELETE FROM payment WHERE organization_id=:org'), w)
            assert await rent_db.scalar(text('SELECT count(*) FROM payment WHERE organization_id=:org'), w) == 0
            await probe.rollback()
        await rent_db.rollback()
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    # Immutable scope bindings retain these drafts through the retention plan's
    # FK closure; guard permission does not override that separate decision.
    assert await rent_db.scalar(text('SELECT count(*) FROM payment WHERE organization_id=:org'), w) == 2
    await rent_db.rollback()


async def test_draft_dunning_dependencies_erase_person_text(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    marker = 'PrivateDunningPerson'
    for table in ('person', 'tenant_profile', 'lease_agreement'):
        column = 'first_name' if table == 'person' else 'notes'
        await rent_db.execute(text(f'UPDATE {table} SET {column}=:marker WHERE organization_id=:org'),
                              {**w, 'marker': marker})
    scope = await rent_db.scalar(text('''INSERT INTO accounting_scope(organization_id,property_id,scope_type,
      label,valid_from,whole_property,origin,created_by) VALUES (:org,:property,'MIET',
      'Rental scope','2026-01-01',true,'REVIEWED',:admin) RETURNING id'''), w)
    notice = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,
      lease_agreement_id,period_start,period_end,created_by,reason) VALUES (:org,:notice,:scope,:lease,
      '2026-01-01','2026-12-31',:admin,'Reviewed rental dunning')'''), {**w, 'notice': notice, 'scope': scope})
    await rent_db.execute(text('''INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,level,
      issue_date,due_date,principal_amount,fee_amount,total_amount,status)
      VALUES (:notice,:org,:tenant,1,'2026-01-01','2026-02-01',99,0,99,'draft')'''), {**w, 'notice': notice})
    await rent_db.commit()
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    try:
        assert await rent_db.scalar(text('SELECT anonymized_at FROM person WHERE id=:person'), w)
        for table, key in (('lease_agreement', 'lease'), ('tenant_profile', 'tenant')):
            assert await rent_db.scalar(text(f'SELECT notes FROM {table} WHERE id=:id'), {'id': w[key]}) == 'Gelöscht'
        assert await rent_db.scalar(text('SELECT count(*) FROM dunning_scope_context WHERE dunning_notice_id=:notice'), {'notice': notice}) == 1
        await assert_no_person_marker(rent_db, w['org'], marker)
    finally:
        await rent_db.rollback()


async def test_registered_resolution_scrubs_retained_meeting_and_agenda_text(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    private = 'Hans-Jürgen Müller-Lüdenscheidt / DE89 3704 0044 0532 0130 00'
    async with api_client(w) as api:
        prop = await api.post('/api/properties', json={'name': 'Meeting erasure', 'management_type': 'WEG'})
        assert prop.status_code == 201, prop.text
        meeting = await api.post('/api/meetings', json={'property_id': prop.json()['id'],
            'meeting_date': '2026-03-15', 'meeting_type': 'ORDENTLICH', 'location': 'Synthetic hall', 'invitation_text': private})
        assert meeting.status_code == 201, meeting.text
        mid = meeting.json()['id']
        assert (await api.put(f'/api/meetings/{mid}', json={'protocol_text': private})).status_code == 200
        agenda = await api.post(f'/api/meetings/{mid}/agenda', json={'meeting_id': mid, 'title': private, 'description': private})
        assert agenda.status_code == 201, agenda.text
        assert (await api.put(f"/api/meetings/agenda/{agenda.json()['id']}",
                              json={'title': private, 'description': private})).status_code == 200
        resolution = await api.post('/api/meetings/resolutions', json={'meeting_id': mid,
            'agenda_item_id': agenda.json()['id'], 'wording': 'Retained legal wording',
            'votes_yes': 5, 'votes_no': 0, 'votes_abstain': 0, 'result': 'ANGENOMMEN'})
        assert resolution.status_code == 201, resolution.text
        entry = await api.post(f"/api/meetings/resolutions/{resolution.json()['id']}/register")
        assert entry.status_code == 201, entry.text
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    meeting_text = (await rent_db.execute(text('SELECT protocol_text,invitation_text FROM owners_meeting WHERE id=:id'), {'id': mid})).one()
    agenda_text = (await rent_db.execute(text('SELECT title,description FROM agenda_item WHERE id=:id'), {'id': agenda.json()['id']})).one()
    wording = await rent_db.scalar(text('SELECT wording FROM resolution_register_entry WHERE id=:id'), {'id': entry.json()['id']})
    audit_payloads = (await rent_db.execute(text('SELECT payload_diff FROM audit_event WHERE organization_id=:org'), w)).scalars().all()
    await rent_db.rollback()
    assert meeting_text == ('Gelöscht', 'Gelöscht')
    assert agenda_text == ('Gelöscht', 'Gelöscht')
    assert wording == 'Retained legal wording'
    assert private not in json.dumps(audit_payloads, ensure_ascii=False)


async def test_retained_text_inventory(rent_db):
    from pathlib import Path
    import re
    # Include every possible FK-retained parent, not just immutable roots.
    # All textual/JSON columns participate, including titles, text and reasons.
    rows = (await rent_db.execute(text('''WITH RECURSIVE retained(entity) AS (
      SELECT entity COLLATE "C" FROM organization_retention_catalog() WHERE disposition IN ('retain','conditional','anonymize')
      UNION SELECT parent.relname::text FROM retained r
        JOIN pg_constraint fk ON fk.conrelid=to_regclass('public.'||r.entity) AND fk.contype='f'
        JOIN pg_class parent ON parent.oid=fk.confrelid
        WHERE parent.relnamespace='public'::regnamespace AND EXISTS(SELECT 1 FROM pg_attribute a
          WHERE a.attrelid=parent.oid AND a.attname='organization_id' AND NOT a.attisdropped)
      ) SELECT r.entity,a.attname
      FROM retained r JOIN pg_class c ON c.oid=to_regclass('public.'||r.entity)
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
      JOIN pg_type typ ON typ.oid=a.atttypid
      WHERE typ.typname IN ('text','varchar','bpchar','json','jsonb')
        OR typ.typelem IN ('text'::regtype,'varchar'::regtype,'bpchar'::regtype)
      ORDER BY r.entity,a.attname'''))).all()
    scrubbed = {(table, column) for table, columns in ERASURE_SCRUB_COLUMNS.items() for column in columns}
    scrubbed |= set((await rent_db.execute(text('''SELECT r.entity,unnest(organization_erasure_payload_columns(r.entity))
      FROM organization_retention_catalog() r'''))).all())
    inventory = (Path(__file__).resolve().parents[2] / 'docs/operations/organization-export.md').read_text()
    inventory = inventory.split('### Catalog survivor contract\n', 1)[-1].split('\n### ', 1)[0]
    survivors = set()
    for line in inventory.splitlines():
        if line.startswith('| `'):
            cells = line.split('|')
            tables, columns = re.findall(r'`([^`]+)`', cells[1]), re.findall(r'`([^`]+)`', cells[2])
            survivors.update((table, column) for table in tables for column in columns)
    missing = sorted(set(rows) - scrubbed - survivors)
    assert not missing, '\n'.join(f'{table}.{column}' for table, column in missing)


async def test_every_organization_table_has_lifecycle_trigger(rent_db):
    missing = (await rent_db.execute(text('''SELECT c.relname FROM pg_class c
      WHERE c.relnamespace='public'::regnamespace AND c.relkind='r'
      AND (c.relname='organization' OR EXISTS(SELECT 1 FROM pg_attribute a
        WHERE a.attrelid=c.oid AND a.attname='organization_id' AND NOT a.attisdropped))
      AND NOT EXISTS(SELECT 1 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid=c.oid AND t.tgname='a_organization_lifecycle'
          AND p.proname='organization_lifecycle_guard' AND t.tgenabled='O' AND t.tgtype=31)
      ORDER BY c.relname'''))).scalars().all()
    assert missing == []


@pytest.mark.parametrize('change', ["execute_after=now()-interval '1 day'", "status='deleted_pending_retention'"])
async def test_runtime_cannot_accelerate_or_complete_deletion(rent_world, rent_db, export_store, change):
    from sqlalchemy.exc import DBAPIError
    w = rent_world
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    await rent_db.execute(text('SET LOCAL SESSION AUTHORIZATION app_rw'))
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"),
                          {'org': str(w['org']), 'actor': str(w['admin'])})
    try:
        with pytest.raises(DBAPIError):
            async with rent_db.begin_nested():
                result = await rent_db.execute(text(f'UPDATE organization_deletion SET {change} WHERE organization_id=:org'), w)
                assert result.rowcount == 1
    finally:
        await rent_db.rollback()


async def test_owner_cancel_preserves_confirmation_and_deadline(rent_world, rent_db, export_store):
    w = rent_world
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
        before = (await rent_db.execute(text('SELECT to_jsonb(d)-\'status\' FROM organization_deletion d WHERE organization_id=:org'), w)).scalar_one()
        await rent_db.rollback()
        assert (await api.post('/api/organizations/deletion/cancel')).status_code == 200
        after = (await rent_db.execute(text('SELECT to_jsonb(d)-\'status\' FROM organization_deletion d WHERE organization_id=:org'), w)).scalar_one()
        await rent_db.rollback()
        assert after == before


async def test_retained_event_payload_inventory(rent_db):
    rows = (await rent_db.execute(text('''SELECT r.entity,a.attname,typ.typname,
      (SELECT string_agg(DISTINCT p.proname,',' ORDER BY p.proname) FROM pg_trigger t
        JOIN pg_proc p ON p.oid=t.tgfoid WHERE t.tgrelid=c.oid AND NOT t.tgisinternal
        AND (t.tgtype & 16)>0) FROM organization_retention_catalog() r
      JOIN pg_class c ON c.oid=to_regclass('public.'||r.entity)
      JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
      JOIN pg_type typ ON typ.oid=a.atttypid
      WHERE r.disposition='retain' AND r.entity ~ '(event|receipt|batch|attempt)$'
      AND typ.typname IN ('json','jsonb','text','varchar')
      ORDER BY r.entity,a.attname'''))).all()
    technical = {'action', 'entity_type', 'prev_event_hash', 'operation', 'request_digest',
                 'resolution_state', 'workflow_state', 'error_code', 'processor_version',
                 'status', 'key', 'effect_key', 'event_type', 'mode', 'kind'}
    content = {(table, column) for table, column, *_ in rows if column not in technical}
    covered = set((await rent_db.execute(text('''SELECT r.entity,unnest(organization_erasure_payload_columns(r.entity))
      FROM organization_retention_catalog() r'''))).all())
    assert content == covered


@pytest.mark.parametrize('case', ['historical_import_key', 'bound_import_key', 'mail_metadata'])
async def test_payload_scrub_preserves_typed_person_bindings_and_mail_metadata(case):
    from app.services.organization_deletion_service import payload_scrubber
    erased, bound = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    scrub = payload_scrubber([{'id': erased, 'first_name': 'prepared', 'last_name': 'a' * 64}], [bound], [], now)
    if case == 'historical_import_key':
        payload = {'kind': 'person', 'id': erased, 'key': 'historical-private@example.invalid'}
        result = scrub(payload)
        assert result['id'] == result['key'] == 'Gelöscht'
    elif case == 'bound_import_key':
        payload = {'kind': 'person', 'id': bound, 'key': 'prepared'}
        assert scrub(payload) == payload
    else:
        payload = {'person_id': erased, 'first_name': 'prepared', 'status': 'prepared',
                   'content_sha256': 'a' * 64, 'id': str(uuid.uuid4()), 'bytes': 42,
                   'storage_uri': 'prepared/' + 'a' * 64, 'created_at': now.isoformat()}
        result = scrub(payload)
        assert result['person_id'] == result['first_name'] == 'Gelöscht'
        assert {key: result[key] for key in payload if key not in {'person_id', 'first_name'}} == {
            key: value for key, value in payload.items() if key not in {'person_id', 'first_name'}}


ERASED_PERSON = {'id': '57a02ec5-d33a-43f1-98f9-7e221da8d220', 'first_name': 'A',
                 'last_name': 'Müller', 'email': 'private+test@example.invalid',
                 'iban': 'DE89370400440532013000', 'phone': '+493012345678',
                 'street': 'Hauptstraße 1', 'zip_code': '12345', 'city': 'Berlin'}


@pytest.mark.parametrize('value', [
    '12345.00', 'a short note', 'Berlin', 'Hauptstraße 1', '2026-01-23',
    'A', 'Müller', 'a@example.invalid', 'XDE89370400440532013000X',
    '157a02ec5d33a43f198f97e221da8d2200', '+4930123456789',
])
def test_payload_scrub_preserves_nonpersonal_values_and_identifier_neighbors(value):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    payload = {'amount': value, 'property': {'street': value, 'city': value}, 'status': value}
    assert scrub(payload) == payload


@pytest.mark.parametrize('value', [
    '  a   MÜLLER  ', 'PRIVATE+TEST@EXAMPLE.INVALID',
    'private%2Btest%40example.invalid', 'DE89 3704 0044 0532 0130 00',
    '57A02EC5D33A43F198F97E221DA8D220', '+49 (30) 1234-5678',
])
def test_payload_scrub_normalizes_whole_identifying_values(value):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    assert scrub({'value': value})['value'] == 'Gelöscht'


@pytest.mark.parametrize('value', [
    'private+test@example.invalid', 'private%2Btest%40example.invalid',
    'DE89 3704 0044 0532 0130 00', '57a02ec5-d33a-43f1-98f9-7e221da8d220',
    '57a02ec5d33a43f198f97e221da8d220', '+49 (30) 1234-5678',
])
def test_payload_scrub_replaces_only_identifier_tokens_inside_text(value):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    assert scrub('Contact: ' + value + '; a short note in Berlin, 12345.00') == (
        'Contact: Gelöscht; a short note in Berlin, 12345.00')


@pytest.mark.parametrize('phone,value', [
    ('0', '0.00'), ('0', '0'), ('000', '0'), ('-', '-'), ('-', '/'), ('( )', '.'),
    ('0000', '00.00'), ('1234', '12.34'), ('1234', '1234'), ('1234', '12/34'), ('12 34 56', '123456'),
])
def test_payload_scrub_ignores_placeholder_phones_shorter_than_seven_digits(phone, value):
    from app.services.organization_deletion_service import payload_scrubber
    person = dict(ERASED_PERSON, phone=phone)
    scrub = payload_scrubber([person], [], [], datetime.now(timezone.utc))
    assert scrub({'fee_amount': value, 'note': 'Betrag ' + value})['fee_amount'] == value
    assert scrub({'fee_amount': value, 'note': 'Betrag ' + value})['note'] == 'Betrag ' + value


@pytest.mark.parametrize('field', ['id', 'request_id', 'message_id', 'counts'])
def test_payload_scrub_walks_metadata_keys_and_values(field):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    identifier = ERASED_PERSON['id']
    payload = {field: {identifier: 2} if field == 'counts' else {'id': identifier}}
    result = scrub(payload)
    assert identifier not in json.dumps(result)
    if field == 'counts':
        assert result['counts']['Gelöscht'] == 2


@pytest.mark.parametrize('value', [ERASED_PERSON['email'], 'A Müller'])
def test_bound_person_context_still_scrubs_other_erased_identities(value):
    from app.services.organization_deletion_service import payload_scrubber
    bound = str(uuid.uuid4())
    scrub = payload_scrubber([ERASED_PERSON], [bound], [], datetime.now(timezone.utc))
    result = scrub({'person_id': bound, 'name': value}, bound)
    assert result['person_id'] == bound
    assert result['name'] == 'Gelöscht'


def test_payload_scrub_walks_keys_even_inside_structured_personal_fields():
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    result = scrub({'person_id': ERASED_PERSON['id'], 'address': {ERASED_PERSON['id']: 'private'}})
    assert ERASED_PERSON['id'] not in json.dumps(result)


def test_payload_scrub_preserves_a_nested_property_address_in_a_person_event():
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    property_record = {'kind': 'property', 'id': str(uuid.uuid4()),
                       'street': 'Hauptstraße 1', 'city': 'Berlin', 'zip': '12345'}
    result = scrub({'person_id': ERASED_PERSON['id'], 'property': property_record})
    assert result['property'] == property_record


@pytest.mark.parametrize('value', [
    'private%2Btest%40example.invalid', 'DE89%203704%200044%200532%200130%2000',
    '57a02ec5d33a43f198f97e221da8d220', '%2B49%20%2830%29%201234-5678',
])
def test_payload_scrub_uses_decoded_boundaries_and_preserves_unrelated_encoding(value):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    assert scrub('Contact%3A%20' + value + '%3B%20a%20short%20note') == (
        'Contact%3A%20Gelöscht%3B%20a%20short%20note')


@pytest.mark.parametrize('kind', [{'old': 'person', 'new': 'company'}, ['person']])
def test_payload_scrub_accepts_structured_kind_metadata(kind):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([ERASED_PERSON], [], [], datetime.now(timezone.utc))
    result = scrub({'kind': kind, 'email': ERASED_PERSON['email']})
    assert result['kind'] == kind
    assert result['email'] == 'Gelöscht'


@pytest.mark.parametrize('entity,column', [
    (entity, column)
    for entity, columns in ERASURE_SCRUB_COLUMNS.items()
    for column in columns
])
def test_payload_scrub_structurally_neutralizes_every_entity_scrub_column(entity, column):
    from app.services.organization_deletion_service import payload_scrubber
    scrub = payload_scrubber([], [], [], datetime.now(timezone.utc))
    payload = {'changes': [{column: {'old': 'Arbitrary private text', 'new': 'Other private text'}}]}
    result = scrub(payload, entity_type=entity)
    assert result['changes'][0][column] == {'old': 'Gelöscht', 'new': 'Gelöscht'}
    assert scrub(payload, entity_type='property') == payload


async def test_runtime_budget_review_completes_with_lifecycle_guard(rent_world, rent_db, export_store):
    from sqlalchemy import event
    from app.core.db import get_review_session_factory
    # Bound the reproduced self-wait at the second connection, not at 15 minutes.
    engine = get_review_session_factory().kw['bind']
    def bounded_wait(connection, *_):
        connection.cursor().execute("SET lock_timeout='1s'")
    event.listen(engine.sync_engine, 'connect', bounded_wait)
    try:
        assert await rent_db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='a_organization_lifecycle'")) > 0
        await rent_db.rollback()
        async with api_client(rent_world) as api:
            state = (await api.get('/api/access-preparation/runtime-budget')).json()
            reviewed = await asyncio.wait_for(api.post('/api/access-preparation/runtime-budget/review',
                json={'expected_review_id': None, 'policy_digest': state['policy_digest'],
                      'reason': 'Synthetic configured budget review', 'acknowledged': True},
                headers={'Idempotency-Key': str(uuid.uuid4())}), timeout=5)
            assert reviewed.status_code == 201, reviewed.text
    finally:
        event.remove(engine.sync_engine, 'connect', bounded_wait)


async def test_me_reads_lifecycle_without_import_admission(rent_world, rent_db, export_store):
    # An independent ACL writer must not stall a banner read.
    await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {'key': f"scope-acl:{rent_world['org']}"})
    try:
        async with api_client(rent_world) as api:
            answer = await asyncio.wait_for(api.get('/api/organizations/me'), timeout=2)
            assert answer.status_code == 200, answer.text
    finally:
        await rent_db.rollback()


async def test_retention_catalog_classifies_every_organization_table(rent_db):
    guards = (await rent_db.execute(text('''SELECT DISTINCT p.proname FROM pg_trigger t
      JOIN pg_proc p ON p.oid=t.tgfoid WHERE NOT t.tgisinternal AND (t.tgtype & 8)>0
      ORDER BY p.proname'''))).scalars().all()
    assert await rent_db.scalar(text("SELECT to_regprocedure('organization_retention_catalog()')")), guards
    tables = set((await rent_db.execute(text('''SELECT c.relname FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r'
      AND EXISTS(SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid
        AND a.attname='organization_id' AND NOT a.attisdropped)'''))).scalars())
    classified = (await rent_db.execute(text('SELECT * FROM organization_retention_catalog()'))).mappings().all()
    assert {row['entity'] for row in classified} == tables
    assert all(row['disposition'] in {'retain', 'conditional', 'delete', 'lifecycle', 'anonymize'} for row in classified)
    by_name = {row['entity']: row['disposition'] for row in classified}
    for name in ('person_data_domain', 'person_data_grant', 'property_data_domain', 'property_data_grant',
                 'dunning_scope_context', 'property_source_admission', 'special_levy', 'special_levy_allocation',
                 'reserve_ledger_entry'):
        assert by_name[name] == 'retain', name
    assert by_name['sepa_mandate'] == 'delete'
    await rent_db.execute(text('CREATE TRIGGER immutable_probe BEFORE DELETE ON sepa_mandate FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence()'))
    assert await rent_db.scalar(text("SELECT disposition FROM organization_retention_catalog() WHERE entity='sepa_mandate'")) == 'retain'
    # A new table is discovered without updating a hand-maintained table list.
    # A missing identity cannot silently drop its rows from the retention plan.
    await rent_db.execute(text('CREATE TABLE public.retention_contract_probe(organization_id uuid, id uuid PRIMARY KEY)'))
    await rent_db.execute(text('GRANT SELECT,DELETE ON public.retention_contract_probe TO app_rw'))
    assert await rent_db.scalar(text("SELECT disposition FROM organization_retention_catalog() WHERE entity='retention_contract_probe'")) == 'delete'
    await rent_db.execute(text('CREATE TRIGGER immutable_probe BEFORE DELETE ON public.retention_contract_probe FOR EACH ROW EXECUTE FUNCTION protect_scope_evidence()'))
    assert await rent_db.scalar(text("SELECT disposition FROM organization_retention_catalog() WHERE entity='retention_contract_probe'")) == 'retain'
    await rent_db.execute(text('ALTER TABLE public.retention_contract_probe DROP CONSTRAINT retention_contract_probe_pkey'))
    assert await rent_db.scalar(text("SELECT disposition FROM organization_retention_catalog() WHERE entity='retention_contract_probe'")) == 'unclassified'
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError, match='Unclassified organization retention table'):
        await rent_db.execute(text('SELECT * FROM organization_retention_plan(:org)'), {'org': uuid.uuid4()})
    await rent_db.rollback()


async def test_api_domains_survive_due_erasure_unchanged(rent_world, rent_db, export_store):
    from tests.test_person_data_domains import domain_fixture as person_domain
    from tests.test_property_data_domains import domain_fixture as property_domain
    from app.services.organization_deletion_service import run_deletion
    from app.services.organization_archive import json_bytes
    import hashlib
    w = rent_world
    async with api_client(w) as api:
        body, _, domain = await person_domain(api)
        person_id = uuid.UUID(body['person_id'])
        await rent_db.execute(text('''UPDATE person SET first_name='Erika',last_name='PrivateDomainMarker',
          email='erika.marker@example.invalid',iban='DE89370400440532013000',street='Geheimweg 1'
          WHERE id=:person'''), {'person': person_id})
        await rent_db.commit()
        granted = await api.post(f"/api/person-data-domains/{domain['id']}/grants",
            json={'user_id': str(w['clerk']), 'capabilities': ['read', 'prepare'], 'reason_code': 'TEAM_ACCESS'},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert granted.status_code == 201, granted.text
        _, property_id = await property_domain(api)
        granted = await api.post(f'/api/property-data-domains/{property_id}/grants',
            json={'user_id': str(w['clerk']), 'capabilities': ['read', 'prepare'], 'reason': 'Synthetic reviewed audience'},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert granted.status_code == 201, granted.text
        kinds = ('person_data_domain', 'person_data_grant', 'property_data_domain', 'property_data_grant')
        async def hashes():
            return {kind: hashlib.sha256(json_bytes((await rent_db.execute(text(
                f'SELECT to_jsonb(t) FROM {kind} t WHERE organization_id=:org ORDER BY id'), w)).scalars().all())).hexdigest() for kind in kinds}
        before = await hashes()
        await rent_db.rollback()
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert await hashes() == before
    person = (await rent_db.execute(text('''SELECT first_name,last_name,email,iban,street,anonymized_at
      FROM person WHERE id=:person'''), {'person': person_id})).one()
    await rent_db.rollback()
    assert person[:5] == ('', 'Anonymisiert', None, None, '')
    assert person.anonymized_at is not None
    async with api_client(w) as api:
        assert (await api.get('/api/auth/session')).status_code == 401
    await rent_db.rollback()


@pytest.mark.parametrize('mode', ['PREPARATION', 'ENFORCED'])
async def test_due_erasure_with_computed_drafts_bound_finance_and_dunning(rent_world, rent_db, export_store, mode):
    from tests.test_financial_input_policy import attest
    from tests.test_source_security_lifecycle import status, transition, transition_body
    from tests.test_access_disposition import reviewed, assessment, decisions, acceptance_body
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    async with api_client(w) as api:
        property_response = await api.post('/api/properties', json={'name': 'Scoped plan property', 'management_type': 'WEG'})
        assert property_response.status_code == 201, property_response.text
        w = {**w, 'plan_property': uuid.UUID(property_response.json()['id'])}
        domain = await api.post('/api/property-data-domains',
            json={'property_id': str(w['plan_property']), 'label': 'Plan audience', 'reason': 'Reviewed plan access'},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert domain.status_code == 201, domain.text
        grant = await api.post(f"/api/property-data-domains/{domain.json()['id']}/grants",
            json={'user_id': str(w['admin']), 'capabilities': ['read', 'prepare', 'export'], 'reason': 'Reviewed plan reader'},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert grant.status_code == 201, grant.text
        for property_key in ('property', 'hidden_property'):
            domain = await api.post('/api/property-data-domains',
                json={'property_id': str(w[property_key]), 'label': 'Rental audience', 'reason': 'Reviewed rental access'},
                headers={'Idempotency-Key': str(uuid.uuid4())})
            assert domain.status_code == 201, domain.text
            grant = await api.post(f"/api/property-data-domains/{domain.json()['id']}/grants",
                json={'user_id': str(w['admin']), 'capabilities': ['read', 'prepare', 'export'], 'reason': 'Reviewed rental reader'},
                headers={'Idempotency-Key': str(uuid.uuid4())})
            assert grant.status_code == 201, grant.text
        for person_key in ('person', 'hidden_person'):
            domain = await api.post('/api/person-data-domains',
                json={'person_id': str(w[person_key]), 'reason_code': 'INITIAL_SETUP'},
                headers={'Idempotency-Key': str(uuid.uuid4())})
            assert domain.status_code == 201, domain.text
            grant = await api.post(f"/api/person-data-domains/{domain.json()['id']}/grants",
                json={'user_id': str(w['admin']), 'capabilities': ['read', 'prepare', 'export'], 'reason_code': 'TEAM_ACCESS'},
                headers={'Idempotency-Key': str(uuid.uuid4())})
            assert grant.status_code == 201, grant.text
        created = await api.post('/api/settlements', json={
            'property_id': str(w['property']), 'lease_agreement_id': str(w['lease']),
            'period_start': '2026-01-01', 'period_end': '2026-12-31', 'advance_payments_total': '0.00'})
        assert created.status_code == 201, created.text
        run = uuid.UUID(created.json()['id'])
        coverage = await attest(api, 'settlement', str(run),
            {'advance_payments_total': '0.00', 'advance_payments_source': 'MANUAL'})
        assert coverage.status_code == 201, coverage.text
        computed = await api.post(f'/api/settlements/{run}/compute', json={})
        assert computed.status_code == 200, computed.text
    plan = await rent_db.scalar(text('''INSERT INTO economic_plan(organization_id,property_id,plan_year,
      period_start,period_end,status,engine_version,snapshot_payload,snapshot_sha256) VALUES (:org,:plan_property,2026,
      '2026-01-01','2026-12-31','draft','test','{}',repeat('a',64)) RETURNING id'''), w)
    await rent_db.execute(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,label,planned_amount) VALUES (:org,:plan,'SUBTOTAL','Draft personal label',99)"), {**w, 'plan': plan})
    scope = await rent_db.scalar(text('''INSERT INTO accounting_scope(organization_id,property_id,scope_type,
      label,valid_from,whole_property,origin,created_by) VALUES (:org,:plan_property,'WEG',
      'Retained configuration','2026-01-01',true,'REVIEWED',:admin) RETURNING id'''), w)
    await rent_db.execute(text('''INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
      economic_plan_id,origin,reason,reviewed_by) VALUES (:org,:plan_property,:scope,:plan,
      'REVIEWED','Historical financial binding',:admin)'''), {**w, 'scope': scope, 'plan': plan})
    await rent_db.execute(text('''INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
      VALUES (:org,:scope,:admin,ARRAY['read','prepare','confirm','approve','export'],:admin,'Reviewed live financial reader')'''),
      {**w, 'scope': scope})
    rental_scope = await rent_db.scalar(text('''INSERT INTO accounting_scope(organization_id,property_id,scope_type,
      label,valid_from,whole_property,origin,created_by) VALUES (:org,:property,'MIET',
      'Rental scope','2026-01-01',true,'REVIEWED',:admin) RETURNING id'''), w)
    await rent_db.execute(text('''INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
      VALUES (:org,:scope,:admin,ARRAY['read','prepare','confirm','approve','send','export'],:admin,'Reviewed rental finance')'''),
      {**w, 'scope': rental_scope})
    await rent_db.execute(text('''INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
      settlement_run_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:run,
      'REVIEWED','Historical settlement binding',:admin)'''), {**w, 'scope': rental_scope, 'run': run})
    bound_notice = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO dunning_scope_context(organization_id,dunning_notice_id,scope_id,
      lease_agreement_id,period_start,period_end,created_by,reason) VALUES (:org,:notice,:scope,:lease,
      '2026-01-01','2026-12-31',:admin,'Reviewed rental dunning')'''), {**w, 'scope': rental_scope, 'notice': bound_notice})
    await rent_db.execute(text('''INSERT INTO dunning_notice(id,organization_id,tenant_profile_id,
      level,issue_date,due_date,principal_amount,fee_amount,total_amount,status)
      VALUES (:notice,:org,:tenant,1,'2026-01-01','2026-02-01',99,0,99,'draft')'''), {**w, 'notice': bound_notice})
    await rent_db.commit()
    assert await rent_db.scalar(text('SELECT count(*) FROM settlement_line WHERE settlement_run_id=:run'), {'run': run}) > 0
    await rent_db.rollback()
    async with api_client(w) as api:
        if mode == 'ENFORCED':
            report = await reviewed(api)
            state = await assessment(api, report)
            assert state['acceptance_available'], json.dumps([
                {'kind': item['kind'], 'disposition': item['disposition'], 'findings': item['findings']}
                for item in await decisions(api, report) if item['disposition'] == 'BLOCKED'])
            response = await api.post(f"/api/access-preparation/snapshots/{report['id']}/disposition/accept",
                json=acceptance_body(report, state), headers={'Idempotency-Key': str(uuid.uuid4())})
            assert response.status_code == 201, response.text
            decision = response.json()
            activated = await transition(api, transition_body(await status(api), 'ENFORCED', decision))
            assert activated.status_code == 201, activated.text
        # A historical unclassified notice can exist after activation. The
        # existing migration-owner fixture path admits it without altering guards.
        notice = await rent_db.scalar(text('''INSERT INTO dunning_notice(organization_id,tenant_profile_id,
          level,issue_date,due_date,principal_amount,fee_amount,total_amount,status)
          VALUES (:org,:tenant,1,'2026-01-01','2026-02-01',99,0,99,'draft') RETURNING id'''), w)
        await rent_db.commit()
        if mode == 'ENFORCED':
            # Isolate each real authorization trigger from target-table RLS:
            # a caller-set flag must not pass a SECURITY DEFINER capability check.
            from sqlalchemy.exc import DBAPIError
            for function, table in [
                ('guard_financial_source_input', 'settlement_line'),
                ('guard_strict_financial_resource', 'economic_plan'),
                ('authorize_bound_financial_write', 'economic_plan'),
                ('authorize_dunning_write', 'dunning_notice'),
            ]:
                await rent_db.execute(text(f'CREATE TEMP TABLE {table} (LIKE public.{table}) ON COMMIT DROP'))
                await rent_db.execute(text(f'INSERT INTO pg_temp.{table} SELECT * FROM public.{table} WHERE organization_id=:org'), w)
                assert await rent_db.scalar(text(f'SELECT count(*) FROM pg_temp.{table}')) > 0
                await rent_db.execute(text(f'GRANT SELECT,DELETE ON pg_temp.{table} TO app_rw'))
                await rent_db.execute(text(f'CREATE TRIGGER authorization_probe BEFORE DELETE ON pg_temp.{table} FOR EACH ROW EXECUTE FUNCTION public.{function}()'))
                await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id','',true),set_config('app.organization_erasure','on',true)"), {'org': str(w['org'])})
                await rent_db.execute(text('SET LOCAL SESSION AUTHORIZATION app_rw'))
                try:
                    with pytest.raises(DBAPIError) as refused:
                        async with rent_db.begin_nested():
                            await rent_db.execute(text(f'DELETE FROM pg_temp.{table}'))
                    assert refused.value.orig.sqlstate == '42501', function
                finally:
                    await rent_db.execute(text('SET LOCAL SESSION AUTHORIZATION DEFAULT'))
                await rent_db.execute(text(f'DROP TABLE pg_temp.{table}'))
            await rent_db.rollback()
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await rent_db.scalar(text("SELECT COALESCE(NULLIF(current_setting('app.current_user_id',true),''),'')")) == ''
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert await rent_db.scalar(text('SELECT status FROM organization_deletion WHERE organization_id=:org'), w) == 'deleted_pending_retention'
    assert await rent_db.scalar(text('SELECT count(*) FROM settlement_line WHERE settlement_run_id=:run'), {'run': run}) == 0
    assert await rent_db.scalar(text('SELECT count(*) FROM economic_plan_line WHERE economic_plan_id=:plan'), {'plan': plan}) == 0
    assert await rent_db.scalar(text('SELECT count(*) FROM dunning_notice WHERE id=:notice'), {'notice': notice}) == 0
    assert await rent_db.scalar(text('SELECT count(*) FROM dunning_notice WHERE id=:notice'), {'notice': bound_notice}) == 1
    if mode == 'ENFORCED':
        assert await rent_db.scalar(text('SELECT reason FROM property_source_security WHERE organization_id=:org'), w) == 'Gelöscht'
    assert await rent_db.scalar(text("SELECT COALESCE(current_setting('app.organization_erasure',true),'')")) != 'on'
    await rent_db.rollback()


async def test_erasure_scrubs_mutable_retained_text_and_keeps_immutable_fields(rent_world, rent_db, export_store):
    from tests.test_letters import create_template
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    marker = 'PrivateConfigurationMarker'
    async with api_client(w) as api:
        for _ in range(2):
            await create_template(api, name=marker, subject=marker, body=marker)
    job = await rent_db.scalar(text('''INSERT INTO declaration_import(organization_id,mode,target_property_id,
      status,initiated_by,max_cost_usd) VALUES (:org,'EXISTING_PROPERTY',:property,'DRAFT',:admin,1) RETURNING id'''), w)
    assignment = await rent_db.scalar(text('''INSERT INTO property_assignment(organization_id,property_id,stable_key,
      assignment_type,designation,location,description,details_json,source_import_id)
      VALUES (:org,:property,'synthetic-assignment','OTHER',CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text),
      jsonb_build_object('personal',CAST(:marker AS text)),:job) RETURNING id'''), {**w, 'job': job, 'marker': marker})
    params = {**w, 'job': job, 'marker': marker, 'assignment': assignment}
    await rent_db.execute(text('''INSERT INTO property_legal_reference(organization_id,property_id,reference_type,
      district,cadastral_district,field_number,parcel,sheet,details_json,source_import_id) VALUES (:org,:property,CAST(:marker AS text),
      CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text),
      jsonb_build_object('personal',CAST(:marker AS text)),:job)'''), params)
    rule = await rent_db.scalar(text('''INSERT INTO property_rule(organization_id,property_id,stable_key,topic,title,
      summary,scope_json,effect_json,source_import_id,approved_by,approved_at,search_text)
      VALUES (:org,:property,'synthetic-rule','OTHER',CAST(:marker AS text),CAST(:marker AS text),
      '{}','{}',:job,:admin,now(),CAST(:marker AS text)) RETURNING id'''), params)
    import hashlib
    original = b'%PDF-1.4 synthetic retained declaration'
    stored = export_store.put(f"{w['org']}/declaration.pdf", original, content_type='application/pdf')
    document = await rent_db.scalar(text('''INSERT INTO property_document(organization_id,property_id,doc_type,
      original_filename,mime_type,content_byte_size,content_sha256,storage_uri,storage_backend,page_count,uploaded_by,deed_reference)
      VALUES (:org,:property,'DECLARATION','synthetic.pdf','application/pdf',:size,:digest,:uri,'LOCAL_FS',1,:admin,:marker) RETURNING id'''),
      {**params, 'size': len(original), 'digest': hashlib.sha256(original).hexdigest(), 'uri': stored.uri})
    params.update(rule=rule, document=document)
    await rent_db.execute(text('''INSERT INTO property_rule_source(organization_id,property_id,property_rule_id,
      property_document_id,pdf_page,printed_page,section,quote,field_path) VALUES (:org,:property,:rule,:document,1,
      CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text),CAST(:marker AS text))'''), params)
    await rent_db.execute(text('''INSERT INTO property_rule_source(organization_id,property_id,property_rule_id,
      property_document_id,pdf_page,quote,field_path) VALUES (:org,:property,:rule,:document,1,
      CAST(:marker AS text),CAST(:marker AS text)||'-second-field')'''), params)
    await rent_db.execute(text('''INSERT INTO approved_fact_source(organization_id,property_id,subject_kind,subject_id,
      field_key,approved_value_json,value_fingerprint,valid_from,property_document_id,pdf_page,printed_page,section,
      quote,source_import_id,search_text) VALUES (:org,:property,'ASSIGNMENT',:assignment,'description',
      to_jsonb(CAST(:marker AS text)),repeat('a',64),now(),:document,1,CAST(:marker AS text),CAST(:marker AS text),
      CAST(:marker AS text),:job,CAST(:marker AS text))'''), params)
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.current_user_id',:actor,true)"), {'org': str(w['org']), 'actor': str(w['admin'])})
    await rent_db.execute(text('''INSERT INTO knowledge_budget_policy(organization_id,max_cost_usd,
      window_minutes,reservation_seconds,changed_by,reason) VALUES (:org,1,60,120,:admin,:marker)'''), {**w, 'marker': marker})
    await rent_db.commit()
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    scrubbed = {}
    for table, columns in [('letter_template', 'name,subject,body'),
                           ('property_assignment', 'designation,location,description,details_json'),
                           ('property_legal_reference', 'reference_type,district,cadastral_district,field_number,parcel,sheet,details_json'),
                           ('property_document', 'deed_reference'),
                           ('property_rule', 'search_text,search_vector'),
                           ('property_rule_source', 'printed_page,section,quote,field_path'),
                           ('approved_fact_source', 'search_text,search_vector'),
                           ('knowledge_budget_policy', 'reason')]:
        rows = (await rent_db.execute(text(f'SELECT {columns} FROM {table} WHERE organization_id=:org'), w)).all()
        scrubbed[table] = rows
    # The administrative receipt retains structure and values except the reason
    # column, which belongs to the same structural scrub set as its entity.
    audit_payloads = (await rent_db.execute(text("SELECT payload_diff FROM audit_event WHERE organization_id=:org AND action='knowledge.budget_policy_changed'"), w)).scalars().all()
    immutable_title = await rent_db.scalar(text('SELECT title FROM property_rule WHERE id=:rule'), params)
    immutable_quote = await rent_db.scalar(text('SELECT quote FROM approved_fact_source WHERE subject_id=:assignment'), params)
    await rent_db.rollback()
    for table, rows in scrubbed.items():
        assert rows and marker not in str(rows), (table, rows)
    assert marker not in str(audit_payloads)
    assert any(payload['max_cost_usd'] == 1 and payload['reason'] == 'Gelöscht' for payload in audit_payloads)
    assert immutable_title == immutable_quote == marker


async def test_cli_continues_after_one_due_organization_fails(rent_world, rent_db, export_store, monkeypatch, caplog):
    from app import organization_deletion as cli
    w = rent_world
    for foreign in (False, True):
        async with api_client(w, foreign=foreign) as api:
            assert (await api.post('/api/organizations/deletion')).status_code == 200
            assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    await rent_db.execute(text("UPDATE organization_deletion SET execute_after=now()-interval '1 day' WHERE organization_id IN (:org,:foreign_org)"), w)
    await rent_db.commit()
    first, second = sorted((w['org'], w['foreign_org']))
    original = cli.run_deletion
    async def fail_first(db, org):
        if org == first:
            await db.execute(text("UPDATE organization SET name='Must roll back' WHERE id=:org"), {'org': org})
            raise RuntimeError('Synthetic organization erasure failure')
        return await original(db, org)
    monkeypatch.setattr(cli, 'run_deletion', fail_first)
    result = await cli.run()
    assert result.completed == 1
    assert result.failed == [first]
    states = dict((await rent_db.execute(text('SELECT organization_id,status FROM organization_deletion WHERE organization_id IN (:org,:foreign_org)'), w)).all())
    assert states == {first: 'cooling_off', second: 'deleted_pending_retention'}
    assert await rent_db.scalar(text('SELECT name FROM organization WHERE id=:org'), {'org': first}) == 'T23 synthetic'
    assert str(first) in caplog.text
    await rent_db.rollback()
    monkeypatch.setattr(cli, 'run_deletion', original)
    result = await cli.run()
    assert result.completed == 1
    assert result.failed == []
    assert await rent_db.scalar(text('SELECT status FROM organization_deletion WHERE organization_id=:org'), {'org': first}) == 'deleted_pending_retention'
    await rent_db.rollback()


def test_cli_reports_failure_with_nonzero_exit_and_organization_ids(monkeypatch, capsys):
    from types import SimpleNamespace
    from app import organization_deletion as cli
    failed = uuid.uuid4()
    async def mixed_result():
        return SimpleNamespace(completed=1, failed=[failed], cleanup_pending=[])
    monkeypatch.setattr(cli, 'run', mixed_result)
    monkeypatch.setattr('sys.argv', ['organization_deletion', 'organization-deletion-run'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert 'Organizations erased: 1' in output.out
    assert str(failed) in output.err


@pytest.mark.parametrize('failure_point', ['after_commit', 'retry'])
async def test_cli_counts_committed_erasure_with_pending_cleanup_once(rent_world, rent_db, export_store, monkeypatch, failure_point):
    from app import organization_deletion as cli
    from app.services import organization_deletion_service as service
    w = rent_world
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    await rent_db.execute(text("UPDATE organization_deletion SET execute_after=now()-interval '1 day' WHERE organization_id=:org"), w)
    await rent_db.commit()
    original = service.erase_unretained_files
    def fail_cleanup(*args):
        raise OSError('Synthetic cleanup failure')
    target = service if failure_point == 'after_commit' else cli
    monkeypatch.setattr(target, 'erase_unretained_files', fail_cleanup)
    result = await cli.run()
    assert result.completed == 1
    assert result.failed == []
    assert result.cleanup_pending == [w['org']]
    assert await rent_db.scalar(text('SELECT status FROM organization_deletion WHERE organization_id=:org'), w) == 'deleted_pending_retention'
    await rent_db.rollback()
    monkeypatch.setattr(target, 'erase_unretained_files', original)
    retried = await cli.run()
    assert retried.completed == 0
    assert retried.failed == retried.cleanup_pending == []


def test_cli_reports_pending_cleanup_with_nonzero_exit(monkeypatch, capsys):
    from app import organization_deletion as cli
    org = uuid.uuid4()
    async def pending_result():
        return cli.ErasureResult(completed=1, cleanup_pending=[org])
    monkeypatch.setattr(cli, 'run', pending_result)
    monkeypatch.setattr('sys.argv', ['organization_deletion', 'organization-deletion-run'])
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    output = capsys.readouterr()
    assert 'Organizations erased: 1' in output.out
    assert 'Organizations failed:' not in output.err
    assert f'Organizations erased, file cleanup pending/retry: {org}' in output.err


async def test_overdue_failed_deletion_can_still_be_cancelled(rent_world, rent_db, export_store):
    async with api_client(rent_world) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
        await rent_db.execute(text("UPDATE organization_deletion SET execute_after=now()-interval '1 day' WHERE organization_id=:org"), rent_world)
        await rent_db.commit()
        answer = await api.post('/api/organizations/deletion/cancel')
        assert answer.status_code == 200, answer.text


async def test_preview_confirmation_readonly_and_cancel(rent_world, rent_db, export_store):
    async with api_client(rent_world) as api:
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body['status'] == 'requested'
        assert body['export_url'] == '/api/organizations/export'
        assert 'Nach der Löschung ist keine Anmeldung mehr möglich' in body['notice']
        assert body['retention']['erased']['person'] == 2
        wrong = await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'wrong'})
        assert wrong.status_code == 422
        confirmed = await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()['status'] == 'cooling_off'
        assert (await api.post('/api/persons', json={'last_name': 'Forbidden'})).status_code == 409
        assert (await api.get('/api/organizations/me')).json()['deletion']['status'] == 'cooling_off'
        # Export is available immediately before and during the cooling-off.
        assert (await api.post('/api/organizations/export')).status_code == 200
        cancelled = await api.post('/api/organizations/deletion/cancel')
        assert cancelled.status_code == 200, cancelled.text
        assert (await api.post('/api/persons', json={'last_name': 'Allowed'})).status_code == 201


async def test_due_run_erases_all_unbound_text_preserves_retention_and_blocks_owner(rent_world, rent_db, export_store):
    w = rent_world
    marker = 'PrivateErasureMarker'
    await rent_db.execute(text('''UPDATE person SET first_name=:marker,email='PrivateErasureMarker@example.invalid',
        street=:marker,iban='DE89370400440532013000' WHERE organization_id=:org'''), {'org': w['org'], 'marker': marker})
    await rent_db.execute(text("UPDATE lease_agreement SET notes=:marker WHERE organization_id=:org"), {'org': w['org'], 'marker': marker})
    await rent_db.execute(text('''INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,action,payload_diff)
      VALUES (:org,:admin,'person',:person,'mail_prepared',jsonb_build_object(
        'person_id',CAST(CAST(:person AS uuid) AS text),'email','PrivateErasureMarker@example.invalid',
        'nested',jsonb_build_array(jsonb_build_object('name',CAST(:marker AS text)))))'''), {**w, 'marker': marker})
    await rent_db.commit()
    orphan = export_store.put(f"{w['org']}/orphan.bin", marker.encode(), content_type='text/plain')
    retained = (await rent_db.execute(text("SELECT id,row_to_json(a)::text FROM audit_event a WHERE organization_id=:org"), w)).all()
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    await rent_db.rollback()
    from app.services.organization_deletion_service import run_deletion
    future = datetime.now(timezone.utc) + timedelta(days=8)
    async with rent_db.begin():
        await run_deletion(rent_db, w['org'], now=future)
    async with api_client(w) as former_owner:
        assert (await former_owner.get('/api/auth/session')).status_code == 401
    assert not export_store.exists(orphan.uri)
    stored = dict((await rent_db.execute(text("SELECT id,row_to_json(a)::text FROM audit_event a WHERE organization_id=:org"), w)).all())
    assert all({k: v for k, v in json.loads(stored[identity]).items() if k != 'payload_diff'} ==
               {k: v for k, v in json.loads(data).items() if k != 'payload_diff'} for identity, data in retained)
    # Scan every text-like column, JSON included, of every organization-owned row.
    state = await rent_db.scalar(text('SELECT retention FROM organization_deletion WHERE organization_id=:org'), w)
    assert state['retained']
    specs = (await rent_db.execute(text('''SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r' AND EXISTS(SELECT 1 FROM pg_attribute a
        WHERE a.attrelid=c.oid AND a.attname='organization_id')'''))).scalars().all()
    for name in specs:
        rows = (await rent_db.execute(text(f'SELECT row_to_json(t)::text FROM "{name}" t WHERE organization_id=:org'), w)).scalars().all()
        retained_ids = {item['id'] for item in state['retained'].get(name, [])}
        for data in rows:
            row = json.loads(data)
            if str(row.get('id')) not in retained_ids:
                assert marker not in data, (name, row)
    assert all(marker.encode() not in path.read_bytes() for path in export_store._root.rglob('*.bin'))
    await assert_no_person_marker(rent_db, w['org'], marker)
    await rent_db.rollback()


async def test_retained_voucher_and_bound_person_are_unchanged(rent_world, rent_db, export_store):
    from app.core.security import hash_password
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:admin'), w)
    await rent_db.execute(text('''UPDATE person SET first_name='Erika',last_name='Retained voucher addressee',
      email='retained@example.invalid',iban='DE89370400440532013000',street='Retained street 1'
      WHERE id=:person'''), w)
    personal_before = (await rent_db.execute(text('SELECT first_name,last_name,email,iban,street FROM person WHERE id=:person'), w)).one()
    await rent_db.execute(text('UPDATE "user" SET password_hash=:password WHERE id=:admin'), {**w, 'password': hash_password('Deletion-Owner-Password-123')})
    vendor = await rent_db.scalar(text('INSERT INTO vendor(organization_id,person_id) VALUES (:org,:person) RETURNING id'), w)
    category = await rent_db.scalar(text('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY code LIMIT 1'))
    await rent_db.execute(text('''INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
      vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
      VALUES (:org,:property,:category,1,:vendor,'2026-01-01','2026-01-01','2026-12-31',99,'UMLAGEFAEHIG','confirmed','MANUAL')'''), {**w, 'vendor': vendor, 'category': category})
    await rent_db.commit()
    before = (await rent_db.execute(text('SELECT row_to_json(t)::text FROM cost_entry t WHERE organization_id=:org'), w)).scalars().all()
    person = await rent_db.scalar(text('SELECT last_name FROM person WHERE id=:person'), w)
    await rent_db.rollback()
    async with api_client(w) as api:
        signed_in = await api.post('/api/auth/login', json={'email': email, 'password': 'Deletion-Owner-Password-123'})
        assert signed_in.status_code == 200, signed_in.text
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['retained']['cost_entry']['count'] == 1
        assert preview.json()['retention']['erased']['person'] == 1
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    after = (await rent_db.execute(text('SELECT row_to_json(t)::text FROM cost_entry t WHERE organization_id=:org'), w)).scalars().all()
    assert before == after
    row = (await rent_db.execute(text('SELECT last_name,processing_restricted FROM person WHERE id=:person'), w)).one()
    assert row == (person, True)
    assert (await rent_db.execute(text('SELECT first_name,last_name,email,iban,street FROM person WHERE id=:person'), w)).one() == personal_before
    async with api_client(w) as api:
        login = await api.post('/api/auth/login', json={'email': email, 'password': 'Deletion-Owner-Password-123'})
        assert login.status_code == 401, login.text
    await rent_db.rollback()


async def test_legacy_finalized_plan_keeps_owner_name_history(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    from app.services.render_names import _owner_names_map
    w = rent_world
    owner = await rent_db.scalar(text("INSERT INTO owner_profile(organization_id,person_id,notes) VALUES (:org,:person,'PrivateOwnerNotes') RETURNING id"), w)
    await rent_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:owner,'2020-01-01')"), {**w, 'owner': owner})
    await rent_db.execute(text('''INSERT INTO economic_plan(organization_id,property_id,plan_year,period_start,period_end,
      reserve_contribution_total,status,engine_version,snapshot_payload,snapshot_sha256)
      VALUES (:org,:property,2026,'2026-01-01','2026-12-31',0,'finalized','legacy','{}',repeat('a',64))'''), w)
    await rent_db.commit()
    before = await _owner_names_map(rent_db, w['org'], w['property'])
    assert before
    await rent_db.rollback()
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    try:
        assert await _owner_names_map(rent_db, w['org'], w['property']) == before
        assert await rent_db.scalar(text('SELECT notes FROM owner_profile WHERE id=:owner'), {'owner': owner}) == 'Gelöscht'
        await assert_no_person_marker(rent_db, w['org'], 'PrivateOwnerNotes')
    finally:
        await rent_db.rollback()



async def test_unused_sepa_consent_does_not_bind_person_to_retention(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:value,true)"), {'value': str(w['org'])})
    bank = await rent_db.scalar(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Unused bank') RETURNING id"), w)
    await rent_db.execute(text('''INSERT INTO sepa_creditor(organization_id,bank_account_id,creditor_id,name,country,town,bank_fingerprint,bank_authorized)
      VALUES (:org,:bank,'DE98ZZZ09999999999','PrivateUnusedCreditor','DE','Berlin',repeat('a',64),true)'''), {**w, 'bank': bank})
    await rent_db.execute(text('''INSERT INTO sepa_mandate(organization_id,bank_account_id,tenant_profile_id,creditor_id,reference,signed_on,
      sequence_type,debtor_country,source_fingerprint,original_reference)
      VALUES (:org,:bank,:tenant,'DE98ZZZ09999999999','UNUSED','2026-01-01','FRST','DE',repeat('a',64),'PrivateUnusedReference')'''), {**w, 'bank': bank})
    await rent_db.commit()
    async with api_client(w) as api:
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert 'sepa_mandate' not in preview.json()['retention']['retained']
        assert preview.json()['retention']['erased']['person'] == 2
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert await rent_db.scalar(text('SELECT count(*) FROM sepa_mandate WHERE organization_id=:org'), w) == 0
    assert await rent_db.scalar(text('SELECT count(*) FROM person WHERE organization_id=:org AND anonymized_at IS NOT NULL'), w) == 2
    await rent_db.rollback()


async def test_rental_settlement_does_not_retain_unrelated_owner_person(rent_world, rent_db, export_store):
    w = rent_world
    owner = await rent_db.scalar(text('INSERT INTO owner_profile(organization_id,person_id) VALUES (:org,:hidden_person) RETURNING id'), w)
    await rent_db.execute(text("INSERT INTO ownership_history(organization_id,unit_id,owner_profile_id,valid_from) VALUES (:org,:unit,:owner,'2020-01-01')"), {**w, 'owner': owner})
    await rent_db.execute(text('''INSERT INTO settlement_run(organization_id,property_id,run_type,period_start,period_end,
      unit_id,lease_agreement_id,tenant_profile_id,status,engine_version)
      VALUES (:org,:property,'BK_MIETER','2026-01-01','2026-12-31',:unit,:lease,:tenant,'finalized','legacy')'''), w)
    await rent_db.commit()
    async with api_client(w) as api:
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert 'ownership_history' not in preview.json()['retention']['retained']
        assert preview.json()['retention']['erased']['person'] == 1


async def test_database_cooling_off_guard_refuses_a_previously_authenticated_write(rent_world, export_store):
    from app.main import app
    from app.core.tenant import AuthContext, get_current_user
    w = rent_world
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
        # Simulate authentication completing just before another transaction
        # confirms deletion. The write must still fail at the DB guard.
        async def previously_authenticated():
            return AuthContext(w['admin'], w['org'], 'OWNER_ADMIN')
        app.dependency_overrides[get_current_user] = previously_authenticated
        try:
            answer = await api.post('/api/persons', json={'last_name': 'Must not be committed'})
            assert answer.status_code == 409, answer.text
        finally:
            app.dependency_overrides.pop(get_current_user, None)


async def test_each_retained_voucher_kind_keeps_row_and_original_hashes(rent_world, rent_db, export_store):
    import hashlib
    import uuid
    from app.services.organization_deletion_service import run_deletion
    from app.core.storage import make_storage_key
    w = rent_world
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:value,true)"), {'value': str(w['org'])})
    vendor = await rent_db.scalar(text('INSERT INTO vendor(organization_id,person_id) VALUES (:org,:person) RETURNING id'), w)
    category = await rent_db.scalar(text('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY code LIMIT 1'))
    cost = await rent_db.scalar(text('''INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
      vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
      VALUES (:org,:property,:category,1,:vendor,'2026-01-01','2026-01-01','2026-12-31',99,'UMLAGEFAEHIG','confirmed','MANUAL') RETURNING id'''), {**w, 'vendor': vendor, 'category': category})
    bank = await rent_db.scalar(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'LAUFEND','Retained bank') RETURNING id"), w)
    await rent_db.execute(text('''INSERT INTO payment(organization_id,bank_account_id,property_id,direction,amount,value_date,
      tenant_profile_id,payment_type,lease_agreement_id,period_year,period_month,status,retention_until)
      VALUES (:org,:bank,:property,'IN',99,'2026-01-01',:tenant,'KALTMIETE',:lease,2026,1,'booked','2034-12-31')'''), {**w, 'bank': bank})
    run = await rent_db.scalar(text('''INSERT INTO settlement_run(organization_id,property_id,run_type,period_start,period_end,
      unit_id,lease_agreement_id,tenant_profile_id,status,engine_version)
      VALUES (:org,:property,'BK_MIETER','2026-01-01','2026-12-31',:unit,:lease,:tenant,'finalized','legacy') RETURNING id'''), w)
    await rent_db.execute(text("INSERT INTO settlement_line(organization_id,settlement_run_id,line_type,label,amount) VALUES (:org,:run,'SUBTOTAL','Retained total',99)"), {**w, 'run': run})
    plan = await rent_db.scalar(text('''INSERT INTO economic_plan(organization_id,property_id,plan_year,period_start,period_end,
      reserve_contribution_total,status,engine_version) VALUES (:org,:property,2026,'2026-01-01','2026-12-31',0,'finalized','legacy') RETURNING id'''), w)
    await rent_db.execute(text("INSERT INTO economic_plan_line(organization_id,economic_plan_id,line_type,label,planned_amount) VALUES (:org,:plan,'SUBTOTAL','Retained planned total',99)"), {**w, 'plan': plan})
    await rent_db.execute(text('''INSERT INTO dunning_notice(organization_id,tenant_profile_id,level,issue_date,due_date,
      principal_amount,fee_amount,total_amount,status,issued_at,issued_by)
      VALUES (:org,:tenant,1,'2026-01-01','2026-02-01',99,0,99,'issued',now(),:admin)'''), w)
    original = b'%PDF-1.4\nRetained original with personal invoice content\n%%EOF'
    digest = hashlib.sha256(original).hexdigest()
    stored = export_store.put(make_storage_key(str(w['org']), digest), original, content_type='application/pdf')
    doc = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO invoice_document(id,organization_id,cost_entry_id,uploaded_by,
      storage_backend,storage_uri,original_filename,mime_type,content_byte_size,content_sha256,version,chain_root_id,status,released_at,retention_until)
      VALUES (:doc,:org,:cost,:admin,'LOCAL_FS',:uri,'Retained.pdf','application/pdf',:size,:digest,1,:doc,'active',now(),'2034-12-31')'''),
      {**w, 'doc': doc, 'cost': cost, 'uri': stored.uri, 'size': len(original), 'digest': digest})
    await rent_db.commit()
    kinds = ('cost_entry','payment','settlement_run','settlement_line','economic_plan','economic_plan_line','dunning_notice','invoice_document','document_ref')
    async def hashes():
        return {kind: hashlib.sha256('\n'.join((await rent_db.execute(text(f'SELECT row_to_json(t)::text FROM "{kind}" t WHERE organization_id=:org ORDER BY id'), w)).scalars()).encode()).hexdigest() for kind in kinds}
    before = await hashes()
    await rent_db.rollback()
    async with api_client(w) as api:
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        for kind in kinds:
            assert preview.json()['retention']['retained'][kind]['count'] == 1, kind
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    assert await hashes() == before
    assert hashlib.sha256(export_store.get(stored.uri)).hexdigest() == digest
    await rent_db.rollback()


def test_file_erasure_refuses_a_symlink_to_another_organization(tmp_path):
    import uuid
    import pytest
    from app.core.storage import LocalFsDocumentStore, get_store, set_store
    from app.services.organization_deletion_service import erase_unretained_files
    previous = get_store()
    try:
        store = LocalFsDocumentStore(tmp_path)
        set_store(store)
        source, foreign = uuid.uuid4(), uuid.uuid4()
        original = store.put(f'{foreign}/original.bin', b'foreign retained original', content_type='text/plain')
        (tmp_path / str(source)).symlink_to(tmp_path / str(foreign), target_is_directory=True)
        with pytest.raises(RuntimeError, match='Unexpected link'):
            erase_unretained_files(source, set())
        assert store.get(original.uri) == b'foreign retained original'
    finally:
        set_store(previous)


async def test_expired_personal_binding_is_anonymized_without_changing_voucher(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    # A new configuration reference must not extend an expired voucher deadline.
    async with api_client(w) as api:
        domain = await api.post('/api/person-data-domains',
            json={'person_id': str(w['person']), 'reason_code': 'INITIAL_SETUP'},
            headers={'Idempotency-Key': str(uuid.uuid4())})
        assert domain.status_code == 201, domain.text
    marker = 'PrivateVendorPerson'
    await rent_db.execute(text('UPDATE person SET first_name=:marker WHERE id=:person'), {**w, 'marker': marker})
    vendor = await rent_db.scalar(text('''INSERT INTO vendor(organization_id,person_id,notes,tax_id,vat_id)
      VALUES (:org,:person,:marker,:marker,:marker) RETURNING id'''), {**w, 'marker': marker})
    category = await rent_db.scalar(text('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY code LIMIT 1'))
    await rent_db.execute(text('''INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
      vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
      VALUES (:org,:property,:category,1,:vendor,'2000-01-01','2000-01-01','2000-12-31',99,'UMLAGEFAEHIG','confirmed','MANUAL')'''), {**w, 'vendor': vendor, 'category': category})
    await rent_db.commit()
    before = (await rent_db.execute(text('SELECT row_to_json(t)::text FROM cost_entry t WHERE organization_id=:org'), w)).scalars().all()
    await rent_db.rollback()
    async with api_client(w) as api:
        preview = await api.post('/api/organizations/deletion')
        assert preview.status_code == 200, preview.text
        assert preview.json()['retention']['erased']['person'] == 2
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    async with rent_db.begin():
        await run_deletion(rent_db, w['org'], now=datetime.now(timezone.utc) + timedelta(days=8))
    try:
        assert await rent_db.scalar(text('SELECT last_name FROM person WHERE id=:person'), w) == 'Anonymisiert'
        assert (await rent_db.execute(text('SELECT row_to_json(t)::text FROM cost_entry t WHERE organization_id=:org'), w)).scalars().all() == before
        assert (await rent_db.execute(text('SELECT notes,tax_id,vat_id FROM vendor WHERE id=:vendor'), {'vendor': vendor})).one() == ('Gelöscht', 'Gelöscht', 'Gelöscht')
        await assert_no_person_marker(rent_db, w['org'], marker)
    finally:
        await rent_db.rollback()


async def test_erasure_scrubs_expired_person_and_former_user_payloads_only(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import retained_hash, run_deletion
    w = rent_world
    private = {'person_id': str(w['person']), 'email': 'ExpiredPrivate@example.invalid',
               'first_name': 'ExpiredPrivateName', 'last_name': 'ExpiredPrivateSurname',
               'street': 'ExpiredPrivateStreet', 'phone': '+493012345678',
               'iban': 'DE89370400440532013000', 'zip_code': '12345', 'city': 'ExpiredPrivateCity'}
    await rent_db.execute(text('''UPDATE person SET first_name=:first_name,last_name=:last_name,email=:email,
      street=:street,phone=:phone,iban=:iban,zip_code=:zip_code,city=:city WHERE id=:person'''), {**w, **private})
    former_email = 'FormerPrivate@example.invalid'
    await rent_db.execute(text('UPDATE "user" SET email=:email WHERE id=:admin'), {**w, 'email': former_email})
    category = await rent_db.scalar(text('SELECT id FROM cost_category WHERE plan_version=1 ORDER BY code LIMIT 1'))
    for person, year in ((w['person'], 2000), (w['hidden_person'], 2026)):
        vendor = await rent_db.scalar(text('INSERT INTO vendor(organization_id,person_id) VALUES (:org,:person) RETURNING id'),
                                     {**w, 'person': person})
        await rent_db.execute(text('''INSERT INTO cost_entry(organization_id,property_id,cost_category_id,cost_category_plan_version,
          vendor_id,invoice_date,service_period_from,service_period_to,gross_amount,tenant_apportionable,status,source)
          VALUES (:org,:property,:category,1,:vendor,:start,:start,:end,99,'UMLAGEFAEHIG','confirmed','MANUAL')'''),
          {**w, 'category': category, 'vendor': vendor, 'start': datetime(year, 1, 1).date(), 'end': datetime(year, 12, 31).date()})
    bound = {'person_id': str(w['hidden_person']), 'email': 'BoundPrivate@example.invalid', 'name': 'BoundPrivateName'}
    operational = {'id': str(uuid.uuid4()), 'status': 'prepared', 'created_at': '2026-01-01T00:00:00Z',
                   'content_sha256': 'a' * 64, 'bytes': 123}
    identities = []
    for payload in ({**private, **operational}, {**bound, **operational},
                    {'email': former_email, 'recipients': [private, bound], 'reason': 'Sent to ' + private['email']}):
        identities.append(await rent_db.scalar(text('''INSERT INTO audit_event(organization_id,actor_user_id,
          entity_type,entity_id,action,payload_diff,prev_event_hash) VALUES (:org,:admin,'person',:person,
          'mail_prepared',CAST(:payload AS jsonb),repeat('b',64)) RETURNING id'''), {**w, 'payload': json.dumps(payload)}))
    request = uuid.uuid4()
    await rent_db.execute(text('''INSERT INTO master_import_batch(organization_id,actor_user_id,request_key,request_digest,result)
      VALUES (:org,:admin,:request,repeat('c',64),CAST(:payload AS jsonb))'''),
      {**w, 'request': request, 'payload': json.dumps({'rows': [{'kind': 'person', 'id': private['person_id'], 'key': private['email']}], 'count': 1})})
    foreign = await rent_db.scalar(text('''INSERT INTO audit_event(organization_id,actor_user_id,entity_type,entity_id,
      action,payload_diff) VALUES (:foreign_org,:foreign_admin,'person',:foreign_person,'mail_prepared',CAST(:payload AS jsonb))
      RETURNING id'''), {**w, 'payload': json.dumps(private)})
    await rent_db.commit()
    async with api_client(w) as api:
        assert (await api.post('/api/organizations/deletion')).status_code == 200
        assert (await api.post('/api/organizations/deletion/confirm', json={'organization_name': 'T23 synthetic'})).status_code == 200
    before = dict((await rent_db.execute(text('SELECT id,to_jsonb(t) FROM audit_event t WHERE organization_id=:org'), w)).all())
    await rent_db.rollback()
    future = datetime.now(timezone.utc) + timedelta(days=8)
    async with rent_db.begin():
        assert await run_deletion(rent_db, w['org'], now=future)
    after = dict((await rent_db.execute(text('SELECT id,to_jsonb(t) FROM audit_event t WHERE organization_id=:org'), w)).all())
    assert len(after) == len(before) + 1  # Only the erasure completion event is appended.
    for identity, original in before.items():
        assert {k: v for k, v in after[identity].items() if k != 'payload_diff'} == {
            k: v for k, v in original.items() if k != 'payload_diff'}
    scrubbed = after[identities[0]]['payload_diff']
    for value in private.values():
        assert value not in json.dumps(scrubbed)
    assert scrubbed['person_id'] == 'Gelöscht'
    assert all(scrubbed[key] == value for key, value in operational.items())
    assert scrubbed['_organization_erasure']['scrubbed_at'] == future.isoformat()
    assert after[identities[1]] == before[identities[1]]
    mixed = after[identities[2]]['payload_diff']
    assert former_email not in json.dumps(mixed)
    assert mixed['recipients'][1] == bound
    assert mixed['recipients'][0]['person_id'] == 'Gelöscht'
    receipt = await rent_db.scalar(text('SELECT result FROM master_import_batch WHERE organization_id=:org AND request_key=:request'),
                                   {**w, 'request': request})
    assert private['person_id'] not in json.dumps(receipt)
    assert private['email'] not in json.dumps(receipt)
    assert receipt['count'] == 1
    assert await rent_db.scalar(text('SELECT payload_diff FROM audit_event WHERE id=:id'), {'id': foreign}) == private
    state = await rent_db.scalar(text('SELECT retention FROM organization_deletion WHERE organization_id=:org'), w)
    for identity in identities:
        key = 'audit_event:' + json.dumps({'id': str(identity)}, separators=(',', ':'))
        assert state['record_hashes'][key] == retained_hash('audit_event', after[identity])
    await assert_no_person_marker(rent_db, w['org'], 'ExpiredPrivate')
    await assert_no_person_marker(rent_db, w['org'], former_email)
    await rent_db.rollback()


@pytest.mark.parametrize('role', ['app_rw', 'app_review', 'app_worker'])
@pytest.mark.parametrize('statement', ["UPDATE audit_event SET payload_diff='{}'", 'DELETE FROM audit_event'])
async def test_runtime_cannot_update_audit_payload_even_with_erasure_flag(rent_world, rent_db, role, statement):
    from sqlalchemy.exc import DBAPIError
    w = rent_world
    await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true),set_config('app.organization_erasure','on',true)"),
                          {'org': str(w['org'])})
    await rent_db.execute(text(f'SET LOCAL SESSION AUTHORIZATION {role}'))
    try:
        with pytest.raises(DBAPIError) as refused:
            async with rent_db.begin_nested():
                await rent_db.execute(text(statement + ' WHERE organization_id=:org'), w)
        assert refused.value.orig.sqlstate == '42501'
    finally:
        await rent_db.execute(text('SET LOCAL SESSION AUTHORIZATION DEFAULT'))
        await rent_db.rollback()


@pytest.mark.parametrize('mutation', ["payload_diff='{}'", "action='changed'", "actor_user_id=NULL,action='changed'",
                                    "occurred_at=now()+interval '1 day'", 'entity_id=NULL', 'prev_event_hash=NULL'])
async def test_audit_guard_refuses_non_erasure_or_structural_changes(rent_world, rent_db, mutation):
    from sqlalchemy.exc import DBAPIError
    w = rent_world
    identity = await rent_db.scalar(text('''INSERT INTO audit_event(organization_id,actor_user_id,entity_type,
      entity_id,action,payload_diff,prev_event_hash) VALUES (:org,:admin,'person',:person,'mail_prepared',
      '{"email":"private@example.invalid"}',repeat('a',64)) RETURNING id'''), w)
    # Even the operator cannot mutate ordinary live history. The flagged path
    # permits payload-only changes during the actual erasure transaction.
    if mutation != "payload_diff='{}'":
        await rent_db.execute(text('''INSERT INTO organization_deletion(organization_id,status,execute_after)
          VALUES (:org,'cooling_off',now()-interval '1 day')'''), w)
        await rent_db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(w['org'])})
        await rent_db.execute(text("SET LOCAL app.organization_erasure='on'"))
    statement = 'UPDATE audit_event SET ' + mutation
    with pytest.raises(DBAPIError):
        async with rent_db.begin_nested():
            await rent_db.execute(text(statement + ' WHERE id=:id'), {'id': identity})
    await rent_db.rollback()


@pytest.mark.parametrize('session_role', ['app_migrator', 'app_rw', 'app_worker', 'app_review'])
async def test_user_hard_delete_nulls_only_audit_actor_and_operator_can_delete_history(rent_world, rent_db, session_role):
    w = rent_world
    actor = await rent_db.scalar(text('''INSERT INTO "user"(organization_id,email,password_hash,display_name)
      VALUES (:org,'hard-delete@example.invalid','synthetic','Synthetic') RETURNING id'''), w)
    identity = await rent_db.scalar(text('''INSERT INTO audit_event(organization_id,actor_user_id,entity_type,
      entity_id,action,payload_diff) VALUES (:org,:actor,'person',:person,'person.created',
      '{"note":"preserved"}') RETURNING id'''), {**w, 'actor': actor})
    before = await rent_db.scalar(text("SELECT to_jsonb(a)-'actor_user_id' FROM audit_event a WHERE id=:id"), {'id': identity})
    # Model an authorized user deletion under each actual login. The FK action
    # executes as the table owner even when session_user is a runtime role.
    await rent_db.execute(text('''CREATE FUNCTION public.delete_audit_actor_probe(actor uuid) RETURNS void
      LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog AS $$
      DELETE FROM public."user" WHERE id=actor $$'''))
    await rent_db.execute(text(f'SET LOCAL SESSION AUTHORIZATION {session_role}'))
    try:
        await rent_db.execute(text('SELECT public.delete_audit_actor_probe(:actor)'), {'actor': actor})
    finally:
        await rent_db.execute(text('SET LOCAL SESSION AUTHORIZATION DEFAULT'))
    after = await rent_db.scalar(text('SELECT to_jsonb(a) FROM audit_event a WHERE id=:id'), {'id': identity})
    assert after.pop('actor_user_id') is None
    assert after == before
    assert (await rent_db.execute(text('DELETE FROM audit_event WHERE id=:id'), {'id': identity})).rowcount == 1
    await rent_db.rollback()
