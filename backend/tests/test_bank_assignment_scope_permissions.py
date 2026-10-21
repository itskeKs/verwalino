"""T21 integrates existing exact-sphere read/prepare/confirm permissions."""
import uuid

import pytest
from sqlalchemy import text

from tests.test_financial_scope_boundary import scope_seed as scope_seed
from tests.test_financial_scope_boundary import worker_tenant as worker_tenant
from tests.test_scoped_account_statements import client, mutate, query, statement_fixture, url

pytestmark = pytest.mark.asyncio


async def bank_transactions(session, seed):
    ids = await statement_fixture(session, seed, "lease")
    ids.update(transaction=uuid.uuid4(), hidden_transaction=uuid.uuid4())
    for key, bank in (("transaction", "bank"), ("hidden_transaction", "second_bank")):
        await mutate(text("""INSERT INTO bank_transaction
          (id,organization_id,bank_account_id,booking_date,value_date,amount,counterparty_name,purpose,import_hash,status)
          VALUES (:tx,:org,:account,'2025-06-02','2025-06-03',500,'Statement Person',
                  'Warmmiete Juni 2025',:hash,'NEU')"""),
          {**ids, "tx": ids[key], "account": ids[bank], "hash": str(ids[key])})
    return ids


async def grant(ids, capabilities):
    await mutate(text("""UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor
      WHERE organization_id=:org AND scope_id=:scope AND user_id=:clerk AND revoked_at IS NULL"""), ids)
    await mutate(text("""INSERT INTO scope_grant
      (organization_id,scope_id,user_id,capabilities,granted_by,reason)
      VALUES (:org,:scope,:clerk,:caps,:actor,'Synthetic T21 exact permission test')"""),
      {**ids, "caps": capabilities})


def assignment(ids):
    # Existing June Soll 500+100; prior booked Kaltmiete100 leaves 400+100.
    return {"lines": [
        {"lease_agreement_id": str(ids["lease"]), "payment_type": kind,
         "amount": amount, "period_year": 2025, "period_month": 6}
        for kind, amount in (("KALTMIETE", "400.00"), ("NK_VORAUSZAHLUNG", "100.00"))
    ]}


@pytest.mark.parametrize("status", ["NEU", "IGNORIERT"])
async def test_global_list_hides_revoked_bank_scope_even_without_proposals(migrator_session, scope_seed, status):
    ids = await bank_transactions(migrator_session, scope_seed)
    await mutate(text("""UPDATE scope_grant SET revoked_at=now(),revoked_by=:actor
      WHERE organization_id=:org AND scope_id=:second_scope AND user_id=:clerk AND revoked_at IS NULL"""), ids)
    await mutate(text("UPDATE bank_transaction SET status=:status WHERE id IN (:transaction,:hidden_transaction)"),
                 {**ids, "status": status})
    async with client(ids) as api:
        response = await api.get('/api/bank-transactions', params={"status": status})
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [str(ids["transaction"])]
        assert (await api.get('/api/bank-transactions', params={"bank_account_id": str(ids["second_bank"])})).status_code == 404
        assert (await api.post(f"/api/bank-transactions/{ids['hidden_transaction']}/ignore")).status_code == 404
    async with client(ids, "actor") as admin:
        response = await admin.get('/api/bank-transactions', params={"status": status})
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []


async def test_prepare_and_confirm_without_approve_can_book_real_mietkonto(migrator_session, scope_seed):
    ids = await bank_transactions(migrator_session, scope_seed)
    await grant(ids, ["read", "prepare", "confirm"])
    async with client(ids) as api:
        response = await api.post(f"/api/bank-transactions/{ids['transaction']}/assign", json=assignment(ids))
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "ZUGEORDNET"
        payments = response.json()["payments"]
        assert len(payments) == 2
        assert {p["amount"] for p in payments} == {"400.00", "100.00"}
        assert all(p["status"] == "booked" and p["bank_transaction_id"] == str(ids["transaction"]) for p in payments)
        statement = await api.get(url(ids, "lease"), params={**query(ids), "period_start": "2025-06-01", "period_end": "2025-06-30"})
        assert statement.status_code == 200, statement.text
        assert statement.json()["soll_total"] == "600.00"
        assert statement.json()["ist_total"] == "600.00"
        assert statement.json()["balance"] == "0.00"


async def test_prepare_without_confirm_cannot_book_but_can_ignore(migrator_session, scope_seed):
    ids = await bank_transactions(migrator_session, scope_seed)
    await grant(ids, ["read", "prepare"])
    async with client(ids) as api:
        denied = await api.post(f"/api/bank-transactions/{ids['transaction']}/assign", json=assignment(ids))
        assert denied.status_code == 404, denied.text
        unchanged = await api.get('/api/bank-transactions', params={"bank_account_id": str(ids["bank"])})
        assert unchanged.status_code == 200, unchanged.text
        assert [row["id"] for row in unchanged.json()["items"]] == [str(ids["transaction"])]
        statement = await api.get(url(ids, "lease"), params={**query(ids), "period_start": "2025-06-01", "period_end": "2025-06-30"})
        assert statement.status_code == 200, statement.text
        assert statement.json()["ist_total"] == "100.00"
        ignored = await api.post(f"/api/bank-transactions/{ids['transaction']}/ignore")
        assert ignored.status_code == 200, ignored.text
        assert ignored.json()["status"] == "IGNORIERT"
