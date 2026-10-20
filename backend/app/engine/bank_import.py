"""Pure, fail-closed bank statement parsers; no database or filesystem access.

CSV: German semicolon fields plus documented DKB Giro legacy/2023 headers.
Format source and supported scope are linked in README.md. Amounts are signed
EUR Decimals, never floats. A parse error rejects the entire input.
"""
from __future__ import annotations

import csv
import io
import re
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
