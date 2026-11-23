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


JUNE = {"period_start": "2025-06-01", "period_end": "2025-06-30"}


async def lease_statement(api, ids, **period):
    """The rent account of the lease: the whole of 2025 unless a period is named."""
    response = await api.get(url(ids, "lease"), params={**query(ids), **period})
    assert response.status_code == 200, response.text
    return response.json()


def paid_positions(statement):
    """(month, payment type) -> Ist of the listed positions that hold money."""
    return {(row["period_month"], row["payment_type"]): row["ist"] for row in statement["open_items"] if row["ist"] != "0.00"}


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
        # Rent 500.00 and advance 100.00 a month since 2024; 2025 is the scope's year. Before the booking June 2025's rent
        # holds 100.00 (this scope's account) and 900.00 (the second scope's); the booking adds 400.00 rent and 100.00
        # advance for June. The rule of #806 (a surplus is set off within its calendar year and position type, the
        # period only selects what is listed) gives:
        #   June rent: 100 + 900 + 400 = 1400.00 against 500.00 owed, a surplus of 900.00. It settles the oldest due
        #   open rent of 2025: January 500.00 and 400.00 of February's 500.00. June rent keeps its own 500.00.
        #   June advance: 100.00 against 100.00 owed, nothing over, and no rent surplus is set off against it.
        # The June view lists those two positions: Soll 500 + 100 = 600.00, Ist 500 + 100 = 600.00, balance 0.00. The
        # 900.00 the second scope's account took for June stay one amount of their own, the money of the months the
        # period names (which part of June's Ist it settled is not tracked).
        june = await lease_statement(api, ids, **JUNE)
        assert (june["soll_total"], june["ist_total"], june["other_scope_ist"], june["balance"]) == (
            "600.00", "600.00", "900.00", "0.00")
        assert paid_positions(june) == {(6, "KALTMIETE"): "500.00", (6, "NK_VORAUSZAHLUNG"): "100.00"}
        # The whole of 2025 shows where the booking went: Soll 12 x 600.00 = 7200.00, Ist 100 + 900 + 400 + 100 =
        # 1500.00 (the lease's debt counts both scopes' accounts), balance 7200.00 - 1500.00 = 5700.00; January's
        # rent 500.00, February's 400.00, June's rent 500.00 and June's advance 100.00 hold it.
        year = await lease_statement(api, ids)
        assert (year["soll_total"], year["ist_total"], year["other_scope_ist"], year["balance"]) == (
            "7200.00", "1500.00", "900.00", "5700.00")
        assert paid_positions(year) == {(1, "KALTMIETE"): "500.00", (2, "KALTMIETE"): "400.00",
                                        (6, "KALTMIETE"): "500.00", (6, "NK_VORAUSZAHLUNG"): "100.00"}


async def test_prepare_without_confirm_cannot_book_but_can_ignore(migrator_session, scope_seed):
    ids = await bank_transactions(migrator_session, scope_seed)
    await grant(ids, ["read", "prepare"])
    async with client(ids) as api:
        denied = await api.post(f"/api/bank-transactions/{ids['transaction']}/assign", json=assignment(ids))
        assert denied.status_code == 404, denied.text
        unchanged = await api.get('/api/bank-transactions', params={"bank_account_id": str(ids["bank"])})
        assert unchanged.status_code == 200, unchanged.text
        assert [row["id"] for row in unchanged.json()["items"]] == [str(ids["transaction"])]
        # Nothing was booked: June 2025's rent holds the 100.00 of this scope's account and the 900.00 of the second
        # scope's, 1000.00 against 500.00 owed. The surplus of 500.00 settles the oldest due open rent of 2025,
        # January's 500.00 (#806). June rent keeps 500.00, June's advance (Soll 100.00) holds nothing.
        # June view: Soll 500 + 100 = 600.00, Ist 500.00, balance 100.00 (the advance). Had the second scope's 900.00
        # not counted for the lease, June would owe 400 + 100 = 500.00.
        june = await lease_statement(api, ids, **JUNE)
        assert (june["soll_total"], june["ist_total"], june["other_scope_ist"], june["balance"]) == (
            "600.00", "500.00", "900.00", "100.00")
        assert paid_positions(june) == {(6, "KALTMIETE"): "500.00"}
        # The whole of 2025: Soll 7200.00, Ist 100 + 900 = 1000.00 of both scopes' accounts, balance 6200.00; January's
        # rent 500.00 and June's rent 500.00 hold it, and nothing of the refused assignment is in it.
        year = await lease_statement(api, ids)
        assert (year["soll_total"], year["ist_total"], year["other_scope_ist"], year["balance"]) == (
            "7200.00", "1000.00", "900.00", "6200.00")
        assert paid_positions(year) == {(1, "KALTMIETE"): "500.00", (6, "KALTMIETE"): "500.00"}
        ignored = await api.post(f"/api/bank-transactions/{ids['transaction']}/ignore")
        assert ignored.status_code == 200, ignored.text
        assert ignored.json()["status"] == "IGNORIERT"


async def test_read_scope_cannot_receive_counterparty_validation_for_a_write(migrator_session, scope_seed):
    ids = await bank_transactions(migrator_session, scope_seed)
    await grant(ids, ["read"])
    async with client(ids) as api:
        response = await api.get(f"/api/bank-accounts/{ids['bank']}")
        assert response.status_code == 200, response.text
        response = await api.post("/api/payments", json={
            "bank_account_id": str(ids["bank"]), "direction": "IN", "amount": "75.25",
            "value_date": "2025-06-03", "payment_type": "KALTMIETE",
        })
        assert response.status_code == 404, response.text
