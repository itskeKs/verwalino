"""Pure, fail-closed bank statement parsers; no database or filesystem access.

CSV: German semicolon fields plus documented DKB Giro legacy/2023 headers.
Format source and supported scope are linked in README.md. Amounts are signed
EUR Decimals, never floats. A parse error rejects the entire input.
"""
from __future__ import annotations

import csv
import io
import re
import stat
import xml.etree.ElementTree as ET
import zlib
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from app.engine.canonical import canonical_sha256

MAX_IMPORT_BYTES = 25 * 1024 * 1024
MAX_IMPORT_ROWS = 100_000
MAX_HEADER_ROWS = 50


class BankImportError(ValueError):
    """A statement cannot be interpreted safely; safe to show to the user."""


@dataclass(frozen=True)
class BankImportRow:
    booking_date: date
    value_date: date
    amount: Decimal
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    purpose: str | None = None
    import_hash: str = ""


_COLUMNS = {
    "booking_date": {"buchungstag", "buchungsdatum"},
    "value_date": {"wertstellung", "valutadatum"},
    "amount": {"betrag", "betrag (eur)", "betrag (€)"},
    "counterparty_name": {"name", "gegenkonto name", "auftraggeber / begünstigter"},
    "counterparty_iban": {"iban", "gegenkonto iban"},
    "purpose": {"verwendungszweck"},
    "currency": {"währung"},
    "sender": {"zahlungspflichtige*r"},
    "recipient": {"zahlungsempfänger*in"},
    "direction": {"umsatztyp"},
    "status": {"status"},
}
_REQUIRED = {"booking_date", "value_date", "amount"}
_AMOUNT = re.compile(r"[+-]?(?:[0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+)(?:,[0-9]{1,2})?")


def _decode_csv(content: bytes) -> str:
    if not content.strip():
        raise BankImportError("Leere Datei: kein CSV-Kontoauszug.")
    if len(content) > MAX_IMPORT_BYTES:
        raise BankImportError("CSV-Datei zu groß (maximal 25 MiB).")
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = content.decode("cp1252")
        except UnicodeDecodeError as exc:
            raise BankImportError("Unbekanntes Textformat: UTF-8 oder CP1252 erwartet.") from exc
    if any(ord(char) < 32 and char not in "\r\n\t" for char in decoded):
        raise BankImportError("Ungültige Steuerzeichen im CSV-Kontoauszug.")
    return decoded


def _columns(header: list[str]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, label in enumerate(header):
        normalized = label.strip().casefold()
        for field, aliases in _COLUMNS.items():
            if normalized in aliases:
                if field in columns:
                    raise BankImportError(f"Mehrdeutige CSV-Spalten für {field}.")
                columns[field] = index
    return columns


def _parse_date(value: str, label: str, line: int, *, short_year: bool) -> date:
    pattern = r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"
    fmt = "%d.%m.%Y"
    if short_year and re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", value):
        pattern = r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}"
        fmt = "%d.%m.%y"
    try:
        if not re.fullmatch(pattern, value):
            raise ValueError("unexpected date format")
        return datetime.strptime(value, fmt).date()
    except ValueError as exc:
        raise BankImportError(f"Zeile {line}: ungültiges {label} ({'TT.MM.JJ oder ' if short_year else ''}TT.MM.JJJJ erwartet).") from exc


def _parse_amount(value: str, line: int) -> Decimal:
    value = re.sub(r"\s*(?:€|EUR)$", "", value).strip()
    if not _AMOUNT.fullmatch(value):
        raise BankImportError(f"Zeile {line}: ungültiger Betrag (z. B. -1.234,56 erwartet).")
    amount = Decimal(value.replace(".", "").replace(",", "."))
    if abs(amount) >= Decimal("1000000000000"):
        raise BankImportError(f"Zeile {line}: Betrag überschreitet den zulässigen Wertebereich.")
    return amount.quantize(Decimal("0.01"))


def _text(value: str, field: str, limit: int, line: int) -> str | None:
    value = value.strip()
    if len(value) > limit:
        raise BankImportError(f"Zeile {line}: {field} überschreitet {limit} Zeichen.")
    return value or None


