"""Vermoegensbericht PDF rendering (M4, m4 section 8.3, ADR-0008).

STRICTLY separated from the engine: READS the finalized settlement_run +
settlement_line, computes NOTHING (a test asserts PDF values == line values).
Renders only a finalized run.

Shows: Stand der Erhaltungsruecklage (mit Entwicklung); Bankkonten-Staende
(Stichtag); Forderungen/Verbindlichkeiten. CLEAR marking which values are
manual Verwalter-Angaben (no automatic booking derivation; ADR-0008) --
transparency over pretended correctness. Stichtag = period_end.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun

_CSS = (Path(__file__).resolve().parent.parent / "templates" / "weg_abrechnung.css").read_text(
    encoding="utf-8"
)

_RESERVE_TYPES = ("RESERVE_OPENING", "RESERVE_CONTRIBUTION", "RESERVE_WITHDRAWAL", "RESERVE_CLOSING",
                  "RESERVE_LATE_CASH", "RESERVE_ADJUSTMENT", "SUBTOTAL")
# Historical asset reports used SUBTOTAL only for reserve corrections/late cash.
# Line types whose value is a manual Verwalter-Angabe (ADR-0008).
_MANUAL_TYPES = ("BANK_BALANCE", "RECEIVABLE", "PAYABLE")


@dataclass
class VermoegensberichtDisplayNames:
    property_name: str = "—"
    property_address: str = ""


def _eur(value: Decimal | None) -> str:
    if value is None:
        return "-"
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    s = f"{abs(q):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return ("-" if neg else "") + s + " EUR"


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def _row(ln: SettlementLine, cls: str = "") -> str:
    manual = ""
    if ln.line_type in _MANUAL_TYPES:
        manual = "<span class='manual-tag'> (Verwalter-Angabe)</span>"
    note = f"<div class='note'>{_esc(ln.note)}</div>" if ln.note else ""
    return (
        f"<tr class='{cls}'><td>{_esc(ln.label)}{manual}{note}</td>"
        f"<td class='num'>{_eur(ln.amount)}</td></tr>"
    )


def build_vermoegensbericht_html(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: VermoegensberichtDisplayNames | None = None,
) -> str:
    dn = names or VermoegensberichtDisplayNames()
    ordered = sorted(lines, key=lambda l: l.sort_order)

    reserve_rows = []
    bank_rows = []
    recv_rows = []
    pay_rows = []
    for ln in ordered:
        if ln.line_type in _RESERVE_TYPES:
            cls = "closing" if ln.line_type == "RESERVE_CLOSING" else ""
            reserve_rows.append(_row(ln, cls))
        elif ln.line_type == "BANK_BALANCE":
            bank_rows.append(_row(ln))
        elif ln.line_type == "RECEIVABLE":
            recv_rows.append(_row(ln))
        elif ln.line_type == "PAYABLE":
            pay_rows.append(_row(ln))

    sections = []
    if reserve_rows:
        overdrawn = any(ln.line_type == 'RESERVE_CLOSING' and ln.amount < 0 for ln in ordered)
        sections.append(
            "<h2>Stand der Erhaltungsruecklage</h2>"
            + ("<p class='hint'><strong>Die Rücklage ist überzogen.</strong> Bitte prüfen Sie die Finanzierung und die Belege.</p>" if overdrawn else "") +
            "<table class='lines'><tbody>" + "".join(reserve_rows) + "</tbody></table>"
        )
    if bank_rows:
        sections.append(
            "<h2>Bankkonten (Stichtagssalden)</h2>"
            "<table class='lines'><tbody>" + "".join(bank_rows) + "</tbody></table>"
        )
    if recv_rows:
        sections.append(
            "<h2>Forderungen der Gemeinschaft</h2>"
            "<table class='lines'><tbody>" + "".join(recv_rows) + "</tbody></table>"
        )
    if pay_rows:
        sections.append(
            "<h2>Verbindlichkeiten der Gemeinschaft</h2>"
            "<table class='lines'><tbody>" + "".join(pay_rows) + "</tbody></table>"
        )

    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    integrity = ""
    if run.snapshot_sha256:
        integrity = (
            f"<div class='integrity'>Snapshot-Pruefsumme (SHA-256): "
            f"{_esc(run.snapshot_sha256)}<br>Engine-Version: {_esc(run.engine_version)}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"/><style>{_CSS}</style></head>
<body>
  <h1>Vermoegensbericht (§ 28 Abs. 4 WEG)</h1>
  <div class="subtitle">Stichtag {run.period_end.strftime('%d.%m.%Y')}
     (Zeitraum {run.period_start.strftime('%d.%m.%Y')} bis {run.period_end.strftime('%d.%m.%Y')})</div>

  <table class="meta">
    <tr><td class="label">Objekt</td><td>{property_display}</td></tr>
  </table>

  {''.join(sections)}

  <div class="disclaimer">
    Hinweis (ADR-0008, [§18/fachlich]): Der <strong>Stand der
    Erhaltungsruecklage</strong> ist aus der Entwicklung (Anfangsbestand +
    Zufuehrungen - Entnahmen ± Korrekturen + nachträgliche Buchungen = Endbestand) abgeleitet. Die mit
    <em>(Verwalter-Angabe)</em> gekennzeichneten Werte (Bankkonten-Stichtagssalden,
    Forderungen/Verbindlichkeiten) sind <strong>manuelle Angaben der
    Verwaltung</strong> und <strong>kein automatischer Buchungssaldo</strong>
    (kein Bankabgleich in dieser Ausbaustufe). Vollstaendigkeit und Eignung fuer
    § 28 Abs. 4 WEG sind fachlich (Steuerberater/Fachanwalt fuer WEG-Recht)
    abzustimmen.
  </div>
  {integrity}
</body>
</html>"""


def render_vermoegensbericht_pdf(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: VermoegensberichtDisplayNames | None = None,
) -> bytes:
    return render_pdf(build_vermoegensbericht_html(run, lines, names))
