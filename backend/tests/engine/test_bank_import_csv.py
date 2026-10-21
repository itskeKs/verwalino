"""Synthetic statements; expected net: 1,234.56 - 200.01 = 1,034.55 EUR.

DKB headers are documented by beancount-dkb's EC extractors (see README).
No third-party transaction data or importer implementation is copied.
"""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine.bank_import import BankImportError, parse_csv

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bank"
HEADER = "Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck;Währung\n"
ROW = "02.01.2026;03.01.2026;1.234,56;Müller;DE02120300000000202051;Miete Januar;EUR\n"


def test_generic_statement_exact_values_and_optional_fields():
    rows = parse_csv((HEADER + ROW + "04.01.2026;05.01.2026;-200,01;;;;EUR\n").encode())
    assert len(rows) == 2
    assert rows[0].booking_date == date(2026, 1, 2)
    assert rows[0].value_date == date(2026, 1, 3)
    assert rows[0].amount == Decimal("1234.56")
    assert rows[0].counterparty_name == "Müller"
    assert rows[0].counterparty_iban == "DE02120300000000202051"
    assert rows[0].purpose == "Miete Januar"
    assert rows[1].counterparty_name is None
    assert rows[1].counterparty_iban is None
    assert rows[1].purpose is None
    assert sum((row.amount for row in rows), Decimal(0)) == Decimal("1034.55")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp1252"])
def test_encodings_produce_identical_rows_and_hashes(encoding):
    content = HEADER + ROW.replace("Müller", "Müller – Haus")
    assert parse_csv(content.encode(encoding)) == parse_csv(content.encode("utf-8"))


def test_dkb_legacy_profile_with_preamble_and_trailing_delimiter():
    rows = parse_csv((FIXTURES / "dkb_legacy.csv").read_bytes())
    assert len(rows) == 2
    assert rows[0].booking_date == date(2026, 1, 2)
    assert rows[0].value_date == date(2026, 1, 3)
    assert rows[0].counterparty_name == "Müller"
    # Legacy Kontonummer is NOT evidence of a counterparty IBAN.
    assert rows[0].counterparty_iban is None
    assert [row.amount for row in rows] == [Decimal("1234.56"), Decimal("-200.01")]


def test_dkb_2023_profile_selects_sender_or_recipient():
    rows = parse_csv((FIXTURES / "dkb_2023.csv").read_bytes())
    assert len(rows) == 2
    assert [row.counterparty_name for row in rows] == ["Müller", "Heizservice"]
    assert [row.amount for row in rows] == [Decimal("1234.56"), Decimal("-200.01")]
    assert rows[0].value_date == date(2026, 1, 3)
    assert rows[0].purpose == "Miete; Januar\nWohnung 1"


def test_column_order_whitespace_and_empty_lines_do_not_change_identity():
    reordered = (
        "\n Währung ; Verwendungszweck ; IBAN ; Name ; Betrag ; Wertstellung ; Buchungstag \n"
        " EUR ; Miete Januar ; DE02 1203 0000 0000 2020 51 ; Müller ; +1.234,56 ;03.01.2026;02.01.2026\n\n"
    )
    assert parse_csv(reordered.encode()) == parse_csv((HEADER + ROW).encode())


def test_identical_genuine_lines_are_preserved_but_reimport_is_stable():
    rows = parse_csv((HEADER + ROW * 2).encode())
    assert len(rows) == 2
    assert len({row.import_hash for row in rows}) == 2
    assert all(len(row.import_hash) == 64 for row in rows)
    assert rows == parse_csv((HEADER + ROW * 2).encode())
    assert rows[0].import_hash == parse_csv((HEADER + ROW).encode())[0].import_hash


@pytest.mark.parametrize("amount", ["1.234,567", "1,234.56", "12.34,56", "NaN", "Infinity", "1e2", "", "1000000000000,00"])
def test_invalid_amount_fails_with_line_number(amount):
    with pytest.raises(BankImportError, match=r"Zeile 2.*Betrag"):
        parse_csv((HEADER + ROW.replace("1.234,56", amount)).encode())


