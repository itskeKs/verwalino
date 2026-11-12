"""Dunning notice PDF rendering (M7).

STRICTLY separated from the engine (SDD section 16): this layer READS the
issued dunning_notice and renders it; it computes NOTHING. Renders only an
issued notice (the versendete Dokument must equal the frozen snapshot).

All addressees and positions use NAMES (no UUIDs in the rendered document),
consistent with settlement_pdf.py.

[section 18/fachlich] The exact layout, legal wording, and Zustellungsnachweis
requirements are product/legal decisions. Older notices retain their original
rendering, including the original explanatory text.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.engine.dunning import NOTICE_FORMAT_KEY, SEGMENTED_NOTICE_FORMAT
from app.models.dunning_notice import DunningNotice


@dataclass
class DunningDisplayNames:
    """Human-readable names resolved at render time from Stammdaten.

    These are NOT stored in the snapshot; resolved on-the-fly from the IDs.
    If a name cannot be resolved, the fallback is a dash (never raw UUIDs).

    Issued notices carry the additional frozen values (ADR-0012 block): the
    property/unit identity and the payment destination chosen at issue time
    (#331). Older notices without these values keep their established
    rendering (no identity rows, generic payment wording).
    """
    person_name: str = "---"
    person_address: str = ""
    organization_name: str = ""
    property_name: str = ""
    unit_label: str = ""
    bank_iban: str | None = None
    bank_bic: str = ""
    bank_name: str = ""


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
    "NK_VORAUSZAHLUNG": "Betriebskosten-Vorauszahlung",
    "HK_VORAUSZAHLUNG": "Heizkosten-Vorauszahlung",
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


def _payment_label(item):
    kind = item.get("payment_type", "")
    if kind == "NK_VORAUSZAHLUNG" and item.get("combined_advance"):
        return "Betriebskosten-Vorauszahlung inkl. Heizkosten"
    return _TYPE_LABELS.get(kind, kind)


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
        type_label = _payment_label(it)
        year, month = it.get("period_year"), it.get("period_month")
        period = _period_label(year, month)
        monthly_due = ''
        # Yearless payment rows can carry a dunning fallback date in the snapshot.
        if (it.get('payment_type') == 'HAUSGELD_VORSCHUSS'
                and int(year or 0) > 0 and month is not None and it.get('due_date')):
            due = date.fromisoformat(it['due_date']).strftime('%d.%m.%Y')
            monthly_due = f'<br/><span class="note">Fällig am {due}</span>'
        arrears_rows.append(
            f"<tr>"
            f"<td>{_esc(period)}{monthly_due}</td>"
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
    snapshot = notice.interest_snapshot
    segmented_interest = snapshot is not None and "premium" in snapshot
    new_format = segmented_interest or bool(
        notice.included_items and notice.included_items[0].get(NOTICE_FORMAT_KEY) == SEGMENTED_NOTICE_FORMAT
    )
    if new_format or (notice.fee_amount and notice.fee_amount > 0):
        fee_label = "Mahngebühr" if new_format else "Mahngebuehr"
        summary_rows.append(
            f"<tr class='summary'>"
            f"<td colspan='4'>{fee_label} (Stufe {notice.level})</td>"
            f"<td class='num'>{_eur(notice.fee_amount)}</td>"
            f"</tr>"
        )
    interest_section = ""
    if snapshot is not None:
        summary_rows.append(
            f"<tr class='summary'><td colspan='4'>Verzugszinsen</td>"
            f"<td class='num'>{_eur(notice.interest_amount)}</td></tr>"
        )
        if not segmented_interest:
            interest_rows = []
            for item in snapshot["items"]:
                label = _payment_label(item)
                due = date.fromisoformat(item["due_date"]).strftime("%d.%m.%Y")
                interest_start = ""
                if item.get("interest_start") and item["payment_type"] in ("KALTMIETE", "NK_VORAUSZAHLUNG"):
                    start = date.fromisoformat(item["interest_start"]).strftime("%d.%m.%Y")
                    interest_start = f"<br/>Zinsbeginn: {start}"
                interest_rows.append(
                    f"<tr><td>{_esc(_period_label(item['period_year'], item['period_month']))}<br/>{_esc(label)}</td>"
                    f"<td>{due}{interest_start}</td><td class='num'>{item['days']}</td>"
                    f"<td class='num'>{_eur(item['principal'])}</td>"
                    f"<td class='num'>{_eur(item['interest'])}</td></tr>"
                )
            base = _esc(snapshot["base_interest_rate"].replace(".", ","))
            annual = _esc(snapshot["annual_rate"].replace(".", ","))
            reference = date.fromisoformat(snapshot["reference_date"]).strftime("%d.%m.%Y")
            interest_section = (
                f"<h2>Verzugszinsen</h2><p>Basiszinssatz: {base} %; "
                f"effektiver Jahreszinssatz: {annual} % (Basis + 5 Prozentpunkte). "
                f"Stichtag: {reference}. Berechnung: Hauptforderung × Jahreszinssatz / 100 × Tage / 365. "
                "Keine Verzinsung von Gebühren oder Zinsen.</p>"
                "<table class='lines'><thead><tr><th>Position</th><th>Fälligkeit</th>"
                "<th>Tage</th><th>Hauptforderung</th><th>Zinsen</th></tr></thead>"
                f"<tbody>{''.join(interest_rows)}</tbody></table>"
            )
        else:
            interest_section = _segmented_interest(snapshot)
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

    # Tenancy/property identity, frozen at issue time (#331). Absent on
    # previously issued notices, which keep their established rendering.
    identity_rows = ""
    if dn.property_name:
        identity_rows += (
            f"<tr><td class='label'>Objekt</td><td>{_esc(dn.property_name)}</td></tr>"
        )
    if dn.unit_label:
        identity_rows += (
            f"<tr><td class='label'>Einheit</td><td>{_esc(dn.unit_label)}</td></tr>"
        )

    # Payment destination, frozen at issue time (#331). Without it the
    # established generic wording stays (previously issued notices).
    if dn.bank_iban and dn.bank_iban.strip():
        payment_sentence = """auf das folgende Konto
    zu ueberweisen:"""
        destination_block = f"""<table class="meta">
    <tr><td class="label">IBAN</td><td><strong>{_esc(dn.bank_iban)}</strong></td></tr>{f"<tr><td class='label'>BIC</td><td>{_esc(dn.bank_bic)}</td></tr>" if dn.bank_bic else ""}{f"<tr><td class='label'>Bank</td><td>{_esc(dn.bank_name)}</td></tr>" if dn.bank_name else ""}
  </table>"""
    else:
        payment_sentence = """auf unser Konto
    zu ueberweisen."""
        destination_block = ""
    # After the body text: nothing for legacy notices (byte-identical
    # rendering), the destination table for new ones.
    after_body = f"  {destination_block}\n" if destination_block else ""

    interest_hint = (
        "Verzugszinsen werden anhand des angegebenen Basiszinssatzes ausgewiesen."
        if snapshot is not None else "Verzugszinsen sind nicht enthalten."
    )
    if not new_format:
        footer = f"""<div class="hint">
    <em>{interest_hint} Mahngebühren müssen angemessen/vertraglich vereinbart sein.</em>
  </div>"""
    elif snapshot is None:
        footer = f'<p class="hint">{interest_hint}</p>'
    else:
        footer = ""
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
    <tr><td class="label">An</td><td>{addressee}</td></tr>{identity_rows}
    <tr><td class="label">Datum</td><td>{notice.issue_date.strftime('%d.%m.%Y')}</td></tr>
    <tr><td class="label">Zahlungsfrist</td><td>{notice.due_date.strftime('%d.%m.%Y')}</td></tr>
    <tr><td class="label">Mahnstufe</td><td>{notice.level} ({_esc(level_label)})</td></tr>
  </table>

  <div class="body-text">
    Sehr geehrte/r {_esc(dn.person_name)},<br/><br/>
    trotz Faelligkeit konnten wir fuer die nachfolgend aufgefuehrten
    Positionen leider keinen vollstaendigen Zahlungseingang feststellen.
    Wir bitten Sie, den ausstehenden Betrag bis zum
    <strong>{notice.due_date.strftime('%d.%m.%Y')}</strong> {payment_sentence}
  </div>

{after_body}  <table class="lines">
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

  {interest_section}

  {footer}
</body>
</html>"""


def _segmented_interest(snapshot: dict) -> str:
    premium = snapshot["premium"]
    paragraph = 2 if premium == 9 else 1
    reference = date.fromisoformat(snapshot["reference_date"]).strftime("%d.%m.%Y")
    rows = []
    for item in snapshot["items"]:
        label = _payment_label(item)
        period = _period_label(item["period_year"], item["period_month"])
        due = date.fromisoformat(item["due_date"]).strftime("%d.%m.%Y")
        for index, segment in enumerate(item["segments"]):
            start = date.fromisoformat(segment["start"]).strftime("%d.%m.%Y")
            end = date.fromisoformat(segment["end"]).strftime("%d.%m.%Y")
            base = _esc(segment["base_interest_rate"].replace(".", ","))
            annual = _esc(segment["annual_rate"].replace(".", ","))
            start_label = "Zinsbeginn: " if index == 0 else ""
            rows.append(
                f"<tr><td>{_esc(period)}<br/>{_esc(label)}<br/>Fällig: {due}</td>"
                f"<td>{start_label}{start}<br/>bis {end}</td>"
                f"<td>{base} %<br/>{annual} %</td><td class='num'>{_esc(str(segment['days']))}</td>"
                f"<td class='num'>{_eur(segment['principal'])}</td>"
                f"<td class='num'>{_eur(segment['interest'])}</td></tr>"
            )
    return (
        f"<h2>Verzugszinsen</h2><p>Basiszinssatz + {_esc(str(premium))} Prozentpunkte (§ 288 Abs. {paragraph} BGB). "
        f"Stichtag: {reference}. Je Abschnitt: Hauptforderung × Jahreszinssatz / 100 × Tage / 365.</p>"
        "<table class='lines'><thead><tr><th>Position</th><th>Zinszeitraum</th>"
        "<th>Basis / jährlich</th><th>Tage</th><th>Hauptforderung</th><th>Zinsen</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def render_dunning_pdf(
    notice: DunningNotice,
    names: DunningDisplayNames | None = None,
) -> bytes:
    """Render an issued dunning notice to PDF bytes (WeasyPrint)."""
    html_str = build_dunning_html(notice, names)
    return render_pdf(html_str)
