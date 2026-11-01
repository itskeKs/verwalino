"""T18: actual API, disposable PostgreSQL, exact synthetic import expectations."""
from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest

from tests.conftest import MIGRATOR_SYNC_URL

CSV = (
    "Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n"
    "02.01.2026;03.01.2026;700,00;Müller;DE02120300000000202051;Kaltmiete;EUR\n"
    "04.01.2026;05.01.2026;150,00;Müller;DE02120300000000202051;NK;EUR\n"
    "06.01.2026;07.01.2026;-200,01;Heizservice;;Heizung;EUR\n"
).encode()
FIXTURES = Path(__file__).parent / "fixtures" / "bank"


@pytest.fixture
def import_accounts(seed_data):
    accounts = {}
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        for tenant, iban in [("a", "DE02120300000000202051"), ("b", "DE12500105170648489890")]:
            property_id, account_id = uuid.uuid4(), uuid.uuid4()
            cursor.execute(
                "INSERT INTO property (id,organization_id,name,management_type) VALUES (%s,%s,%s,'WEG')",
                (str(property_id), str(seed_data[f"org_{tenant}_id"]), "Import test"),
            )
            cursor.execute(
                "INSERT INTO bank_account (id,organization_id,property_id,account_type,label,iban) VALUES (%s,%s,%s,'LAUFEND','Import test',%s)",
                (str(account_id), str(seed_data[f"org_{tenant}_id"]), str(property_id), iban),
            )
            accounts[tenant] = str(account_id)
    return accounts


def stored(account_id):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT organization_id,amount,status,import_hash FROM bank_transaction WHERE bank_account_id=%s ORDER BY booking_date",
            (account_id,),
        )
        return [(uuid.UUID(str(org_id)), amount, status, import_hash)
                for org_id, amount, status, import_hash in cursor.fetchall()]


async def upload(client, account_id, *, content=CSV, filename="statement.csv", dry_run=True):
    return await client.post(
        f"/api/bank-accounts/{account_id}/imports",
        params={"dry_run": str(dry_run).lower()},
        files={"file": (filename, content, "application/octet-stream")},
    )


@pytest.mark.anyio
async def test_preview_then_import_and_reimport(client_a_admin, import_accounts, seed_data):
    account = import_accounts["a"]
    response = await upload(client_a_admin, account)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["count"] == preview["new_count"] == 3
    assert preview["duplicate_count"] == preview["imported_count"] == 0
    assert Decimal(preview["total_amount"]) == Decimal("649.99")
    assert Decimal(preview["new_total_amount"]) == Decimal("649.99")
    assert preview["date_from"] == "2026-01-02"
    assert preview["date_to"] == "2026-01-06"
    assert [Decimal(row["amount"]) for row in preview["transactions"]] == [Decimal("700.00"), Decimal("150.00"), Decimal("-200.01")]
    assert stored(account) == []

    response = await upload(client_a_admin, account, dry_run=False)
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 3
    rows = stored(account)
    assert [row[1] for row in rows] == [Decimal("700.00"), Decimal("150.00"), Decimal("-200.01")]
    assert all(row[0] == seed_data["org_a_id"] and row[2] == "NEU" for row in rows)
    assert len({row[3] for row in rows}) == 3

    for dry_run in (True, False):
        repeated = await upload(client_a_admin, account, dry_run=dry_run)
        assert repeated.status_code == 200, repeated.text
        result = repeated.json()
        assert result["count"] == result["duplicate_count"] == 3
        assert result["new_count"] == result["imported_count"] == 0
        assert Decimal(result["new_total_amount"]) == Decimal("0.00")
        assert stored(account) == rows