@pytest.mark.parametrize("date_value", ["31.02.2026", "2026-01-02", "01/02/2026", ""])
def test_invalid_or_ambiguous_date_fails(date_value):
    with pytest.raises(BankImportError, match=r"Zeile 2.*Buchungsdatum"):
        parse_csv((HEADER + ROW.replace("02.01.2026", date_value)).encode())


@pytest.mark.parametrize("content", [b"", b"\x00\x01", b"date,amount\n2026-01-01,100\n", b"Datum;Summe\n02.01.2026;10,00\n"])
def test_unknown_format_is_explained(content):
    with pytest.raises(BankImportError, match="[Ff]ormat|[Ll]eer|[Zz]eichen"):
        parse_csv(content)


def test_empty_recognized_statement_is_valid():
    assert parse_csv(HEADER.encode()) == []


def test_duplicate_semantic_columns_are_rejected():
    with pytest.raises(BankImportError, match="[Mm]ehrdeutig"):
        parse_csv((HEADER.rstrip() + ";Buchungsdatum\n" + ROW.rstrip() + ";02.01.2026\n").encode())


@pytest.mark.parametrize("content", [HEADER + ROW.rstrip() + ";extra\n", HEADER + "02.01.2026;03.01.2026\n", HEADER + '"unclosed\n'])
def test_malformed_record_is_not_silently_skipped(content):
    with pytest.raises(BankImportError, match="Zeile"):
        parse_csv(content.encode())


def test_foreign_currency_is_rejected_without_conversion():
    with pytest.raises(BankImportError, match=r"Zeile 2.*EUR"):
        parse_csv((HEADER + ROW.replace("EUR", "USD")).encode())


@pytest.mark.parametrize("replacement", ["Vorgemerkt", "Unbekannt"])
def test_dkb_unbooked_or_unknown_status_fails(replacement):
    content = (FIXTURES / "dkb_2023.csv").read_text(encoding="utf-8-sig")
    with pytest.raises(BankImportError, match="Status"):
        parse_csv(content.replace("Gebucht", replacement).encode())


def test_dkb_direction_must_agree_with_signed_amount():
    content = (FIXTURES / "dkb_2023.csv").read_text(encoding="utf-8-sig")
    with pytest.raises(BankImportError, match="Umsatztyp|Vorzeichen"):
        parse_csv(content.replace('"Eingang"', '"Ausgang"').encode())


def test_legacy_daily_balance_is_not_an_imported_transaction():
    content = (FIXTURES / "dkb_legacy.csv").read_text()
    content += '"04.01.2026";"04.01.2026";"Saldo";"";"Tagessaldo";"";"";"1.034,55";"";"";"";\n'
    rows = parse_csv(content.encode())
    assert len(rows) == 2
    assert sum((row.amount for row in rows), Decimal(0)) == Decimal("1034.55")


def test_generic_booking_named_daily_balance_is_not_dropped():
    rows = parse_csv((HEADER + ROW.replace("Miete Januar", "Tagessaldo")).encode())
    assert len(rows) == 1
    assert rows[0].amount == Decimal("1234.56")


def test_import_byte_limit(monkeypatch):
    monkeypatch.setattr("app.engine.bank_import.MAX_IMPORT_BYTES", 16)
    with pytest.raises(BankImportError, match="zu groß"):
        parse_csv((HEADER + ROW).encode())


def test_import_row_limit(monkeypatch):
    monkeypatch.setattr("app.engine.bank_import.MAX_IMPORT_ROWS", 2)
    with pytest.raises(BankImportError, match="mehr als 2"):
        parse_csv((HEADER + ROW * 3).encode())


def test_undecodable_text_is_not_replaced_silently():
    with pytest.raises(BankImportError, match="Textformat"):
        parse_csv(HEADER.encode() + b"\x81")


@pytest.mark.parametrize("field,value", [("Müller", "M" * 256), ("Miete Januar", "X" * 501)])
def test_database_field_limits_fail_without_truncation(field, value):
    with pytest.raises(BankImportError, match="Zeile 2"):
        parse_csv((HEADER + ROW.replace(field, value)).encode())
