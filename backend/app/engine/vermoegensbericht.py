"""Builds one Vermoegensbericht (§ 28 IV WEG) -> AssetReportResult (pure).

m4-weg-abrechnung.md section 6, ADR-0008.

NO distribution math -- only aggregation + the reserve-development invariant
(fail-loud). The MVP scope (ADR-0008):
  - Stand der Erhaltungsruecklage: AUTOMATIC from the development
    (opening + Σ contributions - withdrawals == closing, checked fail-loud).
  - Bankkonten-Staende: MANUAL Stichtagssaldo per account (Verwalter-Angabe).
  - Forderungen/Verbindlichkeiten: MANUAL positions (Verwalter-Angabe).

Manual values are flagged (is_manual=True) so the PDF marks them as Verwalter-
Angaben without an automatic booking derivation (ADR-0008 -- transparency over
pretended correctness). Stichtag = period_end.
"""

from __future__ import annotations

from decimal import Decimal

from app.engine.rounding import CENT
from app.engine.types import (
    AssetReportLine,
    AssetReportResult,
    ReserveDevelopment,
    VermoegensberichtInput,
)
from app.engine.weg import WegValidationError


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT)


def build_vermoegensbericht(inp: VermoegensberichtInput) -> AssetReportResult:
    """Assemble the Vermoegensbericht (m4 section 6, ADR-0008)."""
    if inp.period_end < inp.period_start:
        raise WegValidationError(
            f"period_end ({inp.period_end}) < period_start ({inp.period_start})"
        )

    opening = _money(inp.reserve.opening_balance)
    withdrawals = _money(inp.reserve.withdrawals_total)
    contributions = _money(inp.reserve_contributions_total)
    closing = _money(opening + contributions - withdrawals + inp.reserve.overrides_total + inp.reserve.late_cash_total)
    reserve_dev = ReserveDevelopment(
        opening_balance=opening,
        contributions_total=contributions,
        withdrawals_total=withdrawals,
        closing_balance=closing,
        overrides_total=inp.reserve.overrides_total,
        late_cash_total=inp.reserve.late_cash_total,
    )

    lines: list[AssetReportLine] = []
    sort_order = 0

    # ----- Reserve development (automatic; m4 section 3.3 / 6.2) -----
    lines.append(
        AssetReportLine(
            line_type="RESERVE_OPENING",
            label="Erhaltungsruecklage Anfangsbestand",
            amount=opening,
            sort_order=sort_order,
            is_manual=False,
            note="Anfangsbestand laut Rücklagenbuch",
        )
    )
    sort_order += 1
    if inp.reserve.late_cash_total:
        lines.append(AssetReportLine(line_type='RESERVE_LATE_CASH', label='Nachträgliche Buchungen zum abgeschlossenen Jahr',
            amount=inp.reserve.late_cash_total, sort_order=sort_order, is_manual=False, note='Rücklagenbuch · ursprüngliche Wertstellung'))
        sort_order += 1
    if inp.reserve.overrides_total:
        lines.append(AssetReportLine(line_type='RESERVE_ADJUSTMENT', label='Begründete Rücklagenkorrekturen',
            amount=inp.reserve.overrides_total, sort_order=sort_order, is_manual=True, note='Rücklagenbuch'))
        sort_order += 1
    lines.append(
        AssetReportLine(
            line_type="RESERVE_CONTRIBUTION",
            label="Zufuehrung Erhaltungsruecklage",
            amount=contributions,
            sort_order=sort_order,
            is_manual=False,
            note="Summe der Zufuehrungen im Zeitraum",
        )
    )
    sort_order += 1
    lines.append(
        AssetReportLine(
            line_type="RESERVE_WITHDRAWAL",
            label="Entnahme Erhaltungsruecklage",
            amount=withdrawals,
            sort_order=sort_order,
            is_manual=False,
            note="Entnahmen laut Rücklagenbuch",
        )
    )
    sort_order += 1
    lines.append(
        AssetReportLine(
            line_type="RESERVE_CLOSING",
            label="Erhaltungsruecklage Endbestand",
            amount=closing,
            sort_order=sort_order,
            is_manual=False,
            note="Endbestand = Anfangsbestand + Zuführungen - Entnahmen + Korrekturen + Nachträgliche Buchungen",
        )
    )
    sort_order += 1

    # ----- Bank balances (manual Stichtagssaldo; ADR-0008) -----
    bank_total = Decimal("0.00")
    for b in sorted(inp.bank_balances, key=lambda x: (x.account_type, x.label)):
        amt = _money(b.balance)
        bank_total += amt
        lines.append(
            AssetReportLine(
                line_type="BANK_BALANCE",
                label=f"Bankkonto {b.label} ({b.account_type})".strip(),
                amount=amt,
                sort_order=sort_order,
                is_manual=True,
                note="Stichtagssaldo (Verwalter-Angabe, kein Buchungssaldo)",
            )
        )
        sort_order += 1

    # ----- Receivables (manual; ADR-0008) -----
    receivables_total = Decimal("0.00")
    for r in inp.receivables:
        amt = _money(r.amount)
        receivables_total += amt
        lines.append(
            AssetReportLine(
                line_type="RECEIVABLE",
                label=r.label,
                amount=amt,
                sort_order=sort_order,
                is_manual=True,
                note="Forderung (Verwalter-Angabe, kein Buchungssaldo)",
            )
        )
        sort_order += 1

    # ----- Payables (manual; ADR-0008) -----
    payables_total = Decimal("0.00")
    for p in inp.payables:
        amt = _money(p.amount)
        payables_total += amt
        lines.append(
            AssetReportLine(
                line_type="PAYABLE",
                label=p.label,
                amount=amt,
                sort_order=sort_order,
                is_manual=True,
                note="Verbindlichkeit (Verwalter-Angabe, kein Buchungssaldo)",
            )
        )
        sort_order += 1

    return AssetReportResult(
        reserve=reserve_dev,
        lines=tuple(lines),
        bank_balance_total=_money(bank_total),
        receivables_total=_money(receivables_total),
        payables_total=_money(payables_total),
    )
