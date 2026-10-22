"""M6 bank accounts API tests -- list, get, create, RBAC, RLS."""

from __future__ import annotations

import os
import uuid

import pytest
import psycopg2
from tests.test_rent_changes import rent_db, rent_world, api_client  # noqa: F401
from tests.test_sepa_export import sepa_world, configure, account_url, export_body  # noqa: F401

SYNC_URL = os.environ.get(
    "HV_DATABASE_URL_SYNC",
    "postgresql://app_migrator:migrator_secret@localhost:5433/hausverwaltung",
)


@pytest.fixture(scope="session")
def ba_seed(seed_data):
    org_a = str(seed_data["org_a_id"])
    org_b = str(seed_data["org_b_id"])
    ids = {
        "prop_a": uuid.uuid4(),
        "prop_b_org": uuid.uuid4(),
        "acct_a_1": uuid.uuid4(),
        "acct_a_2": uuid.uuid4(),
        "acct_b": uuid.uuid4(),
    }

    conn = psycopg2.connect(SYNC_URL)
    conn.autocommit = False
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_a"]), org_a, "BA Haus A", "MIET"),
        )
        cur.execute(
            "INSERT INTO property (id, organization_id, name, management_type) VALUES (%s,%s,%s,%s)",
            (str(ids["prop_b_org"]), org_b, "BA Haus B", "WEG"),
        )
        # Pre-existing accounts
        for aid, pid, org, label, atype in [
            (ids["acct_a_1"], ids["prop_a"], org_a, "Girokonto", "LAUFEND"),
            (ids["acct_a_2"], ids["prop_a"], org_a, "Ruecklage", "RUECKLAGE"),
            (ids["acct_b"], ids["prop_b_org"], org_b, "WEG Konto", "LAUFEND"),
        ]:
            cur.execute(
                "INSERT INTO bank_account (id, organization_id, property_id, account_type, label) "
                "VALUES (%s,%s,%s,%s,%s)",
                (str(aid), str(org), str(pid), atype, label),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {**ids, "org_a": seed_data["org_a_id"], "org_b": seed_data["org_b_id"]}


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_all(client_a_admin, ba_seed):
    r = await client_a_admin.get("/api/bank-accounts")
    assert r.status_code == 200
    data = r.json()
    ids = {d["id"] for d in data}
    assert str(ba_seed["acct_a_1"]) in ids
    assert str(ba_seed["acct_a_2"]) in ids
    # org_b account must NOT appear
    assert str(ba_seed["acct_b"]) not in ids


@pytest.mark.asyncio
async def test_list_by_property(client_a_admin, ba_seed):
    r = await client_a_admin.get(f"/api/bank-accounts?property_id={ba_seed['prop_a']}")
    assert r.status_code == 200
    data = r.json()
    # At least the two we seeded
    labels = {d["label"] for d in data}
    assert "Girokonto" in labels
    assert "Ruecklage" in labels


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_own(client_a_admin, ba_seed):
    r = await client_a_admin.get(f"/api/bank-accounts/{ba_seed['acct_a_1']}")
    assert r.status_code == 200
    assert r.json()["label"] == "Girokonto"


@pytest.mark.asyncio
async def test_get_foreign_404(client_a_admin, ba_seed):
    """Cross-org access -> 404 (not 403, RLS)."""
    r = await client_a_admin.get(f"/api/bank-accounts/{ba_seed['acct_b']}")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_success(client_a_admin, ba_seed):
    r = await client_a_admin.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "KAUTION",
            "label": "Kautionskonto",
        },
    )
    assert r.status_code == 201
    data = r.json()
    assert data["account_type"] == "KAUTION"
    assert data["label"] == "Kautionskonto"
    assert data["property_id"] == str(ba_seed["prop_a"])


@pytest.mark.asyncio
async def test_create_invalid_type(client_a_admin, ba_seed):
    r = await client_a_admin.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "INVALID",
            "label": "Nope",
        },
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_readonly_forbidden(client_a_readonly, ba_seed):
    r = await client_a_readonly.post(
        "/api/bank-accounts",
        json={
            "property_id": str(ba_seed["prop_a"]),
            "account_type": "LAUFEND",
            "label": "Blocked",
        },
    )
    assert r.status_code == 403