@pytest.mark.anyio
async def test_default_is_preview_not_write(client_a_admin, import_accounts):
    response = await client_a_admin.post(
        f"/api/bank-accounts/{import_accounts['a']}/imports",
        files={"file": ("test.csv", CSV)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 0
    assert stored(import_accounts["a"]) == []


@pytest.mark.anyio
async def test_concurrent_import_does_not_duplicate(client_a_admin, import_accounts):
    results = await asyncio.gather(*[
        upload(client_a_admin, import_accounts["a"], dry_run=False)
        for _ in range(2)
    ])
    assert [result.status_code for result in results] == [200, 200], [r.text for r in results]
    assert sorted(result.json()["imported_count"] for result in results) == [0, 3]
    assert len(stored(import_accounts["a"])) == 3


@pytest.mark.anyio
async def test_org_isolation_and_account_scoped_identity(client_a_admin, client_b_admin, import_accounts, seed_data):
    foreign = await upload(client_a_admin, import_accounts["b"], dry_run=False)
    assert foreign.status_code == 404, foreign.text
    assert stored(import_accounts["b"]) == []
    for tenant, client in [("a", client_a_admin), ("b", client_b_admin)]:
        result = await upload(client, import_accounts[tenant], dry_run=False)
        assert result.status_code == 200, result.text
        assert result.json()["imported_count"] == 3
        assert all(row[0] == seed_data[f"org_{tenant}_id"] for row in stored(import_accounts[tenant]))


@pytest.mark.anyio
async def test_write_roles_and_unauthenticated_access(client_a_readonly, client_unauth, client_a_clerk, import_accounts):
    for client, expected in [(client_unauth, 401), (client_a_readonly, 403)]:
        response = await upload(client, import_accounts["a"], dry_run=False)
        assert response.status_code == expected, response.text
    assert stored(import_accounts["a"]) == []
    response = await upload(client_a_clerk, import_accounts["a"], dry_run=False)
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 3


@pytest.mark.anyio
@pytest.mark.parametrize("content,filename", [(b"", "empty.csv"), (b"garbage", "unknown.csv"), (CSV + b"broken;last;line\n", "invalid.csv"), (b"<broken>", "bad.xml"), (CSV, "unknown.txt")])
async def test_invalid_import_is_atomic(client_a_admin, import_accounts, content, filename):
    response = await upload(client_a_admin, import_accounts["a"], content=content, filename=filename, dry_run=False)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]
    assert stored(import_accounts["a"]) == []


@pytest.mark.anyio
async def test_dkb_preamble_cannot_target_another_iban(client_b_admin, import_accounts):
    response = await upload(
        client_b_admin, import_accounts["b"],
        content=(FIXTURES / "dkb_2023.csv").read_bytes(), dry_run=False,
    )
    assert response.status_code == 422, response.text
    assert "IBAN" in response.json()["detail"]
    assert stored(import_accounts["b"]) == []


@pytest.mark.anyio
@pytest.mark.parametrize("filename", ["statement.xml", "statement.zip"])
async def test_camt_xml_and_zip_import_via_api(client_a_admin, import_accounts, filename):
    from tests.engine.test_bank_import_camt import archive, document
    content = document()
    if filename.endswith(".zip"):
        content = archive([("statement.xml", content)])
    result = await upload(client_a_admin, import_accounts["a"], content=content, filename=filename, dry_run=False)
    assert result.status_code == 200, result.text
    assert result.json()["imported_count"] == 3
    assert Decimal(result.json()["total_amount"]) == Decimal("649.99")
    repeated = await upload(client_a_admin, import_accounts["a"], content=content, filename=filename, dry_run=False)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["new_count"] == repeated.json()["imported_count"] == 0
    assert len(stored(import_accounts["a"])) == 3


@pytest.mark.anyio
async def test_late_insert_error_rolls_back_all_rows(client_a_admin, import_accounts, monkeypatch):
    from app.engine.bank_import import BankImportError
    from app.repositories.bank_transaction_repo import BankTransactionRepository
    original = BankTransactionRepository.import_rows

    async def fail_after_first_insert(self, account_id, rows):
        await original(self, account_id, rows[:1])
        raise BankImportError("Synthetic error after first insert")

    monkeypatch.setattr(BankTransactionRepository, "import_rows", fail_after_first_insert)
    result = await upload(client_a_admin, import_accounts["a"], dry_run=False)
    assert result.status_code == 422, result.text
    assert "after first insert" in result.json()["detail"]
    assert stored(import_accounts["a"]) == []


@pytest.mark.anyio
async def test_large_import_batches_and_bounded_preview(client_a_admin, import_accounts):
    content = ("Buchungstag;Wertstellung;Betrag\n" + "02.01.2026;03.01.2026;0,01\n" * 1001).encode()
    result = await upload(client_a_admin, import_accounts["a"], content=content, dry_run=False)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["count"] == data["imported_count"] == 1001
    assert data["preview_truncated"] is True
    assert len(data["transactions"]) == 100
    assert Decimal(data["total_amount"]) == Decimal("10.01")
    persisted = stored(import_accounts["a"])
    assert len(persisted) == 1001
    assert sum((row[1] for row in persisted), Decimal(0)) == Decimal("10.01")
    repeated = await upload(client_a_admin, import_accounts["a"], content=content, dry_run=False)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["duplicate_count"] == 1001
    assert repeated.json()["imported_count"] == 0


@pytest.mark.anyio
async def test_oversized_file_rejected_before_parse(client_a_admin, import_accounts, monkeypatch):
    monkeypatch.setattr("app.api.bank_accounts.MAX_IMPORT_BYTES", 16)
    result = await upload(client_a_admin, import_accounts["a"], dry_run=False)
    assert result.status_code == 413, result.text
    assert stored(import_accounts["a"]) == []


@pytest.mark.anyio
async def test_missing_account_not_found(client_a_admin):
    result = await upload(client_a_admin, str(uuid.uuid4()), dry_run=False)
    assert result.status_code == 404, result.text


@pytest.mark.anyio
async def test_transaction_list_pagination_preserves_all_rows(client_a_admin, import_accounts):
    account = import_accounts["a"]
    assert (await upload(client_a_admin, account, dry_run=False)).status_code == 200
    items = []
    offset = 0
    while offset is not None:
        response = await client_a_admin.get(
            f"/api/bank-accounts/{account}/transactions", params={"limit": 1, "offset": offset},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) == 1
        items.extend(page["items"])
        offset = page["next_offset"]
        assert len(items) <= 3
    assert [row["amount"] for row in items] == ["-200.01", "150.00", "700.00"]
    assert len({row["id"] for row in items}) == 3
    assert all(row["bank_account_id"] == account and row["status"] == "NEU" for row in items)


@pytest.mark.anyio
async def test_transaction_list_status_filter(client_a_admin, import_accounts):
    account = import_accounts["a"]
    assert (await upload(client_a_admin, account, dry_run=False)).status_code == 200
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE bank_transaction SET status='IGNORIERT' WHERE bank_account_id=%s AND amount=-200.01", (account,))
    for status, amounts in [("NEU", ["150.00", "700.00"]), ("IGNORIERT", ["-200.01"]), ("ZUGEORDNET", [])]:
        response = await client_a_admin.get(f"/api/bank-accounts/{account}/transactions", params={"status": status})
        assert response.status_code == 200, response.text
        assert [row["amount"] for row in response.json()["items"]] == amounts
        assert response.json()["next_offset"] is None


@pytest.mark.anyio
async def test_transaction_list_read_permissions(client_a_admin, client_a_readonly, client_unauth, import_accounts):
    for client, account, expected in [(client_a_admin, "b", 404), (client_a_readonly, "a", 200), (client_unauth, "a", 401)]:
        response = await client.get(f"/api/bank-accounts/{import_accounts[account]}/transactions")
        assert response.status_code == expected, response.text


@pytest.mark.anyio
@pytest.mark.parametrize("params", [{"status": "INVALID"}, {"limit": 101}, {"offset": -1}])
async def test_transaction_list_rejects_invalid_filters(client_a_admin, import_accounts, params):
    response = await client_a_admin.get(f"/api/bank-accounts/{import_accounts['a']}/transactions", params=params)
    assert response.status_code == 422, response.text


@pytest.mark.anyio
async def test_raw_upload_limit_protects_new_import_path():
    from app.core.uploads import RequestBodyLimitMiddleware
    assert RequestBodyLimitMiddleware._is_upload_scope({
        "type": "http", "method": "POST", "path": f"/api/bank-accounts/{uuid.uuid4()}/imports",
    })


@pytest.mark.anyio
async def test_imported_reserve_payout_stays_draft_until_both_references_exist(client_a_admin, import_accounts):
    api = client_a_admin
    account = import_accounts['a']
    assert (await upload(api, account, dry_run=False)).status_code == 200
    page = await api.get('/api/bank-transactions', params={'bank_account_id': account})
    outgoing = next(row for row in page.json()['items'] if Decimal(row['amount']) < 0)
    assigned = await api.post(f"/api/bank-transactions/{outgoing['id']}/assign", json={'lines': [{
        'payment_type': 'RUECKLAGENENTNAHME', 'amount': '200.01', 'period_year': 2026, 'period_month': 1}]})
    assert assigned.status_code == 200, assigned.text
    draft = assigned.json()['payments'][0]
    assert draft['status'] == 'draft'
    refused = await api.post(f"/api/payments/{draft['id']}/book")
    assert refused.status_code == 422, refused.text
    assert refused.json()['detail']['code'] == 'RESERVE_PAYOUT_EVIDENCE_REQUIRED'
    discarded = await api.delete(f"/api/payments/{draft['id']}")
    assert discarded.status_code == 409, discarded.text
    assert discarded.json()['detail']['code'] == 'RESERVE_IMPORTED_DRAFT_BOUND'
    for changes in ({'amount': '1.00'}, {'direction': 'IN'}, {'value_date': '2026-01-08'}):
        changed = await api.patch(f"/api/payments/{draft['id']}", json=changes)
        assert changed.status_code == 409, changed.text
        assert changed.json()['detail']['code'] == 'RESERVE_IMPORTED_DRAFT_BOUND'
