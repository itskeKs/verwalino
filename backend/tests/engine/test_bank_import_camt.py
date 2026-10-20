"""Synthetic CAMT.053 statements: 700 + 150 - 200.01 = 649.99 EUR."""
import io
from datetime import date
from decimal import Decimal
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.engine.bank_import import BankImportError, parse_camt053, parse_csv

VERSIONS = ["02", "08"]


def transaction(version, name="Müller", amount=None, direction="CRDT"):
    party = f"<Nm>{name}</Nm>" if version == "02" else f"<Pty><Nm>{name}</Nm></Pty>"
    role = "Dbtr" if direction == "CRDT" else "Cdtr"
    amt = f'<AmtDtls><TxAmt><Amt Ccy="EUR">{amount}</Amt></TxAmt></AmtDtls>' if amount else ""
    return f"""<TxDtls>{amt}<RltdPties><{role}>{party}</{role}>
        <{role}Acct><Id><IBAN>DE02120300000000202051</IBAN></Id></{role}Acct>
        </RltdPties><RmtInf><Ustrd>Miete Januar</Ustrd></RmtInf></TxDtls>"""


def entry(version, amount="850.00", direction="CRDT", details=None):
    status = "BOOK" if version == "02" else "<Cd>BOOK</Cd>"
    if details is None:
        details = transaction(version, amount="700.00") + transaction(version, amount="150.00")
    return f"""<Ntry><Amt Ccy="EUR">{amount}</Amt><CdtDbtInd>{direction}</CdtDbtInd>
        <Sts>{status}</Sts><BookgDt><Dt>2026-01-02</Dt></BookgDt>
        <ValDt><Dt>2026-01-03</Dt></ValDt><NtryDtls>{details}</NtryDtls></Ntry>"""


def document(version="08", entries=None):
    if entries is None:
        entries = entry(version) + entry(version, "200.01", "DBIT", transaction(version, "Heizservice", direction="DBIT"))
    return f'''<?xml version="1.0" encoding="UTF-8"?>
    <Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.{version}">
      <BkToCstmrStmt><GrpHdr><MsgId>synthetic</MsgId></GrpHdr><Stmt><Id>synthetic</Id>
      <Acct><Id><IBAN>DE02120300000000202051</IBAN></Id><Ccy>EUR</Ccy></Acct>
      {entries}</Stmt></BkToCstmrStmt>
    </Document>'''.encode()


def archive(files):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zf:
        for name, content in files:
            zf.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize("version", VERSIONS)
def test_both_versions_exact_batch_and_debit(version):
    rows = parse_camt053(document(version))
    assert [row.amount for row in rows] == [Decimal("700.00"), Decimal("150.00"), Decimal("-200.01")]
    assert sum((row.amount for row in rows), Decimal(0)) == Decimal("649.99")
    assert [row.counterparty_name for row in rows] == ["Müller", "Müller", "Heizservice"]
    assert all(row.counterparty_iban == "DE02120300000000202051" for row in rows)
    assert all(row.booking_date == date(2026, 1, 2) for row in rows)
    assert all(row.value_date == date(2026, 1, 3) for row in rows)
    assert rows == parse_camt053(document(version))


@pytest.mark.parametrize("version", VERSIONS)
def test_single_transaction_matches_csv_output_including_identity(version):
    rows = parse_camt053(document(version, entry(version, "700.00", details=transaction(version))))
    csv = "Buchungstag;Wertstellung;Betrag;Name;IBAN;Verwendungszweck\n02.01.2026;03.01.2026;700,00;Müller;DE02120300000000202051;Miete Januar\n"
    assert rows == parse_csv(csv.encode())


def test_zip_multiple_documents_have_stable_unique_occurrences():
    content = document("02")
    rows = parse_camt053(archive([("one.xml", content), ("folder/two.xml", content)]))
    assert len(rows) == 6
    assert len({row.import_hash for row in rows}) == 6
    assert rows == parse_camt053(archive([("one.xml", content), ("folder/two.xml", content)]))


@pytest.mark.parametrize("version", VERSIONS)
def test_entry_without_details_still_has_real_bank_amount(version):
    rows = parse_camt053(document(version, entry(version, "0.01", details="")))
    assert len(rows) == 1
    assert rows[0].amount == Decimal("0.01")
    assert rows[0].counterparty_name is None


