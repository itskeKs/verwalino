"""Isolation sweep: probes and seed for bank accounts and transactions, payments, SEPA, special levies, settlements, economic plans, WEG settlements, cost entries, invoice documents and reports."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.route_isolation import Probe, row
from tests.test_financial_scope_boundary import scope_seed
from tests.test_invoice_extraction_worker import worker_tenant
from tests.test_invoice_source_custody import store, upload
from tests.test_m3_settlement import _create_run, _seed_m3
from tests.test_m4_weg import _create_and_finalize_plan, _seed_weg
from tests.test_scope_grants import bind, scope_fixture
from tests.test_sepa_export import IBAN as SEPA_IBAN
from tests.test_sepa_export import configure as configure_sepa

# Imported by the sweep test with *: the seed fixture and every fixture it requests that
# conftest.py does not provide.
__all__ = ["finance_seeded", "scope_seed", "store", "worker_tenant"]

STATEMENT = Path(__file__).resolve().parents[1] / "fixtures" / "bank" / "dkb_2023.csv"
STATEMENT_IBAN = "DE02120300000000202051"


@pytest.fixture
async def finance_seeded(sweep, worker_tenant, scope_seed, store, migrator_session):
    """Finance records in one fresh organization (neither B nor the shared organization A).

    A fresh organization keeps these rows away from the suites that count rows of organization A
    (test_financial_scope_boundary.scope_seed does the same). It holds the M3 rental and M4 WEG
    graphs, a draft settlement, a finalized plan with a WEG settlement and an asset report, bank
    accounts with an imported statement, rental and Hausgeld payments, a SEPA creditor and mandate,
    an uploaded invoice and accounting scopes.
    """
    api, _ = worker_tenant
    rental, weg = _seed_m3(scope_seed), _seed_weg(scope_seed)

    async def post(url, status=201, **kwargs):
        answer = await api.post(url, **kwargs)
        assert answer.status_code == status, f"seed {url}: {answer.status_code} {answer.text[:300]}"
        return answer.json()

    await _create_run(api, rental, "lease_a", "600.00")
    plan = await _create_and_finalize_plan(api, weg)
    period = {"period_start": "2025-01-01", "period_end": "2025-12-31"}
    await post("/api/weg-settlements", json={"property_id": weg["property"], "economic_plan_id": plan, **period})
    await post("/api/weg-settlements/asset-reports", json={"property_id": weg["property"], **period})

    statement_account = await post("/api/bank-accounts", json={
        "property_id": rental["property"], "account_type": "LAUFEND", "label": "Statement", "iban": STATEMENT_IBAN})
    await post(f"/api/bank-accounts/{statement_account['id']}/imports", status=200, params={"dry_run": "false"},
        files={"file": ("dkb_2023.csv", STATEMENT.read_bytes(), "application/octet-stream")})
    await post("/api/payments", json={
        "bank_account_id": statement_account["id"], "direction": "IN", "amount": "800.00", "value_date": "2025-02-03",
        "payment_type": "KALTMIETE", "tenant_profile_id": rental["tp_a"], "lease_agreement_id": rental["lease_a"],
        "period_year": 2025, "period_month": 2})

    # An owner payment, so that an owner statement that leaked would not be empty.
    await post("/api/payments", json={
        "bank_account_id": weg["bank_giro"], "direction": "IN", "amount": "250.00", "value_date": "2025-02-03",
        "payment_type": "HAUSGELD_VORSCHUSS", "owner_profile_id": weg["owner_a"], "period_year": 2025, "period_month": 2})

    # SEPA needs a tenant contact with IBAN and address, an operating account and a creditor.
    profiles = (await api.get("/api/tenant-profiles")).json()
    person = next(p["person_id"] for p in profiles if p["id"] == rental["tp_a"])
    answer = await api.put(f"/api/persons/{person}", json={
        "iban": SEPA_IBAN, "street": "Testweg 1", "zip_code": "12345", "city": "Teststadt"})
    assert answer.status_code == 200, answer.text
    sepa_account = await post("/api/bank-accounts", json={
        "property_id": rental["property"], "account_type": "LAUFEND", "label": "SEPA", "iban": SEPA_IBAN})
    await configure_sepa(api, {"bank": sepa_account["id"], "tenant": rental["tp_a"]})

    await upload(api)

    # Accounting scopes last: they bind nothing, but a scoped property would change the creates above.
    await scope_fixture(migrator_session, scope_seed)
    # B's own classified whole-property WEG scope: without one, B's unfiltered special-levy list is
    # refused and the sweep could not read it to scan for foreign levies. B's administrator holds the grant.
    own = await scope_fixture(migrator_session, {"org_a_id": sweep.b, "user_a_admin_id": sweep.admin, "user_a_clerk_id": sweep.admin})
    await bind(migrator_session, own)
    await migrator_session.commit()  # also commits the clerk user of scope_seed


def other(select: str, tables: str = "", where: str = "") -> str:
    """SQL for one record of another organization than B over joined tables; the first is ``x``."""
    return (f"SELECT {select} FROM {tables} WHERE x.organization_id IS DISTINCT FROM :b {where} "
            "ORDER BY x.id LIMIT 1")


def run(run_type: str, select: str = "x.id AS run_id", joins: str = "") -> str:
    return other(select, f'"settlement_run" x {joins}', f"AND x.run_type = '{run_type}'")


def with_sibling(parent: str, parent_id: str, sibling: str, select: str) -> str:
    """A ``parent`` record (``x``, id named ``parent_id``) with a ``sibling`` record (``s``) of the same organization."""
    return other(f"x.id AS {parent_id}, {select}", f'"{parent}" x JOIN "{sibling}" s ON s.organization_id = x.organization_id')


BK, WEG, REPORT = "BK_MIETER", "WEG_JAHRESABRECHNUNG", "VERMOEGENSBERICHT"
WEG_PLAN = "JOIN economic_plan p ON p.id = x.economic_plan_id AND p.organization_id = x.organization_id"
PROPERTY = row("property", property_id="id")
TENANT_PAYMENT = other("x.bank_account_id, x.lease_agreement_id, x.tenant_profile_id", '"payment" x',
                       "AND x.lease_agreement_id IS NOT NULL AND x.tenant_profile_id IS NOT NULL")
SCOPE = row("accounting_scope", accounting_scope_id="id", property_id="property_id")
PERIOD = {"period_start": "2025-01-01", "period_end": "2025-12-31"}
NO_MATCH = "a filter by another organization's id matches nothing"

PLAN_INPUTS = {"planned_costs": [], "reserve_contribution_total": "0.00"}
WEG_INPUTS = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00", "paid_advances": []}
REPORT_INPUTS = {"reserve_override_reason": "Reviewed synthetic reserve totals", "reserve_opening_balance": "0.00", "reserve_withdrawals_total": "0.00",
                 "reserve_contributions_total": "0.00", "bank_balances": [], "receivables": [], "payables": []}
PAYMENT = {"direction": "IN", "amount": "10.00", "value_date": "2025-02-03", "payment_type": "KALTMIETE"}
CREDITOR = {"creditor_id": "DE98ZZZ09999999999", "name": "Isolation", "country": "DE", "town": "Teststadt",
            "bank_authorized": True}
MANDATE = {"tenant_profile_id": "{tenant_profile_id}", "reference": "ISOLATION-1", "signed_on": "2026-01-01",
           "sequence_type": "FRST", "last_used_on": None, "debtor_country": "DE", "confirmed_iban": SEPA_IBAN,
           "original_reference": "Isolation original", "mandate_confirmed": True}
SEPA_RUN = {"bank_account_id": "{bank_account_id}", "year": 2026, "month": 10, "collection_date": "2026-10-30",
            "message_id": "ISOLATION-1", "pre_notification_on": "2026-10-15", "notice_period_days": 14,
            "shorter_period_agreed": False, "pre_notification_confirmed": True, "bank_profile_confirmed": True}
DATEV = {"property_id": "{property_id}", "period_start": "2025-01-01", "period_end": "2025-03-31",
         "fiscal_year_start": "2025-01-01", "chart": "SKR03", "advisor_number": "1001", "client_number": "1",
         "account_length": 4, "bank_accounts": {}, "payment_accounts": {}, "category_accounts": {},
         "gross_accounts_reviewed": True}


PROBES: dict[str, Probe] = {
    # app/api/bank_accounts.py
    "GET /api/bank-accounts": Probe(PROPERTY, status=200, because=NO_MATCH),
    "GET /api/bank-accounts/{account_id}": Probe(row("bank_account", account_id="id")),
    "GET /api/bank-accounts/{account_id}/transactions": Probe(row("bank_account", account_id="id")),
    "PATCH /api/bank-accounts/{account_id}": Probe(row("bank_account", account_id="id")),
    "POST /api/bank-accounts": Probe(PROPERTY, body={"property_id": "{property_id}", "account_type": "LAUFEND", "label": "Isolation"}),
    "POST /api/bank-accounts/{account_id}/imports": Probe(row("bank_account", account_id="id")),
    # app/api/bank_transactions.py
    "GET /api/bank-transactions": Probe(row("bank_account", bank_account_id="id")),
    "POST /api/bank-transactions/{transaction_id}/assign": Probe(
        with_sibling("bank_transaction", "transaction_id", "lease_agreement", "s.id AS lease_agreement_id"),
        body={"lines": [{"payment_type": "KALTMIETE", "amount": "10.00", "period_year": 2025, "period_month": 1,
                         "lease_agreement_id": "{lease_agreement_id}"}]}),
    "POST /api/bank-transactions/{transaction_id}/ignore": Probe(row("bank_transaction", transaction_id="id")),
    # app/api/cost_entries.py
    "GET /api/cost-entries": Probe(PROPERTY, status=200, because=NO_MATCH),
    "GET /api/cost-entries/export.csv": Probe(PROPERTY),
    "GET /api/cost-entries/{entry_id}": Probe(row("cost_entry", entry_id="id")),
    "PATCH /api/cost-entries/{entry_id}": Probe(row("cost_entry", entry_id="id")),
    "POST /api/cost-entries": Probe(PROPERTY, body={
        "property_id": "{property_id}", "cost_category_id": "00000000-0000-4000-8000-000000000001",
        "invoice_date": "2025-06-01", "service_period_from": "2025-01-01", "service_period_to": "2025-12-31",
        "gross_amount": "10.00", "tenant_apportionable": "UMLAGEFAEHIG"}),
    "POST /api/cost-entries/{entry_id}/confirm": Probe(row("cost_entry", entry_id="id")),
    "POST /api/cost-entries/{entry_id}/correction": Probe(row("cost_entry", entry_id="id")),
    "POST /api/cost-entries/{entry_id}/reject": Probe(row("cost_entry", entry_id="id")),
    # app/api/economic_plans.py
    "GET /api/economic-plans": Probe(PROPERTY, status=200, because=NO_MATCH),
    "GET /api/economic-plans/{plan_id}": Probe(row("economic_plan", plan_id="id")),
    "GET /api/economic-plans/{plan_id}/pdf": Probe(row("economic_plan", plan_id="id")),
    "POST /api/economic-plans": Probe(PROPERTY, body={"property_id": "{property_id}", "plan_year": 2025, **PERIOD}),
    "POST /api/economic-plans/previous": Probe(PROPERTY, body={
        "property_id": "{property_id}", "plan_year": 2025, "expected_source_digest": "0" * 64,
        "planned_costs": [], "reserve_contribution_total": "0.00"}),
    "POST /api/economic-plans/previous/preview": Probe(PROPERTY, body={"property_id": "{property_id}", "plan_year": 2025}),
    "POST /api/economic-plans/{plan_id}/compute": Probe(row("economic_plan", plan_id="id"), body=PLAN_INPUTS),
    "POST /api/economic-plans/{plan_id}/correction": Probe(row("economic_plan", plan_id="id")),
    "POST /api/economic-plans/{plan_id}/finalize": Probe(row("economic_plan", plan_id="id"), body=PLAN_INPUTS),
    # app/api/financial_input_coverage.py
    "GET /api/financial-input-coverage/source-options": Probe(row("settlement_run", subject_id="id")),
    "POST /api/financial-input-coverage": Probe(row("settlement_run", subject_id="id")),
    # app/api/invoice_documents.py
    "GET /api/invoice-documents": Probe(row("cost_entry", cost_entry_id="id"), status=200, because=NO_MATCH),
    "GET /api/invoice-documents/{doc_id}": Probe(row("invoice_document", doc_id="id")),
    "GET /api/invoice-documents/{doc_id}/content": Probe(row("invoice_document", doc_id="id")),
    "GET /api/invoice-documents/{doc_id}/processing-attempts": Probe(row("invoice_document", doc_id="id")),
    "GET /api/invoice-documents/{doc_id}/verify": Probe(row("invoice_document", doc_id="id")),
    "POST /api/invoice-documents/upload": Probe(row("cost_entry", cost_entry_id="id")),
    "POST /api/invoice-documents/{doc_id}/retry": Probe(row("invoice_document", doc_id="id")),
    "POST /api/invoice-documents/{doc_id}/version": Probe(row("invoice_document", doc_id="id")),
    # app/api/payments.py
    "DELETE /api/payments/{payment_id}": Probe(row("payment", payment_id="id")),
    "GET /api/payments": Probe(TENANT_PAYMENT, status=200, because=NO_MATCH),
    "GET /api/payments/deposits": Probe(SCOPE),
    "GET /api/payments/export.csv": Probe(TENANT_PAYMENT),
    "GET /api/payments/mietkonto/lease/{lease_agreement_id}": Probe(row("lease_agreement", lease_agreement_id="id")),
    "GET /api/payments/mietkonto/owner/{owner_profile_id}": Probe(
        row("owner_profile", owner_profile_id="id"), status=200,
        because="in preparation mode the owner is never looked up: the statement is built from the visible "
                "payments, so it is empty for a foreign and for a random id alike"),
    "GET /api/payments/open-items": Probe(SCOPE),
    "GET /api/payments/open-items/lease/{lease_agreement_id}": Probe(row("lease_agreement", lease_agreement_id="id")),
    "GET /api/payments/{payment_id}": Probe(row("payment", payment_id="id")),
    "PATCH /api/payments/{payment_id}": Probe(row("payment", payment_id="id")),
    "POST /api/payments": Probe(row("bank_account", bank_account_id="id"), body={"bank_account_id": "{bank_account_id}", **PAYMENT}),
    "POST /api/payments/{payment_id}/book": Probe(row("payment", payment_id="id")),
    "POST /api/payments/{payment_id}/reverse": Probe(row("payment", payment_id="id")),
    # app/api/reports.py
    "GET /api/reports/dashboard-kpis": Probe(PROPERTY),
    "GET /api/reports/datev/sources": Probe(PROPERTY),
    "GET /api/reports/property-year": Probe(PROPERTY),
    "GET /api/reports/property-year.csv": Probe(PROPERTY),
    "GET /api/reports/property-year/anlage-v": Probe(PROPERTY),
    "GET /api/reports/property-year/anlage-v.csv": Probe(PROPERTY),
    "POST /api/reports/datev.csv": Probe(PROPERTY, body={**DATEV, "expected_source_digest": "0" * 64}),
    "POST /api/reports/datev/preview": Probe(PROPERTY, body=DATEV),
    # app/api/sepa.py
    "GET /api/payments/sepa/accounts/{bank_id}": Probe(row("bank_account", bank_id="id")),
    "POST /api/payments/sepa.xml": Probe(row("bank_account", bank_account_id="id"), body={**SEPA_RUN, "expected_source_digest": "0" * 64}),
    "POST /api/payments/sepa/accounts/{bank_id}/mandates": Probe(
        with_sibling("bank_account", "bank_id", "tenant_profile", "s.id AS tenant_profile_id"), body=MANDATE),
    "POST /api/payments/sepa/mandates/{mandate_id}/revoke": Probe(row("sepa_mandate", mandate_id="id")),
    "POST /api/payments/sepa/preview": Probe(row("bank_account", bank_account_id="id"), body=SEPA_RUN),
    "PUT /api/payments/sepa/accounts/{bank_id}/creditor": Probe(row("bank_account", bank_id="id"), body=CREDITOR),
    "PUT /api/payments/sepa/mandates/{mandate_id}": Probe(row("sepa_mandate", mandate_id="id"), body={"sequence_type": "FRST", "last_used_on": None}),
    # app/api/settlements.py
    "GET /api/settlements": Probe(PROPERTY, status=200, because=NO_MATCH),
    "GET /api/settlements/{run_id}": Probe(run(BK)),
    "GET /api/settlements/{run_id}/advance-proposal": Probe(run(BK)),
    "GET /api/settlements/{run_id}/consumption-suggestions": Probe(run(BK)),
    "GET /api/settlements/{run_id}/heating-consumption": Probe(
        run(BK, "x.id AS run_id, c.id AS cost_entry_id", "JOIN cost_entry c ON c.organization_id = x.organization_id")),
    "GET /api/settlements/{run_id}/pdf": Probe(run(BK)),
    "PATCH /api/settlements/{run_id}/advance-payments": Probe(run(BK), body={"advance_payments_total": "10.00", "heating_advance_payments_total": "20.00"}),
    "PATCH /api/settlements/{run_id}/advance-payments-derived": Probe(run(BK), query={"period_year": 2025}),
    "POST /api/settlements": Probe(
        other("u.property_id AS property_id, x.id AS lease_agreement_id",
              '"lease_agreement" x JOIN unit u ON u.id = x.unit_id AND u.organization_id = x.organization_id'),
        body={"property_id": "{property_id}", "lease_agreement_id": "{lease_agreement_id}", **PERIOD}),
    "POST /api/settlements/batch": Probe(PROPERTY, body={"property_id": "{property_id}", **PERIOD}),
    "POST /api/settlements/{run_id}/advance-proposal": Probe(run(BK), body={"valid_from": "2025-01-01"}),
    "POST /api/settlements/{run_id}/compute": Probe(run(BK)),
    "POST /api/settlements/{run_id}/correction": Probe(run(BK)),
    "POST /api/settlements/{run_id}/finalize": Probe(run(BK)),
    "POST /api/settlements/{run_id}/preview": Probe(run(BK)),
    # app/api/special_levies.py
    "GET /api/special-levies": Probe(SCOPE),
    "POST /api/special-levies": Probe(SCOPE, body={
        "property_id": "{property_id}", "accounting_scope_id": "{accounting_scope_id}", "amount": "100.00", "due_date": "2025-06-01"}),
    # app/api/weg_settlements.py
    "GET /api/weg-settlements": Probe(PROPERTY, status=200, because=NO_MATCH),
    "GET /api/weg-settlements/asset-reports/{run_id}/pdf": Probe(run(REPORT)),
    "GET /api/weg-settlements/{run_id}": Probe(run(WEG)),
    "GET /api/weg-settlements/{run_id}/economic-plan-successor": Probe(run(WEG)),
    "GET /api/weg-settlements/{run_id}/input-owners": Probe(run(WEG)),
    "GET /api/weg-settlements/{run_id}/reserve-ledger": Probe(run(WEG)),
    "POST /api/weg-settlements/{run_id}/reserve-ledger/overrides": Probe(run(WEG), body={
        'component': 'ADJUSTMENT', 'amount': '1.00', 'reason': 'Foreign reserve probe'}),
    "GET /api/weg-settlements/{run_id}/pdf": Probe(run(WEG)),
    "PATCH /api/weg-settlements/{run_id}/economic-plan": Probe(
        run(WEG, "x.id AS run_id, x.economic_plan_id AS expected_plan_id, x.economic_plan_id AS successor_id", WEG_PLAN),
        body={"expected_plan_id": "{expected_plan_id}", "successor_id": "{successor_id}", "reason": "Isolation", "confirmed": True}),
    "POST /api/weg-settlements": Probe(
        run(WEG, "x.property_id AS property_id, x.economic_plan_id AS economic_plan_id", WEG_PLAN),
        body={"property_id": "{property_id}", "economic_plan_id": "{economic_plan_id}", **PERIOD}),
    "POST /api/weg-settlements/asset-reports": Probe(PROPERTY, body={"property_id": "{property_id}", **PERIOD}),
    "POST /api/weg-settlements/asset-reports/{run_id}/compute": Probe(run(REPORT), body=REPORT_INPUTS),
    "POST /api/weg-settlements/asset-reports/{run_id}/finalize": Probe(run(REPORT), body=REPORT_INPUTS),
    "POST /api/weg-settlements/{run_id}/compute": Probe(run(WEG), body=WEG_INPUTS),
    "POST /api/weg-settlements/{run_id}/finalize": Probe(run(WEG), body=WEG_INPUTS),
    "POST /api/weg-settlements/{run_id}/paid-advances-derived": Probe(run(WEG)),
}

