"""T25: separate deposit liability, real payment lifecycle and source access."""
from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from tests.test_rent_changes import rent_db, rent_world, api_client, statement  # noqa: F401

pytestmark = pytest.mark.asyncio
URL = "/api/payments/deposits"


@pytest_asyncio.fixture
async def deposit_world(rent_world):
    w = rent_world
    async with api_client(w) as api:
        response = await api.patch(f"/api/leases/{w['lease']}", json={"deposit": "1800.00"})
        assert response.status_code == 200, response.text
        for key, kind in (("bank", "KAUTION"), ("rent_bank", "LAUFEND")):
            response = await api.post("/api/bank-accounts", json={
                "property_id": str(w["property"]), "account_type": kind, "label": key,
            })
            assert response.status_code == 201, response.text
            w[key] = response.json()["id"]
    return w


def payment_body(w, **overrides):
    return {
        "bank_account_id": str(w["bank"]), "direction": "IN", "amount": "900.00",
        "value_date": "2026-02-05", "payment_type": "KAUTION",
        "tenant_profile_id": str(w["tenant"]), "lease_agreement_id": str(w["lease"]),
        **overrides,
    }


async def pay(api, w, *, book=True, **overrides):
    response = await api.post("/api/payments", json=payment_body(w, **overrides))
    assert response.status_code == 201, response.text
    row = response.json()
    if book:
        response = await api.post(f"/api/payments/{row['id']}/book")
        assert response.status_code == 200, response.text
        row = response.json()
    return row


async def overview(api, w, **params):
    response = await api.get(URL, params=params)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert isinstance(rows, list)
    return next(row for row in rows if row["lease_agreement_id"] == str(w["lease"]))