async def t55_account(client, ba_seed):
    response = await client.post('/api/bank-accounts', json={
        'property_id': str(ba_seed['prop_a']), 'account_type': 'LAUFEND',
        'label': 'T55 Original', 'iban': 'DE89370400440532013000'})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_t55_bank_rename_and_end_persist(client_a_admin, ba_seed):
    original = await t55_account(client_a_admin, ba_seed)
    url = f"/api/bank-accounts/{original['id']}"
    renamed = await client_a_admin.patch(url, json={'label': 'T55 Mietkonto', 'bank_name': 'Testbank'})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['label'] == 'T55 Mietkonto'
    assert renamed.json()['iban'] == original['iban']
    ended = await client_a_admin.patch(url, json={'is_active': False})
    assert ended.status_code == 200, ended.text
    saved = await client_a_admin.get(url)
    assert saved.status_code == 200
    assert saved.json()['is_active'] is False
    assert saved.json()['property_id'] == original['property_id']
    assert saved.json()['label'] == 'T55 Mietkonto'
    assert saved.json()['account_type'] == original['account_type']
    blocked_import = await client_a_admin.post(url + '/imports?dry_run=true', files={'file': ('synthetic.csv', b'not a bank statement', 'text/csv')})
    assert blocked_import.status_code == 409, blocked_import.text
    assert 'inactive' in blocked_import.json()['detail'].lower()


@pytest.mark.asyncio
async def test_t55_used_bank_details_cannot_change_through_api_or_sql(sepa_world):
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    w = sepa_world
    url = f"/api/bank-accounts/{w['bank']}"
    async with api_client(w) as api:
        await configure(api, w)
        original = (await api.get(url)).json()
        refused = await api.patch(url, json={'bic': 'TESTDEFFXXX'})
        assert refused.status_code == 409, refused.text
        assert (await api.get(url)).json() == original
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text('UPDATE bank_account SET bic=:bic WHERE organization_id=:org AND id=:bank'),
                                           {**w, 'bic': 'TESTDEFFXXX'})
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as api:
        assert (await api.get(url)).json() == original


