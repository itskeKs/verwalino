from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import psycopg2
import pytest
import pytest_asyncio

try:
    SYNC_URL = os.environ["HV_DATABASE_URL_SYNC"]
except KeyError:
    raise RuntimeError(
        "HV_DATABASE_URL_SYNC must point to the disposable declaration test DB"
    ) from None

parsed_sync_url = urlparse(SYNC_URL)
if (
    parsed_sync_url.hostname not in {"localhost", "127.0.0.1"}
    or parsed_sync_url.port != 55432
):
    raise RuntimeError(
        "Refusing destructive migration tests outside localhost:55432"
    )


def _insert_legacy_property(seed_data, *, suffix: str) -> uuid.UUID:
    property_id = uuid.uuid4()
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO property "
                "(id, organization_id, name, street, zip_code, city, management_type) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    str(property_id),
                    str(seed_data["org_a_id"]),
                    "Legacy WEG",
                    f"Altweg {suffix}",
                    "41000",
                    "Mönchengladbach",
                    "WEG",
                ),
            )
    return property_id


async def _create_confirmed_property(client, *, denominator: int = 1_000) -> SimpleNamespace:
    response = await client.post(
        "/api/properties",
        json={
            "name": f"Neue WEG {uuid.uuid4()}",
            "street": "Testweg 1",
            "zip_code": "41000",
            "city": "Mönchengladbach",
            "management_type": "WEG",
            "mea_denominator": denominator,
        },
    )
    assert response.status_code == 201, response.text
    return SimpleNamespace(id=uuid.UUID(response.json()["id"]))


async def _create_unit(client, property_id: uuid.UUID, mea: str, *, number: str = "1"):
    return await client.post(
        "/api/units",
        json={
            "property_id": str(property_id),
            "number": number,
            "usage_type": "WOHNUNG",
            "mea": mea,
        },
    )


def _audit_event_exists(action: str, entity_id: str) -> bool:
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM audit_event WHERE action = %s AND entity_id = %s)",
                (action, entity_id),
            )
            return cur.fetchone()[0]


async def _wait_for_property_lock_waiters(expected_waiters: int) -> None:
    for _ in range(100):
        with psycopg2.connect(SYNC_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND query LIKE '%FROM property%' "
                    "AND query LIKE '%FOR UPDATE%'"
                )
                if cur.fetchone()[0] >= expected_waiters:
                    return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Expected {expected_waiters} requests to wait on the Property row lock"
    )


def _hold_property_row_lock(property_id: uuid.UUID):
    connection = psycopg2.connect(SYNC_URL)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM property WHERE id = %s FOR UPDATE", (str(property_id),)
            )
        return connection
    except Exception:
        connection.rollback()
        connection.close()
        raise


@pytest_asyncio.fixture
async def confirmed_property(client_a_admin) -> SimpleNamespace:
    return await _create_confirmed_property(client_a_admin)


@pytest_asyncio.fixture
async def legacy_property_with_mea(seed_data) -> SimpleNamespace:
    property_id = _insert_legacy_property(seed_data, suffix=str(uuid.uuid4()))
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO unit "
                "(id, organization_id, property_id, number, usage_type, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()),
                    str(seed_data["org_a_id"]),
                    str(property_id),
                    "1",
                    "WOHNUNG",
                    "500",
                ),
            )
    return SimpleNamespace(id=property_id)


@pytest.fixture
def cleanup_oversized_test_unit():
    """Delete the exact test row that cannot fit the pre-0023 MEA column."""
    unit_id: str | None = None

    def register(created_unit_id: str) -> None:
        nonlocal unit_id
        unit_id = created_unit_id

    yield register

    if unit_id is None:
        return
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM unit WHERE id = %s AND mea = %s",
                (unit_id, "1000000000000"),
            )