def _csv_row(values: list[str], columns: dict[str, int], line: int) -> BankImportRow:
    def get(field: str) -> str:
        return values[columns[field]].strip() if field in columns else ""

    if "currency" in columns and get("currency") != "EUR":
        raise BankImportError(f"Zeile {line}: nur EUR-Umsätze werden unterstützt.")
    dkb_2023 = {"sender", "recipient", "direction", "status"}.issubset(columns)
    if any(field in columns for field in ("sender", "recipient", "direction")) and not dkb_2023:
        raise BankImportError(f"Zeile {line}: unvollständiges DKB-2023-Format.")
    if "status" in columns and get("status").casefold() != "gebucht":
        raise BankImportError(f"Zeile {line}: Status muss Gebucht sein; vorgemerkte Umsätze sind nicht importierbar.")

    amount = _parse_amount(get("amount"), line)
    name = get("counterparty_name")
    if dkb_2023:
        direction = get("direction")
        if direction not in {"Eingang", "Ausgang"} or (direction == "Eingang" and amount < 0) or (direction == "Ausgang" and amount > 0):
            raise BankImportError(f"Zeile {line}: Umsatztyp und Vorzeichen widersprechen sich.")
        name = get("sender" if direction == "Eingang" else "recipient")
    iban = re.sub(r"\s+", "", get("counterparty_iban")).upper()
    return BankImportRow(
        booking_date=_parse_date(get("booking_date"), "Buchungsdatum", line, short_year=dkb_2023),
        value_date=_parse_date(get("value_date"), "Valutadatum", line, short_year=dkb_2023),
        amount=amount,
        counterparty_name=_text(name, "Name", 255, line),
        counterparty_iban=_text(iban, "IBAN", 34, line),
        purpose=_text(get("purpose"), "Verwendungszweck", 500, line),
    )


def _with_import_hashes(rows: list[BankImportRow]) -> list[BankImportRow]:
    """Count indistinguishable occurrences, rather than dropping real bookings.

    Identity is independent of CSV encoding, column order and file name. Without
    a bank-supplied ID, overlapping files with different numbers of otherwise
    identical bookings cannot establish which occurrence is new.
    """
    occurrences: Counter[str] = Counter()
    result = []
    for row in rows:
        identity = canonical_sha256({
            "booking_date": row.booking_date.isoformat(),
            "value_date": row.value_date.isoformat(),
            "amount": format(row.amount, ".2f"),
            "name": row.counterparty_name,
            "iban": row.counterparty_iban,
            "purpose": row.purpose,
        })
        occurrences[identity] += 1
        result.append(replace(row, import_hash=canonical_sha256({
            "identity": identity, "occurrence": occurrences[identity],
        })))
    return result


def parse_csv(content: bytes) -> list[BankImportRow]:
    """Parse semicolon CSV (UTF-8/BOM or CP1252), or reject it as a whole.

    Required fields: Buchungstag/Buchungsdatum, Wertstellung/Valutadatum,
    Betrag/Betrag (EUR)/Betrag (€). Optional fields are mapped by explicit aliases,
    never fuzzy guesses. A bounded preamble is supported for bank metadata.
    Unknown bank formats are NOT silently treated as the user's bank profile.
    """
    reader = csv.reader(io.StringIO(_decode_csv(content), newline=""), delimiter=";", strict=True)
    columns = None
    width = 0
    dkb_legacy = False
    rows = []
    try:
        for values in reader:
            if not any(value.strip() for value in values):
                continue
            if columns is None:
                candidate = _columns(values)
                if _REQUIRED.issubset(candidate):
                    columns = candidate
                    width = len(values)
                    dkb_legacy = {
                        "buchungstext", "kontonummer", "blz", "betrag (eur)",
                        "auftraggeber / begünstigter",
                    }.issubset({value.strip().casefold() for value in values})
                elif reader.line_num >= MAX_HEADER_ROWS:
                    break
                continue
            if len(values) != width:
                raise BankImportError(f"Zeile {reader.line_num}: falsche Spaltenanzahl (erwartet {width}, erhalten {len(values)}).")
            row = _csv_row(values, columns, reader.line_num)
            if dkb_legacy and row.purpose == "Tagessaldo":
                continue
            rows.append(row)
            if len(rows) > MAX_IMPORT_ROWS:
                raise BankImportError(f"CSV enthält mehr als {MAX_IMPORT_ROWS} Umsätze.")
    except csv.Error as exc:
        raise BankImportError(f"Zeile {reader.line_num}: ungültige CSV-Struktur.") from exc
    if columns is None:
        raise BankImportError("Unbekanntes CSV-Format: Semikolon und Spalten Buchungstag/Buchungsdatum, Wertstellung/Valutadatum und Betrag erwartet.")
    return _with_import_hashes(rows)


_CAMT_NAMESPACES = {
    f"urn:iso:std:iso:20022:tech:xsd:camt.053.001.{version}"
    for version in ("02", "08")
}
MAX_ZIP_MEMBERS = 100


class _StatementTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        # Parser callback, not a byte search: also protects UTF-16 XML.
        raise BankImportError("XML-DTD/DOCTYPE und Entitäten sind nicht erlaubt.")


def _xml_path(path: str, namespace: str) -> str:
    return "/".join(f"{{{namespace}}}{part}" for part in path.split("/"))