@pytest.mark.parametrize("version", VERSIONS)
def test_date_time_and_multiple_remittance_lines(version):
    content = document(version, entry(version, "700.00", details=transaction(version)))
    content = content.replace(b"<Dt>2026-01-02</Dt>", b"<DtTm>2026-01-02T23:59:00+01:00</DtTm>")
    content = content.replace(b"<Ustrd>Miete Januar</Ustrd>", b"<Ustrd>Miete</Ustrd><Ustrd>Januar</Ustrd>")
    row = parse_camt053(content)[0]
    assert row.booking_date == date(2026, 1, 2)
    assert row.purpose == "Miete\nJanuar"


@pytest.mark.parametrize("replacement", [b"849.99", b"850.01"])
def test_batch_sum_must_exactly_equal_entry(replacement):
    with pytest.raises(BankImportError, match="Summe"):
        parse_camt053(document().replace(b">850.00<", b">" + replacement + b"<"))


def test_batch_without_individual_amounts_is_not_guessed():
    details = transaction("08") + transaction("08")
    with pytest.raises(BankImportError, match="Sammelbuchung"):
        parse_camt053(document("08", entry("08", details=details)))


@pytest.mark.parametrize("bad", [b"-850.00", b"NaN", b"1e3", b"850,00", b"850.001"])
def test_invalid_xml_amounts_are_rejected(bad):
    with pytest.raises(BankImportError, match="Betrag"):
        parse_camt053(document().replace(b">850.00<", b">" + bad + b"<"))


@pytest.mark.parametrize("old,new", [(b'Ccy="EUR"', b'Ccy="USD"'), (b">CRDT<", b">OTHER<"), (b">BOOK<", b">PDNG<"), (b"<ValDt><Dt>2026-01-03</Dt></ValDt>", b""), (b"camt.053.001.08", b"camt.053.001.09")])
def test_unsupported_or_incomplete_financial_fields_fail(old, new):
    with pytest.raises(BankImportError):
        parse_camt053(document().replace(old, new))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_dtd_and_entities_rejected_in_all_encodings(encoding):
    xml = document().decode().split("?>", 1)[1]
    xml = '<!DOCTYPE Document [<!ENTITY injected "injected">]>' + xml.replace("Müller", "&injected;")
    with pytest.raises(BankImportError, match="DTD|DOCTYPE|Entit"):
        parse_camt053(xml.encode(encoding))


@pytest.mark.parametrize("filename", ["../escape.xml", "/absolute.xml", "folder/../../escape.xml", "C:\\escape.xml", "nested.zip"])
def test_unsafe_zip_members_rejected(filename):
    with pytest.raises(BankImportError, match="ZIP"):
        parse_camt053(archive([(filename, document())]))


def test_zip_expansion_is_bounded(monkeypatch):
    compressed = archive([("statement.xml", document())])
    monkeypatch.setattr("app.engine.bank_import.MAX_IMPORT_BYTES", len(compressed) + 1)
    with pytest.raises(BankImportError, match="groß|Grenze"):
        parse_camt053(compressed)


def test_archive_with_broken_second_document_returns_no_partial_success():
    with pytest.raises(BankImportError, match="XML"):
        parse_camt053(archive([("one.xml", document()), ("broken.xml", b"<broken>")]))


def test_detail_direction_without_own_amount_cannot_contradict_entry():
    detail = transaction("08").replace("<TxDtls>", "<TxDtls><CdtDbtInd>DBIT</CdtDbtInd>")
    with pytest.raises(BankImportError, match="Summe|Richtung"):
        parse_camt053(document("08", entry("08", "700.00", details=detail)))


def test_zip_rejects_statements_for_different_accounts():
    second = document().replace(
        b"<Acct><Id><IBAN>DE02120300000000202051</IBAN>",
        b"<Acct><Id><IBAN>DE12500105170648489890</IBAN>",
    )
    with pytest.raises(BankImportError, match="mehrere Konten"):
        parse_camt053(archive([("one.xml", document()), ("two.xml", second)]))


def test_empty_recognized_statement_is_valid():
    assert parse_camt053(document(entries="")) == []


def test_xml_transaction_limit(monkeypatch):
    monkeypatch.setattr("app.engine.bank_import.MAX_IMPORT_ROWS", 2)
    with pytest.raises(BankImportError, match="mehr als 2"):
        parse_camt053(document())


def test_zip_member_limit(monkeypatch):
    monkeypatch.setattr("app.engine.bank_import.MAX_ZIP_MEMBERS", 1)
    with pytest.raises(BankImportError, match="mehr als 1"):
        parse_camt053(archive([("one.xml", document()), ("two.xml", document())]))


@pytest.mark.parametrize("content", [b"", b"<Document/>", b"PK\x03\x04invalidzip"])
def test_unknown_input_is_explained(content):
    with pytest.raises(BankImportError):
        parse_camt053(content)
