"""T20: pure proposals against real derived Mietkonto open items, exact cents."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import uuid

import pytest

from app.engine.bank_import import BankImportRow
from app.engine.bank_matching import BankMatchingError, MatchTarget, suggest_matches
from app.engine.mietkonto import (
    LeaseChargeInput, OwnerAdvanceChargeInput, PaymentInput, account_statement,
    owner_expected_charges, tenant_expected_charges,
)

LEASE = uuid.UUID(int=1)
TENANT = uuid.UUID(int=2)
OWNER = uuid.UUID(int=3)
IBAN = "DE02120300000000202051"


@pytest.mark.parametrize('purpose', ['Heizkosten Januar 2026', 'Heizung 01/2026', 'HK 01/2026', 'Warmmiete 01/2026'])
def test_heating_hints_and_warm_rent_include_the_heating_component(purpose):
    charges = tenant_expected_charges(LeaseChargeInput(LEASE, date(2026, 1, 1), None,
        Decimal('750'), Decimal('150'), heating_advance_payment=Decimal('90')),
        date(2026, 1, 1), date(2026, 1, 31))
    target = replace(tenant(), open_items=account_statement(charges, []).open_items)
    match, = suggest_matches(row('990' if purpose.startswith('Warm') else '90', purpose), [target])
    assert {line.payment_type for line in match.lines} == (
        {'KALTMIETE', 'NK_VORAUSZAHLUNG', 'HK_VORAUSZAHLUNG'} if purpose.startswith('Warm') else {'HK_VORAUSZAHLUNG'})


def row(amount="850.00", purpose="Warmmiete Januar 2026", **changes):
    return replace(BankImportRow(date(2026, 1, 2), date(2026, 1, 3), Decimal(amount) if isinstance(amount, str) else amount, "Max Müller", IBAN, purpose), **changes)


def tenant(*, payments=(), lease_id=LEASE, name="Max Müller", iban=IBAN, base="700.00", nk="150.00"):
    charges = tenant_expected_charges(
        LeaseChargeInput(lease_id, date(2026, 1, 1), None, Decimal(base), Decimal(nk)),
        date(2026, 1, 1), date(2026, 3, 31),
    )
    return MatchTarget(name=name, iban=iban, open_items=account_statement(charges, list(payments)).open_items,
                       lease_agreement_id=lease_id, tenant_profile_id=TENANT)


def test_warm_rent_exact_split_from_existing_soll():
    target = tenant()
    matches = suggest_matches(row(), [target])
    assert len(matches) == 1
    match = matches[0]
    assert match.confidence == "HIGH"
    assert not match.requires_review
    assert match.unallocated_amount == Decimal("0.00")
    assert [(line.payment_type, line.amount) for line in match.lines] == [("KALTMIETE", Decimal("700.00")), ("NK_VORAUSZAHLUNG", Decimal("150.00"))]
    assert all(line.lease_agreement_id == LEASE and line.tenant_profile_id == TENANT and line.owner_profile_id is None for line in match.lines)
    assert all((line.period_year, line.period_month) == (2026, 1) for line in match.lines)
    assert sum((line.amount for line in match.lines), Decimal(0)) == Decimal("850.00")
    assert suggest_matches(row(), [target]) == matches


def test_ambiguous_partial_rent_requires_explicit_split_no_invented_tilgung():
    match, = suggest_matches(row("600.00"), [tenant()])
    assert match.requires_review and match.confidence == "LOW"
    assert [line.open_amount for line in match.lines] == [Decimal("700.00"), Decimal("150.00")]
    assert all(line.amount is None for line in match.lines)
    assert match.unallocated_amount == Decimal("600.00")


def test_explicit_partial_component_is_exact():
    match, = suggest_matches(row("600.00", "Kaltmiete 01/2026"), [tenant()])
    assert [(line.payment_type, line.amount) for line in match.lines] == [("KALTMIETE", Decimal("600.00"))]
    assert match.requires_review and match.confidence == "MEDIUM"
    assert match.unallocated_amount == Decimal("0.00")


def test_foreign_known_iban_blocks_even_matching_name_and_amount():
    assert suggest_matches(row(counterparty_iban="DE12500105170648489890"), [tenant()]) == ()


def test_name_only_is_not_high_confidence():
    match, = suggest_matches(row(counterparty_iban=None, counterparty_name="MÜLLER, MAX"), [tenant()])
    assert match.confidence == "MEDIUM"
    assert match.lines[0].amount == Decimal("700.00")


def test_iban_whitespace_and_case_are_normalized():
    match, = suggest_matches(row(counterparty_iban="de02 1203 0000 0000 2020 51"), [tenant()])
    assert match.confidence == "HIGH"


def test_unrelated_person_does_not_match_by_amount_alone():
    assert suggest_matches(row(counterparty_name="Andere Person", counterparty_iban=None), [tenant()]) == ()


def test_paid_components_are_not_proposed_again():
    paid = PaymentInput(uuid.UUID(int=10), "KALTMIETE", "IN", Decimal("700.00"), 2026, 1, lease_agreement_id=LEASE)
    match, = suggest_matches(row("150.00"), [tenant(payments=[paid])])
    assert [(line.payment_type, line.amount) for line in match.lines] == [("NK_VORAUSZAHLUNG", Decimal("150.00"))]
    paid_nk = replace(paid, payment_id=uuid.UUID(int=11), payment_type="NK_VORAUSZAHLUNG", amount=Decimal("150.00"))
    assert suggest_matches(row(), [tenant(payments=[paid, paid_nk])]) == ()


def test_owner_hausgeld_uses_existing_owner_soll():
    charges = owner_expected_charges([OwnerAdvanceChargeInput(OWNER, None, Decimal("300.00"))], OWNER, date(2026, 1, 1), date(2026, 1, 31))
    target = MatchTarget("Erika Eigentümer", IBAN, account_statement(charges, []).open_items, owner_profile_id=OWNER)
    match, = suggest_matches(row("300.00", "Hausgeld 01/2026", counterparty_name="Erika Eigentümer"), [target])
    assert match.confidence == "HIGH"
    line, = match.lines
    assert line.payment_type == "HAUSGELD_VORSCHUSS" and line.amount == Decimal("300.00")
    assert line.owner_profile_id == OWNER and line.lease_agreement_id is None


@pytest.mark.parametrize("purpose", ["Miete Februar 2026", "Miete 02/2026", "Miete 2026-02"])
def test_explicit_month_overrides_bank_value_month(purpose):
    match, = suggest_matches(row(purpose=purpose), [tenant()])
    assert all((line.period_year, line.period_month) == (2026, 2) for line in match.lines)


def test_value_month_fallback_requires_review():
    match, = suggest_matches(row(purpose="Miete"), [tenant()])
    assert match.requires_review and match.confidence == "MEDIUM"
    assert all(line.period_month == 1 for line in match.lines)


def test_conflicting_explicit_months_are_not_guessed():
    assert suggest_matches(row(purpose="Miete 01/2026 und 02/2026"), [tenant()]) == ()


@pytest.mark.parametrize("amount", ["0.00", "-850.00", "1000.00"])
def test_no_arbitrary_income_allocation_for_zero_debit_or_overpayment(amount):
    assert suggest_matches(row(amount), [tenant()]) == ()


@pytest.mark.parametrize("amount", [Decimal("NaN"), Decimal("1.001"), 850.0])
def test_invalid_money_is_rejected_not_rounded(amount):
    with pytest.raises(BankMatchingError):
        suggest_matches(row(amount=amount), [tenant()])


def test_shared_identity_is_deterministic_but_never_high_confidence():
    targets = [tenant(lease_id=uuid.UUID(int=9)), tenant()]
    first = suggest_matches(row(), targets)
    assert first == suggest_matches(row(), list(reversed(targets)))
    assert len(first) == 2
    assert all(match.requires_review and match.confidence == "MEDIUM" for match in first)


def test_cent_values_remain_exact_without_rounding():
    match, = suggest_matches(row(), [tenant(base="700.01", nk="149.99")])
    assert [line.amount for line in match.lines] == [Decimal("700.01"), Decimal("149.99")]


@pytest.mark.parametrize("purpose", ["Kaution Januar 2026", "Rückbuchung Miete Januar 2026", "Kaution2026", "Storno12345 Miete01/2026"])
def test_non_rent_or_reversal_purpose_is_not_reinterpreted_as_rent(purpose):
    assert suggest_matches(row(purpose=purpose), [tenant()]) == ()


def test_mixed_rent_and_owner_purpose_requires_manual_target_split():
    assert suggest_matches(row(purpose="Warmmiete und Hausgeld Januar 2026"), [tenant()]) == ()


def test_duplicate_target_identity_is_not_a_second_candidate():
    target = tenant()
    with pytest.raises(BankMatchingError):
        suggest_matches(row(), [target, target])


def test_iban_match_with_different_name_requires_review():
    match, = suggest_matches(row(counterparty_name="Andere Person"), [tenant()])
    assert match.requires_review and match.confidence == "MEDIUM"


def test_no_automatic_allocation_to_oldest_month():
    target = tenant()
    target = replace(target, open_items=tuple(item for item in target.open_items if item.period_month == 1))
    assert suggest_matches(row(purpose="Miete", value_date=date(2026, 2, 3)), [target]) == ()


def test_invalid_mixed_target_is_rejected():
    with pytest.raises(BankMatchingError):
        suggest_matches(row(), [replace(tenant(), owner_profile_id=OWNER)])
