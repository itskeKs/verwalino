"""Anlage V mapping is an exact, conservative aid over an existing report."""
from datetime import date
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest

from app.engine.anlage_v import (
    ANLAGE_V_LINES, DEFAULT_CATEGORY_MAPPING, CategoryMapping,
    build_category_mapping, map_property_year,
)
from app.services.report_service import PropertyYearReport


def year_report(income=(), expenses=()):
    income = [dict(payment_type=code, amount=amount) for code, amount in income]
    expenses = [dict(category_id=uuid4(), category_code=code, category_name=f"Category {code}",
                     tenant_apportionability="MANUELL_PRUEFEN", amount=amount) for code, amount in expenses]
    with localcontext() as context:
        context.prec = 100
        received = sum((Decimal(row['amount']) for row in income), Decimal('0.00'))
        spent = sum((Decimal(row['amount']) for row in expenses), Decimal('0.00'))
        return PropertyYearReport(property_id=uuid4(), property_name='Sonnenhof', year=2025,
            period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), accounting_scope_id=None,
            income=income, expenses=expenses, income_total=f'{received:.2f}',
            expense_total=f'{spent:.2f}', balance=f'{received - spent:.2f}')


def amounts(summary):
    return {row.key: row.amount for row in summary.lines}


def test_landlord_sees_income_and_expenses_on_distinct_lines():
    summary = map_property_year(year_report(
        [('KALTMIETE', '1234.56'), ('NK_VORAUSZAHLUNG', '150.00')],
        [('KALTWASSER', '20.01'), ('ENTWAESSERUNG', '30.02'), ('BUCHHALTUNG', '45.67')]))
    assert amounts(summary)['rent'] == '1234.56'
    assert amounts(summary)['levies'] == '150.00'
    assert amounts(summary)['water'] == '50.03'
    assert amounts(summary)['administration'] == '45.67'
    assert summary.unmapped == []
    assert all(row.difference == '0.00' for row in summary.reconciliation)


def test_unknown_categories_are_listed_with_original_labels_and_signed_amounts():
    summary = map_property_year(year_report(expenses=[('FUTURE_CATEGORY', '-12.34')]))
    row, = summary.unmapped
    assert (row.code, row.label, row.amount, row.section) == (
        'FUTURE_CATEGORY', 'Category FUTURE_CATEGORY', '-12.34', 'Werbungskosten')
    assert '[§/fachlich]' in row.note
    assert summary.reconciliation[1].unmapped_total == '-12.34'


@pytest.mark.parametrize('code,line', [
    ('KALTMIETE', 'rent'), ('NK_VORAUSZAHLUNG', 'levies'), ('HK_VORAUSZAHLUNG', 'levies'),
    ('KAUTION', None), ('HAUSGELD_VORSCHUSS', None), ('RUECKLAGENANTEIL', None),
    ('SONDERZAHLUNG', None), ('NACHZAHLUNG', None), ('OTHER', None),
])
def test_income_types_do_not_turn_deposits_or_owner_funds_into_rent(code, line):
    summary = map_property_year(year_report(income=[(code, '10.01')]))
    if line:
        assert amounts(summary)[line] == '10.01'
        assert summary.unmapped == []
    else:
        assert summary.unmapped[0].code == code
        assert summary.unmapped[0].amount == '10.01'
        assert all(row.amount == '0.00' for row in summary.lines)
    assert summary.reconciliation[0].report_total == '10.01'
    assert summary.reconciliation[0].difference == '0.00'


@pytest.mark.parametrize('code', ['ERHALTUNGSRUECKLAGE', 'SONDERUMLAGE'])
def test_reserve_and_special_levy_costs_require_evidence_of_actual_expenditure(code):
    summary = map_property_year(year_report(expenses=[(code, '1000.00')]))
    assert summary.unmapped[0].code == code
    assert all(row.amount == '0.00' for row in summary.lines)


