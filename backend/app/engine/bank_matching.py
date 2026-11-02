"""Pure bank-match proposals; no booking, I/O, or invented repayment order.

The read-edge supplies tenant/account-scoped targets and existing Mietkonto
OpenItems derived from Soll AND booked payments. A proposal is never authority
to book: API and UI must revalidate/confirm it. Ambiguous partial splits retain
amount=None and the full unallocated amount; the user must enter the split.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.engine.bank_import import BankImportRow
from app.engine.mietkonto import OpenItem, PT_HK_VORAUSZAHLUNG, PT_HAUSGELD_VORSCHUSS, PT_KALTMIETE, PT_NK_VORAUSZAHLUNG
from app.engine.rounding import CENT

Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class BankMatchingError(ValueError):
    """Invalid matching input; never silently round or reinterpret money."""


@dataclass(frozen=True)
class MatchTarget:
    name: str
    iban: str | None
    open_items: tuple[OpenItem, ...]
    lease_agreement_id: uuid.UUID | None = None
    tenant_profile_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None


@dataclass(frozen=True)
class MatchLine:
    payment_type: str
    period_year: int
    period_month: int
    open_amount: Decimal
    amount: Decimal | None
    lease_agreement_id: uuid.UUID | None = None
    tenant_profile_id: uuid.UUID | None = None
    owner_profile_id: uuid.UUID | None = None


@dataclass(frozen=True)
class MatchSuggestion:
    target_name: str
    confidence: Confidence
    lines: tuple[MatchLine, ...]
    reasons: tuple[str, ...]
    requires_review: bool
    unallocated_amount: Decimal


_MONTHS = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3,
    "april": 4, "apr": 4, "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "oktober": 10, "okt": 10,
    "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
_MONTH_PATTERN = re.compile(r"\b(" + "|".join(_MONTHS) + r")\.?(?:\s*([12][0-9]{3}))?\b")


def _money(value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise BankMatchingError("Beträge müssen endliche Decimal-Werte sein.")
    try:
        if value != value.quantize(CENT):
            raise BankMatchingError("Beträge müssen centgenau sein; keine automatische Rundung.")
    except InvalidOperation as exc:
        raise BankMatchingError("Ungültiger Geldbetrag.") from exc


def _normalized(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").casefold().strip()


def _name(value: str | None) -> tuple[str, ...]:
    # Exact normalized tokens, not substrings or fuzzy person identification.
    normalized = _normalized(value).translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"}))
    return tuple(sorted(re.findall(r"\w+", normalized)))


def _iban(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def _words(value: str | None) -> set[str]:
    # Reference digits must not hide classification words, e.g. Storno12345.
    return set(re.findall(r"[^\W\d_]+", _normalized(value)))


def matching_period(transaction: BankImportRow) -> tuple[int, int, bool] | None:
    """Resolve year/month and whether explicit; shared by target loading and matching."""
    purpose = _normalized(transaction.purpose)
    periods: set[tuple[int, int]] = set()
    for match in re.finditer(r"(?<![0-9.])([0-9]{1,2})[./-]([12][0-9]{3})(?![0-9])", purpose):
        month, year = map(int, match.groups())
        if not 1 <= month <= 12:
            return None
        periods.add((year, month))
    for match in re.finditer(r"(?<![0-9])([12][0-9]{3})-(0?[1-9]|1[0-2])(?![-0-9])", purpose):
        year, month = map(int, match.groups())
        periods.add((year, month))
    for match in _MONTH_PATTERN.finditer(purpose):
        month = _MONTHS[match[1]]
        # Do not guess a year across December/January or different bank months.
        if match[2] is None and month != transaction.value_date.month:
            return None
        periods.add((int(match[2]) if match[2] else transaction.value_date.year, month))
    if len(periods) > 1:
        return None
    if periods:
        year, month = next(iter(periods))
        return year, month, True
    return transaction.value_date.year, transaction.value_date.month, False


def _component_hint(purpose: str | None) -> set[str] | None:
    words = _words(purpose)
    result: set[str] = set()
    if words & {"warmmiete", "miete"}:
        result.update({PT_KALTMIETE, PT_NK_VORAUSZAHLUNG, PT_HK_VORAUSZAHLUNG})
    if "kaltmiete" in words:
        result.add(PT_KALTMIETE)
    if words & {"nk", "nebenkosten", "betriebskosten"}:
        result.add(PT_NK_VORAUSZAHLUNG)
    if words & {"heizkosten", "heizung", "hk"}:
        result.add(PT_HK_VORAUSZAHLUNG)
    if "hausgeld" in words:
        result.add(PT_HAUSGELD_VORSCHUSS)
    return result or None


def _validate_target(target: MatchTarget) -> None:
    if bool(target.lease_agreement_id) == bool(target.owner_profile_id):
        raise BankMatchingError("Ein Ziel muss genau einen Mietvertrag oder Eigentümer haben.")
    if target.owner_profile_id and target.tenant_profile_id:
        raise BankMatchingError("Eigentümerziel darf kein Mieterprofil enthalten.")
    seen = set()
    for item in target.open_items:
        _money(item.open_amount)
        key = (item.period_year, item.period_month, item.payment_type)
        if key in seen:
            raise BankMatchingError("Offene Posten müssen je Periode und Typ bereits aggregiert sein.")
        seen.add(key)


def _suggestion(
    transaction: BankImportRow, target: MatchTarget, period: tuple[int, int, bool]
) -> MatchSuggestion | None:
    bank_iban, target_iban = _iban(transaction.counterparty_iban), _iban(target.iban)
    if bank_iban and target_iban and bank_iban != target_iban:
        return None
    iban_match = bool(bank_iban and bank_iban == target_iban)
    bank_name, target_name = _name(transaction.counterparty_name), _name(target.name)
    name_match = bool(bank_name and bank_name == target_name)
    if not iban_match and not name_match:
        return None
    name_conflict = bool(iban_match and bank_name and target_name and not name_match)
    year, month, explicit = period
    allowed = {PT_KALTMIETE, PT_NK_VORAUSZAHLUNG, PT_HK_VORAUSZAHLUNG} if target.lease_agreement_id else {PT_HAUSGELD_VORSCHUSS}
    hint = _component_hint(transaction.purpose)
    if hint is not None:
        allowed &= hint
    items = sorted((item for item in target.open_items
                    if item.open_amount > 0 and (item.period_year, item.period_month) == (year, month)
                    and item.payment_type in allowed), key=lambda item: item.payment_type)
    total = sum((item.open_amount for item in items), Decimal("0.00"))
    if not items or transaction.amount > total:
        return None
    full = transaction.amount == total
    resolved = full or len(items) == 1
    lines = tuple(MatchLine(
        payment_type=item.payment_type, period_year=year, period_month=month,
        open_amount=item.open_amount,
        amount=(item.open_amount if full else transaction.amount) if resolved else None,
        lease_agreement_id=target.lease_agreement_id,
        tenant_profile_id=target.tenant_profile_id, owner_profile_id=target.owner_profile_id,
    ) for item in items)
    requires_review = not full or not explicit or name_conflict
    confidence: Confidence = "HIGH" if iban_match and not requires_review else "MEDIUM"
    reasons = ["IBAN stimmt überein." if iban_match else "Name stimmt überein; IBAN nicht beidseitig bekannt.",
               "Monat aus Verwendungszweck." if explicit else "Monat aus Wertstellung – bitte prüfen."]
    if name_conflict:
        reasons.append("Name weicht ab – bitte prüfen.")
    if not full:
        reasons.append("Teilzahlung – Aufteilung vor Übernahme prüfen.")
    if not resolved:
        confidence = "LOW"
        reasons.append("Mehrere offene Komponenten; keine automatische Tilgungsreihenfolge.")
    return MatchSuggestion(target.name, confidence, lines, tuple(reasons), requires_review,
                           Decimal("0.00") if resolved else transaction.amount)


def suggest_matches(
    transaction: BankImportRow, targets: Sequence[MatchTarget]
) -> tuple[MatchSuggestion, ...]:
    """Suggest only positive incoming payments against scoped open monthly Soll.

    Targets must come from the same account/property/org at the service edge.
    No identities or Soll are loaded here. Credits, unknown identities,
    overpayments and conflicting months require manual assignment instead.
    """
    _money(transaction.amount)
    identities = set()
    for target in targets:
        _validate_target(target)
        identity = (target.lease_agreement_id, target.owner_profile_id)
        if identity in identities:
            raise BankMatchingError("Doppelte Zielidentität; offene Posten vorher zusammenführen.")
        identities.add(identity)
    if transaction.amount <= 0:
        return ()
    words = _words(transaction.purpose)
    if words & {
        "kaution", "kautionszahlung", "storno", "rückbuchung", "rueckbuchung",
        "rückzahlung", "rueckzahlung", "rücklastschrift", "ruecklastschrift",
        "erstattung", "rückerstattung", "rueckerstattung",
    }:
        return ()
    hint = _component_hint(transaction.purpose)
    if hint and PT_HAUSGELD_VORSCHUSS in hint and hint & {PT_KALTMIETE, PT_NK_VORAUSZAHLUNG, PT_HK_VORAUSZAHLUNG}:
        return ()
    period = matching_period(transaction)
    if period is None:
        return ()
    matches = [match for target in targets if (match := _suggestion(transaction, target, period)) is not None]
    if len(matches) > 1:
        matches = [replace(match, confidence="MEDIUM" if match.confidence == "HIGH" else match.confidence,
                           requires_review=True, reasons=match.reasons + ("Mehrere passende Ziele – bitte auswählen.",))
                   for match in matches]
    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return tuple(sorted(matches, key=lambda match: (
        rank[match.confidence], str(match.lines[0].lease_agreement_id or match.lines[0].owner_profile_id),
        match.lines[0].period_year, match.lines[0].period_month,
    )))
