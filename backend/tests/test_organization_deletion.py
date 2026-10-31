"""Organization erasure preserves immutable records and ends all access."""
from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import text

from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_organization_export import export_store  # noqa: F401


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
    assert all(stored[identity] == data for identity, data in retained)
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
    await rent_db.rollback()


async def test_retained_voucher_and_bound_person_are_unchanged(rent_world, rent_db, export_store):
    from app.core.security import hash_password
    from app.services.organization_deletion_service import run_deletion
    w = rent_world
    email = await rent_db.scalar(text('SELECT email FROM "user" WHERE id=:admin'), w)
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
    async with api_client(w) as api:
        login = await api.post('/api/auth/login', json={'email': email, 'password': 'Deletion-Owner-Password-123'})
        assert login.status_code == 401, login.text
    await rent_db.rollback()


async def test_legacy_finalized_plan_keeps_owner_name_history(rent_world, rent_db, export_store):
    from app.services.organization_deletion_service import run_deletion
    from app.services.render_names import _owner_names_map
    w = rent_world
    owner = await rent_db.scalar(text('INSERT INTO owner_profile(organization_id,person_id) VALUES (:org,:person) RETURNING id'), w)
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
    assert await _owner_names_map(rent_db, w['org'], w['property']) == before
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
    vendor = await rent_db.scalar(text('INSERT INTO vendor(organization_id,person_id) VALUES (:org,:person) RETURNING id'), w)
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
    assert await rent_db.scalar(text('SELECT last_name FROM person WHERE id=:person'), w) == 'Anonymisiert'
    assert (await rent_db.execute(text('SELECT row_to_json(t)::text FROM cost_entry t WHERE organization_id=:org'), w)).scalars().all() == before
    await rent_db.rollback()