def test_totals_reconcile_to_every_report_cent_even_with_low_outer_precision():
    report = year_report(
        [('KALTMIETE', '123456789012345678901234.91'), ('NK_VORAUSZAHLUNG', '-0.09'),
         ('KAUTION', '0.01'), ('UNKNOWN', '-0.02')],
        [('KALTWASSER', '0.01'), ('ENTWAESSERUNG', '0.02'), ('NEW', '-0.01')])
    with localcontext() as context:
        context.prec = 6
        summary = map_property_year(report)
    with localcontext() as context:
        context.prec = 100
        for row, total in zip(summary.reconciliation, [report.income_total, report.expense_total]):
            mapped = sum((Decimal(line.amount) for line in summary.lines if line.section == row.section), Decimal(0))
            unmapped = sum((Decimal(item.amount) for item in summary.unmapped if item.section == row.section), Decimal(0))
            assert mapped + unmapped == Decimal(total)
            assert Decimal(row.mapped_total) == mapped
            assert Decimal(row.unmapped_total) == unmapped
            assert row.difference == '0.00'
    assert amounts(summary)['water'] == '0.03'


def test_an_empty_year_shows_zero_lines_and_no_claim_of_tax_completeness():
    summary = map_property_year(year_report())
    assert len(summary.lines) == len(ANLAGE_V_LINES)
    assert all(row.amount == '0.00' for row in summary.lines)
    assert summary.unmapped == []
    assert all(row.report_total == row.mapped_total == row.unmapped_total == row.difference == '0.00'
               for row in summary.reconciliation)
    assert 'keine Steuerberatung' in summary.footnote


def test_inconsistent_report_totals_are_refused_instead_of_claiming_reconciliation():
    report = year_report(income=[('KALTMIETE', '10.00')])
    with pytest.raises(ValueError, match='reconcile'):
        map_property_year(report.model_copy(update={'income_total': '11.00'}))


def test_table_construction_refuses_one_category_assigned_to_two_lines():
    with pytest.raises(ValueError, match='Duplicate'):
        build_category_mapping([
            CategoryMapping('KALTWASSER', 'water', '[§/fachlich] water'),
            CategoryMapping('KALTWASSER', 'other', '[§/fachlich] other'),
        ])


def test_table_construction_refuses_unknown_lines_and_missing_judgement_notes():
    with pytest.raises(ValueError, match='Unknown'):
        build_category_mapping([CategoryMapping('NEW', 'missing', '[§/fachlich] check')])
    with pytest.raises(ValueError, match='note'):
        build_category_mapping([CategoryMapping('NEW', 'other', '')])
    with pytest.raises(TypeError):
        DEFAULT_CATEGORY_MAPPING['NEW'] = CategoryMapping('NEW', 'other', '[§/fachlich] check')


def test_import_rejects_a_duplicate_category_before_the_module_can_be_used():
    """Exercise the actual import-time table construction, not just its helper."""
    from pathlib import Path
    import subprocess
    import sys

    source = Path('app/engine/anlage_v.py').read_text()
    marker = 'DEFAULT_CATEGORY_MAPPING = build_category_mapping('
    assert marker in source
    source = source.replace(marker, marker + "[CategoryMapping('KALTWASSER', 'other', '[§/fachlich] duplicate')] + ", 1)
    result = subprocess.run([sys.executable, '-c', source], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Duplicate' in result.stderr


def test_every_seeded_global_category_has_an_explicit_default_or_review_assignment():
    import importlib

    seed = importlib.import_module('migrations.versions.0005_cost_category').SEED_CATEGORIES
    assert {row['code'] for row in seed} <= DEFAULT_CATEGORY_MAPPING.keys()


def test_documentation_renders_the_complete_line_and_mapping_tables_without_drift():
    from pathlib import Path

    labels = {line.key: line.label for line in ANLAGE_V_LINES}
    expected = {
        'lines': [
            '| Key | German detail label | Section |', '| --- | --- | --- |',
            *(f'| `{line.key}` | {line.label} | {line.section} |' for line in ANLAGE_V_LINES),
        ],
        'mapping': [
            '| Category / income code | German detail label | Judgement note |', '| --- | --- | --- |',
            *(f'| `{row.code}` | {labels.get(row.line_key, "Nicht zugeordnet")} | {row.note} |'
              for row in DEFAULT_CATEGORY_MAPPING.values()),
        ],
    }
    document = (Path(__file__).resolve().parents[3] / 'docs/m6-zahlungen-mietkonto.md').read_text()
    for table, rows in expected.items():
        section = document.split(f'<!-- anlage-v-{table}:start -->\n', 1)[1].split(f'\n<!-- anlage-v-{table}:end -->', 1)[0]
        assert section.splitlines() == rows