def test_nonnegative_mea_constraint_preserves_negative_legacy_rows(seed_data) -> None:
    """Rehearse actual 0023 against isolated pre-migration tables, retaining later evidence."""
    import importlib
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration = importlib.import_module('migrations.versions.0023_property_mea_scale')
    engine = create_engine(SYNC_URL)
    try:
        with engine.connect() as db:
            transaction = db.begin()
            try:
                db.execute(text('SET LOCAL search_path=pg_temp,public'))
                db.execute(text('CREATE TEMP TABLE property(id uuid PRIMARY KEY) ON COMMIT DROP'))
                db.execute(text('CREATE TEMP TABLE unit(id uuid PRIMARY KEY, mea numeric(12,6)) ON COMMIT DROP'))
                unit_id = uuid.uuid4()
                db.execute(text('INSERT INTO unit(id,mea) VALUES (:id,-1)'), {'id':unit_id})
                with Operations.context(MigrationContext.configure(db)):
                    migration.upgrade()
                assert str(db.scalar(text('SELECT mea FROM unit WHERE id=:id'), {'id':unit_id})) == '-1.000000'
                assert db.scalar(text("SELECT convalidated FROM pg_constraint WHERE conname='ck_unit_mea_nonnegative' AND conrelid='pg_temp.unit'::regclass")) is False
                with pytest.raises(DBAPIError):
                    with db.begin_nested():
                        db.execute(text('INSERT INTO unit(id,mea) VALUES (:id,-1)'), {'id':uuid.uuid4()})
            finally: transaction.rollback()
    finally: engine.dispose()


@pytest.mark.asyncio
async def test_existing_property_reports_unconfirmed_scale(
    client_a_admin, seed_data
) -> None:
    property_id = _insert_legacy_property(seed_data, suffix="1")
    response = await client_a_admin.get(f"/api/properties/{property_id}")
    assert response.status_code == 200
    assert response.json()["mea_scale_status"] == "LEGACY_UNCONFIRMED"
    assert response.json()["mea_denominator"] is None


@pytest.mark.asyncio
async def test_manual_mea_write_is_blocked_without_confirmed_scale(
    client_a_admin, seed_data
) -> None:
    property_id = _insert_legacy_property(seed_data, suffix="2")
    response = await _create_unit(client_a_admin, property_id, "500")
    assert response.status_code == 409
    assert "MEA scale" in response.json()["detail"]


@pytest.mark.asyncio
async def test_manual_property_can_start_with_confirmed_scale(client_a_admin) -> None:
    response = await client_a_admin.post(
        "/api/properties",
        json={
            "name": "Neue WEG",
            "street": "Testweg 1",
            "zip_code": "41000",
            "city": "Mönchengladbach",
            "management_type": "WEG",
            "mea_denominator": 1_000,
        },
    )
    assert response.status_code == 201
    assert response.json()["mea_scale_status"] == "CONFIRMED"
    assert response.json()["mea_denominator"] == 1_000