def _xml_node(parent: ET.Element, path: str, namespace: str, *, required: bool = True) -> ET.Element | None:
    nodes = parent.findall(_xml_path(path, namespace))
    if len(nodes) > 1 or (required and not nodes):
        raise BankImportError(f"CAMT: fehlendes oder mehrdeutiges Feld {path}.")
    return nodes[0] if nodes else None


def _xml_text(parent: ET.Element, path: str, namespace: str, *, required: bool = True) -> str:
    node = _xml_node(parent, path, namespace, required=required)
    value = node.text.strip() if node is not None and node.text else ""
    if required and not value:
        raise BankImportError(f"CAMT: leeres Pflichtfeld {path}.")
    return value


def _camt_amount(node: ET.Element, label: str) -> Decimal:
    value = (node.text or "").strip()
    if node.get("Ccy") != "EUR":
        raise BankImportError(f"CAMT {label}: nur EUR-Beträge werden unterstützt.")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
        raise BankImportError(f"CAMT {label}: ungültiger Betrag (positiver XML-Dezimalwert erwartet).")
    amount = Decimal(value)
    if amount >= Decimal("1000000000000"):
        raise BankImportError(f"CAMT {label}: Betrag überschreitet den Wertebereich.")
    return amount.quantize(Decimal("0.01"))


def _camt_date(entry: ET.Element, field: str, namespace: str) -> date:
    day = _xml_text(entry, f"{field}/Dt", namespace, required=False)
    timestamp = _xml_text(entry, f"{field}/DtTm", namespace, required=False)
    try:
        if bool(day) == bool(timestamp):
            raise ValueError("exactly one date required")
        if day and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
            return date.fromisoformat(day)
        if timestamp and "T" in timestamp:
            return datetime.fromisoformat(timestamp).date()
        raise ValueError("invalid date format")
    except ValueError as exc:
        raise BankImportError(f"CAMT: fehlendes oder ungültiges Datum {field}.") from exc


def _camt_signed(amount: Decimal, direction: str) -> Decimal:
    if direction not in {"CRDT", "DBIT"}:
        raise BankImportError("CAMT: CdtDbtInd muss CRDT oder DBIT sein.")
    return amount if direction == "CRDT" else -amount


def _camt_entry(entry: ET.Element, namespace: str) -> list[BankImportRow]:
    status = _xml_text(entry, "Sts" if namespace.endswith(".02") else "Sts/Cd", namespace)
    if status != "BOOK":
        raise BankImportError("CAMT: nur Status BOOK (gebucht) ist importierbar.")
    amount_node = _xml_node(entry, "Amt", namespace)
    total = _camt_signed(_camt_amount(amount_node, "Buchung"), _xml_text(entry, "CdtDbtInd", namespace))
    booking_date = _camt_date(entry, "BookgDt", namespace)
    value_date = _camt_date(entry, "ValDt", namespace)
    details = entry.findall(_xml_path("NtryDtls/TxDtls", namespace))
    if not details:
        return [BankImportRow(
            booking_date, value_date, total,
            purpose=_text(_xml_text(entry, "AddtlNtryInf", namespace, required=False), "Verwendungszweck", 500, 0),
        )]
    result = []
    for detail in details:
        amount_nodes = detail.findall(_xml_path("AmtDtls/TxAmt/Amt", namespace)) + detail.findall(_xml_path("Amt", namespace))
        if len(amount_nodes) > 1:
            raise BankImportError("CAMT: mehrdeutiger Einzelbetrag der Sammelbuchung.")
        if not amount_nodes and len(details) > 1:
            raise BankImportError("CAMT: Sammelbuchung ohne eindeutige Einzelbeträge.")
        direction = _xml_text(detail, "CdtDbtInd", namespace, required=False) or _xml_text(entry, "CdtDbtInd", namespace)
        unsigned_amount = _camt_amount(amount_nodes[0], "Einzelbuchung") if amount_nodes else abs(total)
        amount = _camt_signed(unsigned_amount, direction)
        role = "Dbtr" if amount >= 0 else "Cdtr"
        party_path = f"RltdPties/{role}" + ("/Pty" if namespace.endswith(".08") else "")
        name = _xml_text(detail, f"{party_path}/Nm", namespace, required=False)
        iban = _xml_text(detail, f"RltdPties/{role}Acct/Id/IBAN", namespace, required=False)
        remittance = detail.findall(_xml_path("RmtInf/Ustrd", namespace))
        purpose = "\n".join((node.text or "").strip() for node in remittance)
        result.append(BankImportRow(
            booking_date=booking_date, value_date=value_date, amount=amount,
            counterparty_name=_text(name, "Name", 255, 0),
            counterparty_iban=_text(re.sub(r"\s+", "", iban).upper(), "IBAN", 34, 0),
            purpose=_text(purpose, "Verwendungszweck", 500, 0),
        ))
    if sum((row.amount for row in result), Decimal(0)) != total:
        raise BankImportError("CAMT: Summe der Einzelbeträge stimmt nicht mit der Buchung überein.")
    return result