@pytest.mark.asyncio
async def test_t55_bound_account_needs_live_prepare_not_only_read(sepa_world, rent_db):
    from sqlalchemy import text
    w = sepa_world
    ids = {**w, 'scope': uuid.uuid4(), 'grant': uuid.uuid4()}
    await rent_db.execute(text("""INSERT INTO accounting_scope(id,organization_id,property_id,scope_type,label,
        valid_from,whole_property,origin,created_by) VALUES (:scope,:org,:property,'MIET','T55 Exact sphere',
        '2026-01-01',true,'REVIEWED',:admin)"""), ids)
    await rent_db.execute(text("""INSERT INTO accounting_scope_binding(organization_id,property_id,scope_id,
        bank_account_id,origin,reason,reviewed_by) VALUES (:org,:property,:scope,:bank,'REVIEWED','T55 source',:admin)"""), ids)
    await rent_db.execute(text("""INSERT INTO scope_grant(id,organization_id,scope_id,user_id,capabilities,granted_by,reason)
        VALUES (:grant,:org,:scope,:clerk,ARRAY['read','prepare'],:admin,'T55 exact preparation')"""), ids)
    await rent_db.commit()
    url = f"/api/bank-accounts/{w['bank']}"
    async with api_client(w, actor='clerk') as api:
        renamed = await api.patch(url, json={'label': 'T55 Exact right'})
        assert renamed.status_code == 200, renamed.text
        await rent_db.execute(text('UPDATE scope_grant SET revoked_at=clock_timestamp(),revoked_by=:admin WHERE id=:grant'), ids)
        await rent_db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
            VALUES (:org,:scope,:clerk,ARRAY['read'],:admin,'T55 read without preparation')"""), ids)
        await rent_db.commit()
        assert (await api.get(url)).status_code == 200
        refused = await api.patch(url, json={'label': 'Forbidden after revocation'})
        assert refused.status_code in (403, 404), refused.text
        assert (await api.get(url)).json()['label'] == 'T55 Exact right'


@pytest.mark.asyncio
async def test_t55_bank_mutations_require_writer_and_same_org(client_a_admin, client_a_readonly, client_b_admin, ba_seed):
    original = await t55_account(client_a_admin, ba_seed)
    url = f"/api/bank-accounts/{original['id']}"
    assert (await client_a_readonly.patch(url, json={'label': 'forbidden'})).status_code == 403
    assert (await client_b_admin.patch(url, json={'is_active': False})).status_code == 404
    assert (await client_a_admin.get(url)).json()['label'] == 'T55 Original'


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [{'label': 'x' * 256}, {'is_active': 1}, {'property_id': str(uuid.uuid4())}, {'account_type': 'KAUTION'}])
async def test_t55_bank_rejects_invalid_or_identity_edits(client_a_admin, ba_seed, body):
    original = await t55_account(client_a_admin, ba_seed)
    response = await client_a_admin.patch(f"/api/bank-accounts/{original['id']}", json=body)
    assert response.status_code == 422, response.text
    assert (await client_a_admin.get(f"/api/bank-accounts/{original['id']}")).json() == original


@pytest.mark.asyncio
async def test_t55_bank_rename_preserves_sepa_and_end_blocks_new_collection(sepa_world):
    w = sepa_world
    async with api_client(w) as api:
        await configure(api, w)
        original = (await api.get(account_url(w))).json()
        expected = {**original, 'bank': {**original['bank'], 'label': 'T55 Nur Bezeichnung'}}
        source = export_body(w)
        before = await api.post('/api/payments/sepa/preview', json=source)
        assert before.status_code == 200, before.text
        assert before.json()['total'] == '850.00'
        url = f"/api/bank-accounts/{w['bank']}"
        renamed = await api.patch(url, json={'label': 'T55 Nur Bezeichnung'})
        assert renamed.status_code == 200, renamed.text
        after = await api.post('/api/payments/sepa/preview', json=source)
        assert after.status_code == 200, after.text
        assert after.json() == before.json()
        assert (await api.get(account_url(w))).json() == expected
        closed = await api.patch(url, json={'is_active': False})
        assert closed.status_code == 200, closed.text
        refused = await api.post('/api/payments/sepa/preview', json=source)
        assert refused.status_code == 409, refused.text
        assert (await api.get(account_url(w))).json() == expected
        assert (await api.get(url)).json()['is_active'] is False


@pytest.mark.asyncio
async def test_t55_closed_bank_still_allows_existing_booked_payment_reversal(sepa_world):
    w = sepa_world
    body = {'bank_account_id': str(w['bank']), 'direction': 'IN', 'amount': '10.00',
            'value_date': '2026-10-01', 'payment_type': 'KALTMIETE',
            'tenant_profile_id': str(w['tenant']), 'lease_agreement_id': str(w['lease']),
            'period_year': 2026, 'period_month': 10}
    async with api_client(w) as api:
        created = await api.post('/api/payments', json=body)
        assert created.status_code == 201, created.text
        payment_id = created.json()['id']
        booked = await api.post(f'/api/payments/{payment_id}/book')
        assert booked.status_code == 200, booked.text
        ended = await api.patch(f"/api/bank-accounts/{w['bank']}", json={'is_active': False})
        assert ended.status_code == 200, ended.text
        assert (await api.post('/api/payments', json=body)).status_code == 409
        reversed_payment = await api.post(f'/api/payments/{payment_id}/reverse', json={'note': 'T55 correction of existing history'})
        assert reversed_payment.status_code == 201, reversed_payment.text
        assert reversed_payment.json()['amount'] == '10.00'
        assert reversed_payment.json()['direction'] == 'OUT'
        assert reversed_payment.json()['reverses_id'] == payment_id
        original = (await api.get(f'/api/payments/{payment_id}')).json()
        assert original['reversed_by_id'] == reversed_payment.json()['id']
        assert original['amount'] == '10.00'


@pytest.mark.asyncio
async def test_t55_bank_rename_waits_for_shared_financial_input_lock(sepa_world, rent_db):
    import asyncio
    from sqlalchemy import text
    w = sepa_world
    await rent_db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                          {'key': f"financial-inputs:{w['org']}"})
    blocker = await rent_db.scalar(text('SELECT pg_backend_pid()'))
    async with api_client(w) as api:
        task = asyncio.create_task(api.patch(f"/api/bank-accounts/{w['bank']}", json={'label': 'T55 Serialized'}))
        waiting = False
        try:
            for _ in range(200):
                waiting = await rent_db.scalar(text("""SELECT EXISTS(SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND NOT l.granted
                    AND :blocker=ANY(pg_blocking_pids(l.pid)))"""), {'blocker': blocker})
                if waiting or task.done():
                    break
                await asyncio.sleep(0.01)
        finally:
            await rent_db.rollback()
        response = await asyncio.wait_for(task, timeout=15)
        assert waiting, 'Bank metadata changes must use the existing financial-input lock'
        assert response.status_code == 200, response.text


@pytest.mark.asyncio
async def test_t55_migration_preserves_old_rows_security_and_issued_pdf(sepa_world):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_letters import create_template, issue_body
    w = sepa_world
    async with api_client(w) as api:
        response = await api.put(f"/api/properties/{w['property']}", json={'construction_year': 1984, 'total_area': '1234.56'})
        assert response.status_code == 200, response.text
        template = await create_template(api)
        issued = await api.post('/api/letters/issue', json=issue_body(w, template))
        assert issued.status_code == 201, issued.text
        pdf_url = issued.json()[0]['pdf_url']
        original_pdf = (await api.get(pdf_url)).content
        assert original_pdf.startswith(b'%PDF')
    paths = list((Path(__file__).parents[1] / 'migrations/versions').glob('0136_*.py'))
    assert len(paths) == 1
    spec = importlib.util.spec_from_file_location('t55_migration', paths[0])
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == '0136' and migration.down_revision == '0135'
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()

            def snapshot():
                names = connection.execute(text("""SELECT c.relname FROM pg_class c
                    JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relkind IN ('r','p')
                    AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_class'::regclass
                        AND d.objid=c.oid AND d.deptype='e') ORDER BY c.relname""")).scalars().all()
                removed = {'property': "ARRAY['construction_year','total_area']", 'bank_account': "ARRAY['is_active']", 'vendor': "ARRAY['is_active']"}
                rows = {}
                for name in names:
                    expression = 'to_jsonb(t)' + (' - ' + removed[name] if name in removed else '')
                    rows[name] = connection.execute(text(f'SELECT ({expression})::text FROM "{name}" t ORDER BY ({expression})::text')).scalars().all()
                security = [connection.execute(text(query)).all() for query in (
                    "SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity,c.relacl::text,pg_get_userbyid(c.relowner) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname",
                    "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY tablename,policyname",
                    "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal AND left(t.tgname,4)<>'t55_' AND t.tgname<>'a_t55_input_lock' ORDER BY pg_get_triggerdef(t.oid)",
                    "SELECT pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' AND left(p.proname,4)<>'t55_' ORDER BY p.proname,p.oid",
                )]
                return rows, security

            try:
                before = snapshot()
                assert before[0]['lease_agreement'] and before[0]['letter_document']
                with Operations.context(MigrationContext.configure(connection)):
                    with pytest.raises(DBAPIError):
                        with connection.begin_nested():
                            migration.downgrade()
                    assert snapshot() == before
                    connection.execute(text('UPDATE property SET construction_year=NULL,total_area=NULL WHERE construction_year IS NOT NULL OR total_area IS NOT NULL'))
                    connection.execute(text('UPDATE bank_account SET is_active=true WHERE NOT is_active'))
                    connection.execute(text('UPDATE vendor SET is_active=true WHERE NOT is_active'))
                    connection.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
                    migration.downgrade()
                    assert not connection.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='property' AND column_name='construction_year')"))
                    assert snapshot() == before
                    migration.upgrade()
                    assert snapshot() == before
                    assert connection.scalar(text('SELECT bool_and(is_active) FROM bank_account'))
                    assert connection.scalar(text('SELECT count(*) FROM property WHERE construction_year IS NOT NULL OR total_area IS NOT NULL')) == 0
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    async with api_client(w) as api:
        prop = (await api.get(f"/api/properties/{w['property']}")).json()
        assert (prop['construction_year'], prop['total_area']) == (1984, '1234.56')
        assert (await api.get(pdf_url)).content == original_pdf