@pytest.mark.asyncio
async def test_legacy_scale_confirmation_is_explicit_and_audited(
    client_a_admin, legacy_property_with_mea
) -> None:
    response = await client_a_admin.post(
        f"/api/properties/{legacy_property_with_mea.id}/mea-scale/confirm",
        json={
            "denominator": 1_000,
            "expected_scale_status": "LEGACY_UNCONFIRMED",
            "confirm_existing_values_as_numerators": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["mea_scale_status"] == "CONFIRMED"
    assert response.json()["mea_denominator"] == 1_000
    assert _audit_event_exists(
        "property.mea_scale_confirmed", str(legacy_property_with_mea.id)
    )


@pytest.mark.asyncio
async def test_unit_building_part_round_trips(client_a_admin) -> None:
    confirmed_property = await _create_confirmed_property(
        client_a_admin, denominator=100_000
    )
    response = await client_a_admin.post(
        "/api/units",
        json={
            "property_id": str(confirmed_property.id),
            "number": "34",
            "usage_type": "WOHNUNG",
            "building_part": "Gebäude I",
            "mea": "47732",
        },
    )
    assert response.status_code == 201
    assert response.json()["building_part"] == "Gebäude I"


@pytest.mark.asyncio
async def test_full_denominator_numerator_fits_storage(
    client_a_admin, cleanup_oversized_test_unit
) -> None:
    prop = await _create_confirmed_property(client_a_admin, denominator=1_000_000_000_000)
    response = await _create_unit(client_a_admin, prop.id, "1000000000000")
    if response.status_code == 201:
        cleanup_oversized_test_unit(response.json()["id"])
    assert response.status_code == 201
    assert response.json()["mea"] == "1000000000000"


@pytest.mark.asyncio
async def test_fractional_mea_is_rejected(client_a_admin, confirmed_property) -> None:
    response = await _create_unit(client_a_admin, confirmed_property.id, "1.5")
    assert response.status_code == 422
    assert response.json()["detail"] == "MEA_MUST_BE_INTEGER"


@pytest.mark.asyncio
async def test_unit_mea_cannot_exceed_denominator(client_a_admin, confirmed_property) -> None:
    response = await _create_unit(client_a_admin, confirmed_property.id, "1001")
    assert response.status_code == 422
    assert response.json()["detail"] == "MEA_EXCEEDS_DENOMINATOR"


@pytest.mark.asyncio
async def test_create_rejects_aggregate_mea_overflow(client_a_admin, confirmed_property) -> None:
    assert (await _create_unit(client_a_admin, confirmed_property.id, "600")).status_code == 201
    response = await _create_unit(client_a_admin, confirmed_property.id, "401", number="2")
    assert response.status_code == 409
    assert response.json()["detail"] == "MEA_TOTAL_EXCEEDS_DENOMINATOR"


@pytest.mark.asyncio
async def test_update_rejects_aggregate_mea_overflow(client_a_admin, confirmed_property) -> None:
    first = await _create_unit(client_a_admin, confirmed_property.id, "500")
    assert first.status_code == 201
    assert (await _create_unit(client_a_admin, confirmed_property.id, "500", number="2")).status_code == 201
    response = await client_a_admin.put(
        f"/api/units/{first.json()['id']}", json={"mea": "501"}
    )
    assert response.status_code == 409
    units = await client_a_admin.get(f"/api/units?property_id={confirmed_property.id}")
    assert sorted(unit["mea"] for unit in units.json()) == ["500.000000", "500.000000"]


@pytest.mark.asyncio
async def test_legacy_confirmation_rejects_fractional_existing_mea(
    client_a_admin, seed_data
) -> None:
    property_id = _insert_legacy_property(seed_data, suffix="fractional")
    with psycopg2.connect(SYNC_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO unit "
                "(id, organization_id, property_id, number, usage_type, mea) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid.uuid4()), str(seed_data["org_a_id"]), str(property_id),
                    "1", "WOHNUNG", "0.5",
                ),
            )
    response = await client_a_admin.post(
        f"/api/properties/{property_id}/mea-scale/confirm",
        json={
            "denominator": 1_000,
            "expected_scale_status": "LEGACY_UNCONFIRMED",
            "confirm_existing_values_as_numerators": True,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "LEGACY_MEA_NOT_INTEGER"
    property_response = await client_a_admin.get(f"/api/properties/{property_id}")
    assert property_response.json()["mea_scale_status"] == "LEGACY_UNCONFIRMED"
    assert not _audit_event_exists("property.mea_scale_confirmed", str(property_id))


@pytest.mark.asyncio
async def test_concurrent_creates_serialize_on_property(
    client_a_admin, client_a_clerk
) -> None:
    prop = await _create_confirmed_property(client_a_admin)
    admin_started = asyncio.Event()
    clerk_started = asyncio.Event()
    lock_connection = _hold_property_row_lock(prop.id)
    first_task: asyncio.Task | None = None
    second_task: asyncio.Task | None = None
    try:
        async def admin_create():
            admin_started.set()
            return await _create_unit(client_a_admin, prop.id, "600", number="1")

        async def clerk_create():
            clerk_started.set()
            return await _create_unit(client_a_clerk, prop.id, "600", number="2")

        first_task = asyncio.create_task(admin_create())
        second_task = asyncio.create_task(clerk_create())
        await asyncio.gather(admin_started.wait(), clerk_started.wait())
        await _wait_for_property_lock_waiters(2)
        lock_connection.commit()
        first, second = await asyncio.gather(first_task, second_task)
    finally:
        lock_connection.rollback()
        lock_connection.close()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task is not None),
            return_exceptions=True,
        )

    assert sorted((first.status_code, second.status_code)) == [201, 409]
    units = await client_a_admin.get(f"/api/units?property_id={prop.id}")
    assert sum(int(unit["mea"].split(".")[0]) for unit in units.json()) == 600


@pytest.mark.asyncio
async def test_concurrent_mea_updates_serialize_on_property(
    client_a_admin, client_a_clerk
) -> None:
    prop = await _create_confirmed_property(client_a_admin)
    first = await _create_unit(client_a_admin, prop.id, "400", number="1")
    second = await _create_unit(client_a_admin, prop.id, "400", number="2")
    assert first.status_code == second.status_code == 201

    admin_started = asyncio.Event()
    clerk_started = asyncio.Event()
    lock_connection = _hold_property_row_lock(prop.id)
    first_task: asyncio.Task | None = None
    second_task: asyncio.Task | None = None
    try:
        async def admin_update():
            admin_started.set()
            return await client_a_admin.put(
                f"/api/units/{first.json()['id']}", json={"mea": "600"}
            )

        async def clerk_update():
            clerk_started.set()
            return await client_a_clerk.put(
                f"/api/units/{second.json()['id']}", json={"mea": "600"}
            )

        first_task = asyncio.create_task(admin_update())
        second_task = asyncio.create_task(clerk_update())
        await asyncio.gather(admin_started.wait(), clerk_started.wait())
        await _wait_for_property_lock_waiters(2)
        lock_connection.commit()
        first_response, second_response = await asyncio.gather(first_task, second_task)
    finally:
        lock_connection.rollback()
        lock_connection.close()
        for task in (first_task, second_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (first_task, second_task) if task is not None),
            return_exceptions=True,
        )

    assert sorted((first_response.status_code, second_response.status_code)) == [200, 409]
    units = await client_a_admin.get(f"/api/units?property_id={prop.id}")
    assert sum(int(unit["mea"].split(".")[0]) for unit in units.json()) == 1_000