async def test_expected_deposit_before_first_payment(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        row = await overview(api, w)
    assert row == {
        "lease_agreement_id": str(w["lease"]), "tenant_profile_id": str(w["tenant"]),
        "unit_id": str(w["unit"]), "property_id": str(w["property"]),
        "tenant_name": "Synthetic T23 tenant", "unit_label": "1",
        "property_name": "T23 isolated rental", "expected_amount": "1800.00",
        "paid_amount": "0.00", "refunded_amount": "0.00", "held_amount": "0.00",
    }


async def test_deposit_payment_never_changes_rent_statement(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        before = await statement(api, w)
        await pay(api, w)
        after = await statement(api, w)
    assert after == before


def engine_payment(amount="900.00", direction="IN", **flags):
    from app.engine.mietkonto import PaymentInput
    return PaymentInput(payment_id=uuid.uuid4(), payment_type="KAUTION", direction=direction,
                        amount=Decimal(amount), period_year=None, period_month=None, **flags)


async def test_pure_deposit_two_installments_and_refund_frozen_exact():
    from app.engine.mietkonto import deposit_statement, account_statement
    payments = [engine_payment(), engine_payment()]
    result = deposit_statement(Decimal("1800"), payments)
    assert (result.expected_amount, result.paid_amount, result.refunded_amount, result.held_amount) == (
        Decimal("1800.00"), Decimal("1800.00"), Decimal("0.00"), Decimal("1800.00"))
    payments.append(engine_payment("300.00", "OUT"))
    result = deposit_statement(Decimal("1800"), payments)
    assert (result.paid_amount, result.refunded_amount, result.held_amount) == (
        Decimal("1800.00"), Decimal("300.00"), Decimal("1500.00"))
    with pytest.raises(FrozenInstanceError):
        result.held_amount = Decimal("0")
    # Generic engine is deliberately NOT globally stripped of KAUTION.
    assert account_statement([], payments).ist_total == Decimal("1500.00")


@pytest.mark.parametrize("flag", ["is_booked", "is_reversed", "is_reversal"])
@pytest.mark.parametrize("direction", ["IN", "OUT"])
async def test_pure_deposit_excludes_inactive_rows(flag, direction):
    from app.engine.mietkonto import deposit_statement
    flags = {flag: flag != "is_booked"}
    result = deposit_statement(Decimal("1800"), [engine_payment(direction=direction, **flags)])
    assert (result.paid_amount, result.refunded_amount, result.held_amount) == (Decimal("0.00"),) * 3


async def test_pure_deposit_cent_exact_no_interest_no_refund_ceiling():
    from dataclasses import replace
    from app.engine.mietkonto import deposit_statement
    payments = [engine_payment("0.10"), engine_payment("0.20"), engine_payment("0.31", "OUT"),
                replace(engine_payment("1000"), payment_type="NK_VORAUSZAHLUNG")]
    result = deposit_statement(Decimal("0.00"), payments)
    assert (result.expected_amount, result.paid_amount, result.refunded_amount, result.held_amount) == (
        Decimal("0.00"), Decimal("0.30"), Decimal("0.31"), Decimal("-0.01"))


async def test_persistent_installments_refund_draft_and_both_storno_directions(deposit_world, rent_db):
    w = deposit_world
    async with api_client(w) as api:
        original = (await api.get("/api/leases", params={"unit_id": str(w["unit"])})).json()
        before = await statement(api, w)
        installments = [await pay(api, w, value_date="2025-12-15"),
                        await pay(api, w, bank_account_id=w["rent_bank"], value_date="2027-01-15")]
        row = await overview(api, w)
        assert (row["paid_amount"], row["refunded_amount"], row["held_amount"]) == ("1800.00", "0.00", "1800.00")
        refund = await pay(api, w, amount="300.00", direction="OUT")
        await pay(api, w, amount="19.99", book=False)
        await pay(api, w, amount="21.01", direction="OUT", book=False)
        for direction in ("IN", "OUT"):
            cancelled = await pay(api, w, amount="42.01", direction=direction)
            response = await api.post(f"/api/payments/{cancelled['id']}/reverse", json={"note": "T25 correction"})
            assert response.status_code == 201, response.text
        assert await statement(api, w) == before
        assert (await api.get("/api/leases", params={"unit_id": str(w["unit"])})).json() == original
    async with api_client(w) as reloaded:
        row = await overview(reloaded, w)
        assert (row["expected_amount"], row["paid_amount"], row["refunded_amount"], row["held_amount"]) == (
            "1800.00", "1800.00", "300.00", "1500.00")
        payments = (await reloaded.get("/api/payments", params={"lease_agreement_id": str(w["lease"])})).json()
        assert len(payments) == 9
        assert sum(p["reversed_by_id"] is not None for p in payments) == 2
        assert sum(p["reverses_id"] is not None for p in payments) == 2
    for payment in [*installments, refund]:
        actions = (await rent_db.execute(text("SELECT action FROM audit_event WHERE entity_id=:id ORDER BY occurred_at"),
                                        {"id": uuid.UUID(payment["id"])})).scalars().all()
        assert actions == ["payment.created", "payment.booked"]


async def test_zero_expected_activity_negative_held_and_empty_leases(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        response = await api.patch(f"/api/leases/{w['lease']}", json={"deposit": "0.00"})
        assert response.status_code == 200, response.text
        assert (await api.get(URL)).json() == []
        await pay(api, w, amount="0.10")
        await pay(api, w, amount="0.20")
        await pay(api, w, amount="0.31", direction="OUT")
        row = await overview(api, w)
        assert (row["expected_amount"], row["paid_amount"], row["refunded_amount"], row["held_amount"]) == (
            "0.00", "0.30", "0.31", "-0.01")


async def test_rent_types_unchanged_by_deposit_separation(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        await pay(api, w, bank_account_id=w["rent_bank"], payment_type="KALTMIETE", amount="700.00", period_year=2026, period_month=2)
        await pay(api, w, bank_account_id=w["rent_bank"], payment_type="NK_VORAUSZAHLUNG", amount="150.00", period_year=2026, period_month=2)
        before = await statement(api, w)
        assert before["ist_total"] == "850.00"
        await pay(api, w)
        assert await statement(api, w) == before
        assert (await overview(api, w))["paid_amount"] == "900.00"


async def test_readonly_can_read_not_create_book_reverse_and_org_isolated(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        booked = await pay(api, w)
        draft = await pay(api, w, book=False)
    async with api_client(w, "reader") as reader:
        assert (await overview(reader, w))["paid_amount"] == "900.00"
        assert (await reader.post("/api/payments", json=payment_body(w))).status_code == 403
        assert (await reader.post(f"/api/payments/{draft['id']}/book")).status_code == 403
        assert (await reader.post(f"/api/payments/{booked['id']}/reverse", json={})).status_code == 403
    async with api_client(w, foreign=True) as foreign:
        assert (await foreign.get(URL)).json() == []
        assert (await foreign.get(f"/api/payments/{booked['id']}")).status_code == 404
        assert (await foreign.post("/api/payments", json=payment_body(w))).status_code == 404


async def scoped_world(db, w, *, whole=False):
    w["scope"] = uuid.uuid4()
    w["weg_scope"] = uuid.uuid4()
    for key, kind in (("scope", "MIET"), ("weg_scope", "WEG")):
        await db.execute(text("""INSERT INTO accounting_scope
            (id,organization_id,property_id,scope_type,label,valid_from,valid_to,whole_property,origin,created_by)
            VALUES (:id,:org,:property,:kind,'T25 reviewed scope','2025-01-01','2027-12-31',:whole,'REVIEWED',:admin)"""),
            {**w, "id": w[key], "kind": kind, "whole": whole})
        if not whole:
            await db.execute(text("""INSERT INTO accounting_scope_unit(organization_id,scope_id,unit_id,property_id)
                VALUES (:org,:scope,:unit,:property)"""), {**w, "scope": w[key]})
        for actor, caps in (("admin", ["read", "prepare", "confirm"]), ("reader", ["read"])):
            await db.execute(text("""INSERT INTO scope_grant(organization_id,scope_id,user_id,capabilities,granted_by,reason)
                VALUES (:org,:scope,:user,:caps,:admin,'T25 explicit scope grant')"""),
                {**w, "scope": w[key], "user": w[actor], "caps": caps})
    for key in ("bank", "rent_bank"):
        await db.execute(text("""INSERT INTO accounting_scope_binding
            (organization_id,property_id,scope_id,bank_account_id,origin,reason,reviewed_by)
            VALUES (:org,:property,:scope,:bank,'REVIEWED','T25 reviewed account',:admin)"""),
            {**w, "bank": uuid.UUID(w[key])})
    await db.commit()


async def test_exact_miet_scope_includes_deposit_and_running_accounts_lifetime(deposit_world, rent_db):
    w = deposit_world
    # Retained lifetime rows span multiple years within a valid multi-year scope.
    async with api_client(w) as api:
        await pay(api, w, value_date="2025-12-15")
        await pay(api, w, bank_account_id=w["rent_bank"], value_date="2027-01-15")
        await pay(api, w, amount="13.00", book=False)
    await scoped_world(rent_db, w)
    # Reviewed retained payments, including drafts, belong to the exact account sphere.
    rows = (await rent_db.execute(text("SELECT id,bank_account_id FROM payment WHERE organization_id=:org"), w)).all()
    for payment_id, bank_id in rows:
        await rent_db.execute(text("""INSERT INTO accounting_scope_binding
            (organization_id,property_id,scope_id,payment_id,payment_bank_account_id,origin,reason,reviewed_by)
            VALUES (:org,:property,:scope,:payment,:bank_id,'REVIEWED','T25 retained payment',:admin)"""),
            {**w, "payment": payment_id, "bank_id": bank_id})
    await rent_db.commit()
    async with api_client(w) as api:
        refund = await pay(api, w, amount="300.00", direction="OUT")
        correction = await pay(api, w, amount="10.00")
        response = await api.post(f"/api/payments/{correction['id']}/reverse", json={})
        assert response.status_code == 201, response.text
        discarded = await pay(api, w, amount="17.01", book=False)
        assert (await api.delete(f"/api/payments/{discarded['id']}")).status_code == 204
        assert (await api.post(f"/api/payments/{discarded['id']}/book")).status_code == 409
    assert await rent_db.scalar(text("SELECT scope_id FROM accounting_scope_binding WHERE payment_id=:id"),
                                {"id": uuid.UUID(refund["id"])}) == w["scope"]
    await rent_db.commit()
    async with api_client(w, "reader") as reader:
        assert (await reader.get(URL)).status_code == 409
        row = await overview(reader, w, accounting_scope_id=str(w["scope"]))
        assert row["paid_amount"] == "1800.00"
        assert row["refunded_amount"] == "300.00"
        assert row["held_amount"] == "1500.00"
        assert row["expected_amount"] == "1800.00"
        assert (await reader.get(URL, params={"accounting_scope_id": str(w["weg_scope"])})).status_code == 422
        assert (await reader.get(URL, params={"accounting_scope_id": str(uuid.uuid4())})).status_code == 404
    async with api_client(w, "clerk") as clerk, api_client(w, foreign=True) as foreign:
        for api in (clerk, foreign):
            denied = await api.get(URL, params={"accounting_scope_id": str(w["scope"])})
            assert denied.status_code == 404, denied.text
            assert str(w["tenant"]) not in denied.text
    # No cached rights: revocation is effective on the next request.
    await rent_db.execute(text("UPDATE scope_grant SET revoked_at=now(),revoked_by=:admin WHERE organization_id=:org AND scope_id=:scope AND user_id=:reader"), w)
    await rent_db.commit()
    async with api_client(w, "reader") as reader:
        assert (await reader.get(URL, params={"accounting_scope_id": str(w["scope"])})).status_code == 404


async def test_scope_requires_classified_inputs_not_partial_zero(deposit_world, rent_db):
    w = deposit_world
    await scoped_world(rent_db, w)
    await rent_db.execute(text("INSERT INTO bank_account(organization_id,property_id,account_type,label) VALUES (:org,:property,'KAUTION','Unreviewed')"), w)
    await rent_db.commit()
    async with api_client(w) as api:
        response = await api.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 409, response.text


async def test_partial_scope_excludes_unrelated_lease_but_keeps_unpaid_expected(deposit_world, rent_db):
    w = deposit_world
    extra = uuid.uuid4()
    await rent_db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:extra,:org,:property,'Excluded T25','WOHNUNG')"), {**w, "extra": extra})
    await rent_db.commit()
    async with api_client(w) as api:
        response = await api.post("/api/leases", json={"unit_id": str(extra), "tenant_profile_id": str(w["tenant"]),
            "valid_from": "2026-01-01", "base_rent": "100.00", "advance_payment": "0.00", "deposit": "77.00"})
        assert response.status_code == 201, response.text
    await scoped_world(rent_db, w)
    async with api_client(w, "reader") as reader:
        response = await reader.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 200, response.text
        assert [row["lease_agreement_id"] for row in response.json()] == [str(w["lease"])]
        assert response.json()[0]["expected_amount"] == "1800.00"


async def enforce_sources(api, w):
    from tests.test_source_security_lifecycle import accepted, status, transition, transition_body
    grants = {}
    for kind, keys in (("property", ("property", "hidden_property")), ("person", ("person", "hidden_person"))):
        for key in keys:
            body = {f"{kind}_id": str(w[key])}
            body.update({"label": "T25 property audience", "reason": "Explicit source rights"}
                        if kind == "property" else {"reason_code": "INITIAL_SETUP"})
            response = await api.post(f"/api/{kind}-data-domains", json=body,
                                      headers={"Idempotency-Key": str(uuid.uuid4())})
            assert response.status_code == 201, response.text
            domain_id = response.json()["id"]
            for actor in (("admin", "reader") if not key.startswith("hidden_") else ("admin",)):
                body = {"user_id": str(w[actor]), "capabilities": ["read"]}
                body.update({"reason": "T25 explicit source reader"} if kind == "property"
                            else {"reason_code": "TEAM_ACCESS"})
                response = await api.post(f"/api/{kind}-data-domains/{domain_id}/grants", json=body,
                                          headers={"Idempotency-Key": str(uuid.uuid4())})
                assert response.status_code == 201, response.text
                if actor == "reader":
                    grants[kind] = (domain_id, response.json()["id"])
    decision = await accepted(api)
    response = await transition(api, transition_body(await status(api), "ENFORCED", decision))
    assert response.status_code == 201, response.text
    return grants


@pytest.mark.parametrize("revoked_source", ["person", "property"])
async def test_enforced_property_and_label_sources_fail_closed(deposit_world, rent_db, revoked_source):
    w = deposit_world
    async with api_client(w) as api:
        response = await api.patch(f"/api/leases/{w['hidden_lease']}", json={"deposit": "2600.00"})
        assert response.status_code == 200, response.text
    await scoped_world(rent_db, w)
    async with api_client(w) as admin:
        grants = await enforce_sources(admin, w)
    async with api_client(w, "reader") as reader:
        response = await reader.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 200, response.text
        assert len(response.json()) == 1
        assert response.json()[0]["tenant_name"] == "Synthetic T23 tenant"
        assert response.json()[0]["expected_amount"] == "1800.00"
        assert str(w["hidden_lease"]) not in response.text
        assert str(w["hidden_tenant"]) not in response.text
        assert str(w["hidden_property"]) not in response.text
        assert (await reader.get(URL)).status_code == 404  # strict mode needs an exact scope
        assert (await reader.post("/api/payments", json=payment_body(w))).status_code == 403
    domain_id, grant_id = grants[revoked_source]
    async with api_client(w) as admin:
        response = await admin.post(f"/api/{revoked_source}-data-domains/{domain_id}/grants/{grant_id}/revoke")
        assert response.status_code == 204, response.text
    async with api_client(w, "reader") as reader:
        response = await reader.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 404, response.text
        assert "Synthetic" not in response.text
        assert "1800.00" not in response.text


async def test_missing_payment_is_404_and_unauthenticated_deposits_are_401(deposit_world):
    from app.main import app
    import httpx
    async with api_client(deposit_world) as api:
        assert (await api.get(f"/api/payments/{uuid.uuid4()}")).status_code == 404
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as anonymous:
        assert (await anonymous.get(URL)).status_code == 401


async def test_deposits_wait_for_existing_financial_input_lock(deposit_world, rent_db):
    import asyncio
    w = deposit_world
    await rent_db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                          {"key": f"financial-inputs:{w['org']}"})
    async with api_client(w) as api:
        task = asyncio.create_task(api.get(URL))
        try:
            await asyncio.sleep(0.1)
            assert not task.done()
            await rent_db.rollback()
            response = await asyncio.wait_for(task, timeout=5)
            assert response.status_code == 200, response.text
        finally:
            await rent_db.rollback()
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.parametrize("field,key,status", [
    ("lease_agreement_id", "foreign_lease", 404),
    ("lease_agreement_id", "missing", 404),
    ("lease_agreement_id", "hidden_lease", 422),
    ("tenant_profile_id", "hidden_tenant", 422),
    ("tenant_profile_id", "foreign_tenant", 404),
])
async def test_new_deposit_payment_requires_coherent_visible_sources(deposit_world, field, key, status):
    w = deposit_world
    async with api_client(w) as api:
        body = payment_body(w, **{field: str(w[key]) if key != "missing" else str(uuid.uuid4())})
        response = await api.post("/api/payments", json=body)
        assert response.status_code == status, response.text
        assert (await api.get("/api/payments", params={"lease_agreement_id": str(w["lease"])})).json() == []


async def test_deposit_draft_cannot_be_repointed_to_other_lease(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        draft = await pay(api, w, book=False)
        response = await api.patch(f"/api/payments/{draft['id']}", json={"lease_agreement_id": str(w["hidden_lease"])})
        assert response.status_code == 422, response.text
        persisted = await api.get(f"/api/payments/{draft['id']}")
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["lease_agreement_id"] == str(w["lease"])


async def test_original_lease_documents_and_finalized_snapshot_unchanged(deposit_world, rent_db):
    from tests.financial_coverage_helpers import attest_fixture
    w = deposit_world
    await rent_db.execute(text("""INSERT INTO cost_entry
        (id,organization_id,property_id,cost_category_id,cost_category_plan_version,
         invoice_date,service_period_from,service_period_to,gross_amount,
         tenant_distribution_key,tenant_apportionable,status,source,confirmed_at,confirmed_by)
        SELECT :id,:org,:property,id,1,'2026-04-01','2026-01-01','2026-04-30',1200,
               'WOHNFLAECHE','UMLAGEFAEHIG','confirmed','MANUAL',now(),:admin
        FROM cost_category WHERE code='GRUNDSTEUER' AND plan_version=1"""), {**w, "id": uuid.uuid4()})
    await rent_db.execute(text("""INSERT INTO property_document
        (id,organization_id,property_id,doc_type,title,original_filename,mime_type,
         content_byte_size,content_sha256,storage_uri,storage_backend,page_count)
        VALUES (:id,:org,:property,'DECLARATION','T25 retained metadata','original.pdf',
                'application/pdf',4,:sha,'synthetic/t25/original.pdf','LOCAL_FS',1)"""),
        {**w, "id": uuid.uuid4(), "sha": "a" * 64})
    await rent_db.commit()
    async with api_client(w) as api:
        await pay(api, w, bank_account_id=w["rent_bank"], payment_type="NK_VORAUSZAHLUNG", amount="150.00", period_year=2026, period_month=3)
        response = await api.post("/api/settlements", json={
            'heating_advance_payments_total': '0.00', "property_id": str(w["property"]), "lease_agreement_id": str(w["lease"]),
            "period_start": "2026-01-01", "period_end": "2026-04-30", "advance_payments_total": "150.00"})
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        await attest_fixture(api, "settlement", run_id, {
            'heating_advance_payments_total': '0.00', 'heating_advance_payments_source': "MANUAL", "advance_payments_total": "150.00", "advance_payments_source": "MANUAL"})
        response = await api.post(f"/api/settlements/{run_id}/finalize", json={})
        assert response.status_code == 200, response.text
        finalized = response.json()
        assert finalized["snapshot_sha256"]
        original = await rent_db.scalar(text("SELECT to_jsonb(l) FROM lease_agreement l WHERE id=:lease"), w)
        documents = (await rent_db.execute(text("SELECT to_jsonb(d) FROM property_document d WHERE organization_id=:org ORDER BY id"), w)).scalars().all()
        assert len(documents) == 1
        snapshot = await rent_db.scalar(text("SELECT to_jsonb(r) FROM settlement_run r WHERE id=:id"), {"id": uuid.UUID(run_id)})
        await rent_db.commit()
        before = await statement(api, w)
        await pay(api, w)
        await pay(api, w)
        await pay(api, w, amount="300.00", direction="OUT")
        assert (await overview(api, w))["held_amount"] == "1500.00"
        assert await statement(api, w) == before
        assert (await api.get(f"/api/settlements/{run_id}")).json() == finalized
    assert await rent_db.scalar(text("SELECT to_jsonb(l) FROM lease_agreement l WHERE id=:lease"), w) == original
    assert (await rent_db.execute(text("SELECT to_jsonb(d) FROM property_document d WHERE organization_id=:org ORDER BY id"), w)).scalars().all() == documents
    assert await rent_db.scalar(text("SELECT to_jsonb(r) FROM settlement_run r WHERE id=:id"), {"id": uuid.UUID(run_id)}) == snapshot


async def test_past_lease_retained_and_same_unit_contracts_not_combined(deposit_world):
    w = deposit_world
    async with api_client(w) as api:
        response = await api.post("/api/leases", json={
            "unit_id": str(w["unit"]), "tenant_profile_id": str(w["tenant"]),
            "valid_from": "2025-01-01", "valid_to": "2025-12-31", "base_rent": "600.00",
            "advance_payment": "100.00", "deposit": "1000.00"})
        assert response.status_code == 201, response.text
        old_lease = response.json()["id"]
        await pay(api, w, lease_agreement_id=old_lease, amount="1000.00", value_date="2025-01-02")
        await pay(api, w, lease_agreement_id=old_lease, amount="300.00", direction="OUT", value_date="2026-02-15")
        response = await api.get(URL)
        assert response.status_code == 200, response.text
        rows = {row["lease_agreement_id"]: row for row in response.json()}
        assert set(rows) == {old_lease, str(w["lease"])}
        assert rows[old_lease]["paid_amount"] == "1000.00"
        assert rows[old_lease]["held_amount"] == "700.00"
        assert rows[str(w["lease"])]["expected_amount"] == "1800.00"
        assert rows[str(w["lease"])]["paid_amount"] == "0.00"


async def test_one_denied_lease_source_cannot_disappear_from_whole_property_report(deposit_world, rent_db):
    w = deposit_world
    extra = uuid.uuid4()
    await rent_db.execute(text("INSERT INTO unit(id,organization_id,property_id,number,usage_type) VALUES (:extra,:org,:property,'Other contact','WOHNUNG')"), {**w, "extra": extra})
    await rent_db.commit()
    async with api_client(w) as admin:
        response = await admin.post("/api/leases", json={
            "unit_id": str(extra), "tenant_profile_id": str(w["hidden_tenant"]),
            "valid_from": "2026-01-01", "base_rent": "100.00", "advance_payment": "0.00", "deposit": "700.00"})
        assert response.status_code == 201, response.text
        extra_lease = response.json()["id"]
    await scoped_world(rent_db, w, whole=True)
    async with api_client(w) as admin:
        await enforce_sources(admin, w)
        response = await admin.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 200, response.text
        assert {row["lease_agreement_id"] for row in response.json()} == {str(w["lease"]), extra_lease}
    async with api_client(w, "reader") as reader:
        assert (await reader.get(f"/api/persons/{w['hidden_person']}")).status_code == 404
        response = await reader.get(URL, params={"accounting_scope_id": str(w["scope"])})
        assert response.status_code == 404, response.text
        assert "1800.00" not in response.text
        assert "700.00" not in response.text


async def test_retained_unallocated_deposit_is_not_silent_zero_but_can_be_reversed(deposit_world, rent_db):
    w = deposit_world
    # Retained historical shape admitted by the existing schema, not a new draft.
    original = uuid.uuid4()
    await rent_db.execute(text("""INSERT INTO payment
        (id,organization_id,property_id,bank_account_id,tenant_profile_id,
         direction,amount,value_date,payment_type,status)
        VALUES (:id,:org,:property,:bank,:tenant,'IN',10,'2026-02-01','KAUTION','booked')"""),
        {**w, "id": original, "bank": uuid.UUID(w["bank"])})
    await rent_db.commit()
    async with api_client(w) as api:
        assert (await api.get(URL)).status_code == 409
        response = await api.post(f"/api/payments/{original}/reverse", json={"note": "Correct retained allocation"})
        assert response.status_code == 201, response.text
        assert response.json()["reverses_id"] == str(original)
        row = await overview(api, w)
        assert (row["paid_amount"], row["refunded_amount"], row["held_amount"]) == ("0.00", "0.00", "0.00")
