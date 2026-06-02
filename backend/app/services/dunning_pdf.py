"""Dunning notice PDF rendering (M7).

STRICTLY separated from the engine (SDD section 16): this layer READS the
issued dunning_notice and renders it; it computes NOTHING. Renders only an
issued notice (the versendete Dokument must equal the frozen snapshot).

All addressees and positions use NAMES (no UUIDs in the rendered document),
consistent with settlement_pdf.py.

[section 18/fachlich] The exact layout, legal wording, and Zustellungsnachweis
requirements are product/legal decisions. A functional standard layout is
provided here with clearly-marked provisional text.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.dunning_notice import DunningNotice


@dataclass
class DunningDisplayNames:
    """Human-readable names resolved at render time from Stammdaten.

    These are NOT stored in the snapshot; resolved on-the-fly from the IDs.
    If a name cannot be resolved, the fallback is a dash (never raw UUIDs).
    """
    person_name: str = "---"
    person_address: str = ""
    organization_name: str = ""


_CSS = (Path(__file__).resolve().parent.parent / "templates" / "mahnung.css").read_text(
    encoding="utf-8"
)

_LEVEL_LABELS = {
    1: "Zahlungserinnerung",
    2: "1. Mahnung",
    3: "Letzte Mahnung",
}

# Payment type display labels (German).
_TYPE_LABELS = {
    "KALTMIETE": "Kaltmiete",
    "NK_VORAUSZAHLUNG": "Nebenkostenvorauszahlung",
    "HAUSGELD_VORSCHUSS": "Hausgeldvorschuss",
    "RUECKLAGENANTEIL": "Ruecklagenanteil",
    "SONDERZAHLUNG": "Sonderzahlung",
    "NACHZAHLUNG": "Nachzahlung",
    "KAUTION": "Kaution",
}

_MONTH_NAMES = {
    1: "Januar", 2: "Februar", 3: "Maerz", 4: "April",
    5: "Mai", 6: "Juni", 7: "Juli", 8: "August",
    9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


def _eur(value: Decimal | str | None) -> str:
    """Format a Decimal as German money: 1.234,56 EUR."""
    if value is None:
        return "-"
    if isinstance(value, str):
        value = Decimal(value)
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    s = f"{abs(q):,.2f}"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")
    return ("-" if neg else "") + s + " EUR"


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def _period_label(year: int | str | None, month: int | str | None) -> str:
    """Format a period as a human-readable string."""
    if year is None:
        return "---"
    y = int(year)
    if month is not None:
        m = int(month)
        mname = _MONTH_NAMES.get(m, str(m))
        return f"{mname} {y}"
    return str(y)


def build_dunning_html(
    notice: DunningNotice,
    names: DunningDisplayNames | None = None,
) -> str:
    """Build the dunning notice HTML from an issued notice + display names."""
    dn = names or DunningDisplayNames()
    level_label = _LEVEL_LABELS.get(notice.level, f"Mahnstufe {notice.level}")

    # Build arrears rows from the included_items snapshot (JSONB).
    items = notice.included_items or []
    arrears_rows = []
    for it in items:
        type_label = _TYPE_LABELS.get(it.get("payment_type", ""), it.get("payment_type", ""))
        period = _period_label(it.get("period_year"), it.get("period_month"))
        arrears_rows.append(
            f"<tr>"
            f"<td>{_esc(period)}</td>"
            f"<td>{_esc(type_label)}</td>"
            f"<td class='num'>{_eur(it.get('soll'))}</td>"
            f"<td class='num'>{_eur(it.get('ist'))}</td>"
            f"<td class='num'>{_eur(it.get('open_amount'))}</td>"
            f"</tr>"
        )

    # Summary rows
    summary_rows = [
        f"<tr class='summary'>"
        f"<td colspan='4'>Hauptforderung (Summe Rueckstaende)</td>"
        f"<td class='num'>{_eur(notice.principal_amount)}</td>"
        f"</tr>"
    ]
    if notice.fee_amount and notice.fee_amount > 0:
        summary_rows.append(
            f"<tr class='summary'>"
            f"<td colspan='4'>Mahngebuehr (Stufe {notice.level})</td>"
            f"<td class='num'>{_eur(notice.fee_amount)}</td>"
            f"</tr>"
        )
    summary_rows.append(
        f"<tr class='total'>"
        f"<td colspan='4'>Gesamtbetrag</td>"
        f"<td class='num'>{_eur(notice.total_amount)}</td>"
        f"</tr>"
    )

    # Addressee block
    addressee = _esc(dn.person_name)
    if dn.person_address:
        addressee += f"<br/><span class='meta-sub'>{_esc(dn.person_address)}</span>"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<style>{_CSS}</style>
</head>
<body>
  <h1>{_esc(level_label)}</h1>
  <div class="subtitle">Ausstellungsdatum: {notice.issue_date.strftime('%d.%m.%Y')}</div>

  <table class="meta">
    <tr><td class="label">An</td><td>{addressee}</td></tr>
    <tr><td class="label">Datum</td><td>{notice.issue_date.strftime('%d.%m.%Y')}</td></tr>
    <tr><td class="label">Zahlungsfrist</td><td>{notice.due_date.strftime('%d.%m.%Y')}</td></tr>
    <tr><td class="label">Mahnstufe</td><td>{notice.level} ({_esc(level_label)})</td></tr>
  </table>

  <div class="body-text">
    Sehr geehrte/r {_esc(dn.person_name)},<br/><br/>
    trotz Faelligkeit konnten wir fuer die nachfolgend aufgefuehrten
    Positionen leider keinen vollstaendigen Zahlungseingang feststellen.
    Wir bitten Sie, den ausstehenden Betrag bis zum
    <strong>{notice.due_date.strftime('%d.%m.%Y')}</strong> auf unser Konto
    zu ueberweisen.
  </div>

  <table class="lines">
    <thead>
      <tr>
        <th>Zeitraum</th>
        <th>Art</th>
        <th class="num">Soll</th>
        <th class="num">Gezahlt</th>
        <th class="num">Rueckstand</th>
      </tr>
    </thead>
    <tbody>
      {''.join(arrears_rows)}
      {''.join(summary_rows)}
    </tbody>
  </table>

  <div class="hint">
    <em>[section 18/fachlich] Der genaue Wortlaut, rechtliche Hinweise
    (z. B. Verzugszinsen, Kuendigungsandrohung bei Stufe 3) und das
    endgueltige Layout sind fachlich/juristisch abzustimmen. Verzugszinsen
    sind im MVP nicht enthalten. Mahngebuehren sind provisorisch und muessen
    angemessen/vertraglich vereinbart sein.</em>
  </div>
</body>
</html>"""


def render_dunning_pdf(
    notice: DunningNotice,
    names: DunningDisplayNames | None = None,
) -> bytes:
    """Render an issued dunning notice to PDF bytes (WeasyPrint)."""
    html_str = build_dunning_html(notice, names)
    return render_pdf(html_str)