def _parse_camt_xml(content: bytes) -> tuple[list[BankImportRow], set[str]]:
    try:
        root = ET.fromstring(content, parser=ET.XMLParser(target=_StatementTreeBuilder()))
    except ET.ParseError as exc:
        raise BankImportError("Ungültige CAMT-XML-Datei.") from exc
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if namespace not in _CAMT_NAMESPACES or root.tag != f"{{{namespace}}}Document":
        raise BankImportError("Unbekanntes CAMT-Format: camt.053.001.02 oder .08 erwartet.")
    container = _xml_node(root, "BkToCstmrStmt", namespace)
    statements = container.findall(_xml_path("Stmt", namespace))
    if not statements:
        raise BankImportError("CAMT enthält keinen Kontoauszug (Stmt).")
    rows = []
    accounts = set()
    for statement in statements:
        iban = _xml_text(statement, "Acct/Id/IBAN", namespace, required=False)
        if iban:
            accounts.add(re.sub(r"\s+", "", iban).upper())
        currency = _xml_text(statement, "Acct/Ccy", namespace, required=False)
        if currency and currency != "EUR":
            raise BankImportError("CAMT: nur EUR-Konten sind unterstützt.")
        for entry in statement.findall(_xml_path("Ntry", namespace)):
            rows.extend(_camt_entry(entry, namespace))
            if len(rows) > MAX_IMPORT_ROWS:
                raise BankImportError(f"CAMT enthält mehr als {MAX_IMPORT_ROWS} Umsätze.")
    if len(accounts) > 1:
        raise BankImportError("CAMT enthält mehrere Konten; bitte je Konto getrennt importieren.")
    return rows, accounts


def _camt_zip_documents(content: bytes) -> list[bytes]:
    documents = []
    total = 0
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise BankImportError(f"ZIP enthält mehr als {MAX_ZIP_MEMBERS} Einträge.")
            names = set()
            for member in members:
                name = member.filename
                if name in names or name.startswith("/") or "\\" in name or ":" in name or ".." in name.split("/") or stat.S_ISLNK(member.external_attr >> 16):
                    raise BankImportError("ZIP enthält doppelte oder unsichere Dateipfade.")
                names.add(name)
                if member.is_dir():
                    continue
                if not name.lower().endswith(".xml") or member.flag_bits & 1 or member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
                    raise BankImportError("ZIP darf nur unverschlüsselte XML-Dateien enthalten.")
                if total + member.file_size > MAX_IMPORT_BYTES:
                    raise BankImportError("ZIP entpackt zu groß (Grenze 25 MiB).")
                with archive.open(member) as source:
                    data = source.read(MAX_IMPORT_BYTES - total + 1)
                total += len(data)
                if total > MAX_IMPORT_BYTES:
                    raise BankImportError("ZIP entpackt zu groß (Grenze 25 MiB).")
                documents.append(data)
    except (BadZipFile, RuntimeError, EOFError, zlib.error, NotImplementedError) as exc:
        raise BankImportError("Ungültiges oder nicht unterstütztes ZIP-Archiv.") from exc
    if not documents:
        raise BankImportError("ZIP enthält keine XML-Kontoauszüge.")
    return documents


def parse_camt053(content: bytes) -> list[BankImportRow]:
    """Parse .02/.08 XML or a bounded ZIP of statements without extracting files.

    Batch details must sum exactly to the entry amount. An unresolvable batch,
    unsafe archive or any invalid document rejects the entire input.
    """
    if not content.strip():
        raise BankImportError("Leere CAMT-Datei.")
    if len(content) > MAX_IMPORT_BYTES:
        raise BankImportError("CAMT-Datei zu groß (maximal 25 MiB).")
    documents = _camt_zip_documents(content) if content.startswith(b"PK") else [content]
    rows = []
    accounts = set()
    for document in documents:
        document_rows, document_accounts = _parse_camt_xml(document)
        rows.extend(document_rows)
        accounts.update(document_accounts)
        if len(rows) > MAX_IMPORT_ROWS:
            raise BankImportError(f"CAMT enthält mehr als {MAX_IMPORT_ROWS} Umsätze.")
    if len(accounts) > 1:
        raise BankImportError("CAMT enthält mehrere Konten; bitte je Konto getrennt importieren.")
    return _with_import_hashes(rows)