@pytest.mark.asyncio
async def test_other_tenant_cannot_write_or_confirm_another_orgs_mea_scale(
    client_a_admin, client_b_admin
) -> None:
    prop = await _create_confirmed_property(client_a_admin)
    unit = await _create_unit(client_a_admin, prop.id, "400")
    assert unit.status_code == 201

    create_response = await client_b_admin.post(
        "/api/units",
        json={
            "property_id": str(prop.id),
            "number": "foreign-create",
            "usage_type": "WOHNUNG",
            "mea": "1",
        },
    )
    update_response = await client_b_admin.put(
        f"/api/units/{unit.json()['id']}", json={"mea": "500"}
    )
    confirm_response = await client_b_admin.post(
        f"/api/properties/{prop.id}/mea-scale/confirm",
        json={
            "denominator": 1_000,
            "expected_scale_status": "CONFIRMED",
            "confirm_existing_values_as_numerators": True,
        },
    )

    assert create_response.status_code == 404
    assert update_response.status_code == 404
    assert confirm_response.status_code == 404
    assert not _audit_event_exists("property.mea_scale_confirmed", str(prop.id))

    property_response = await client_a_admin.get(f"/api/properties/{prop.id}")
    assert property_response.json()["mea_scale_status"] == "CONFIRMED"
    assert property_response.json()["mea_denominator"] == 1_000
    units = await client_a_admin.get(f"/api/units?property_id={prop.id}")
    assert [item["mea"] for item in units.json()] == ["400.000000"]
