"""BK-Abrechnung PDF rendering (M3, section 8.2).

STRICTLY separated from the engine (SDD section 16): this layer READS the
finalized settlement_run + settlement_line and renders them; it computes
NOTHING (a test asserts PDF values == settlement_line values).  Renders only a
finalized run (the versendete Dokument must equal the frozen snapshot).

All BGH-Pflichtangaben (section 8.1) are present:
  Objekt + Abrechnungszeitraum; Adressat/Einheit; Gesamtkosten je Kostenart;
  angewandter Umlageschluessel je Position; Berechnung des Anteils (Basis +
  Zeitanteil); Abzug der Vorauszahlungen; Nachzahlung/Guthaben; Hinweis auf
  externe Werte.

[OFFEN/§18] O3-4: the exact layout / the § 556 III BGB Einwendungsfrist text is
a product/fachliche decision. A functional, BGH-complete standard layout is
used here with a clearly-marked provisional Einwendungsfrist hint.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun


@dataclass
class SettlementDisplayNames:
    """Human-readable names resolved at render time from Stammdaten.

    These are NOT stored in the snapshot; they are resolved on-the-fly from the
    IDs on the settlement_run.  If a name cannot be resolved (e.g. deleted
    person), the fallback is a dash.
    """

    property_name: str = "—"
    property_address: str = ""
    unit_label: str = "—"
    tenant_name: str = "—"
    lease_info: str = "—"

_CSS = (Path(__file__).resolve().parent.parent / "templates" / "bk_abrechnung.css").read_text(
    encoding="utf-8"
)

# Distribution-key display labels (German, prueffest).
_KEY_LABELS = {
    "MEA": "Miteigentumsanteil (MEA)",
    "WOHNFLAECHE": "Wohnflaeche",
    "BEHEIZTE_FLAECHE": "beheizte Flaeche",
    "EINHEIT": "je Einheit",
    "PERSONENZAHL": "Personenzahl",
    "VERBRAUCH": "Verbrauch",
    "EXTERNE_ABRECHNUNG": "externe Abrechnung",
    "MANUELLER_BETRAG": "manueller Betrag",
}


def _eur(value: Decimal | None) -> str:
    """Format a Decimal as German money: 1.234,56 EUR. None -> '-'."""
    if value is None:
        return "-"
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    s = f"{abs(q):,.2f}"  # 1,234.56
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")  # -> 1.234,56
    return ("-" if neg else "") + s + " EUR"


def _quota(value: Decimal | None) -> str:
    if value is None:
        return ""
    # Trim trailing zeros for display, German decimal comma.
    s = format(value.normalize(), "f")
    return s.replace(".", ",")


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def build_settlement_html(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: SettlementDisplayNames | None = None,
) -> str:
    """Build the BK-Abrechnung HTML from the finalized run + lines (no compute).

    ``names`` provides human-readable display names resolved from Stammdaten at
    render time.  If omitted, falls back to a dash placeholder (never raw UUIDs
    in the rendered document).
    """
    dn = names or SettlementDisplayNames()
    ordered = sorted(lines, key=lambda l: l.sort_order)

    cost_rows = []
    for ln in ordered:
        if ln.line_type != "COST":
            continue
        key_label = _KEY_LABELS.get(ln.distribution_key or "", ln.distribution_key or "")
        # Anteil-Basis (Rechenweg): key base share/total + time share.
        basis = ""
        if ln.distribution_base_share is not None and ln.distribution_base_total is not None:
            basis = f"{_quota(ln.distribution_base_share)} / {_quota(ln.distribution_base_total)}"
        zeit = ""
        if ln.time_share_days is not None and ln.time_share_total_days is not None:
            zeit = f"{ln.time_share_days} / {ln.time_share_total_days} Tage"
        note = f'<div class="note">{_esc(ln.note)}</div>' if ln.note else ""
        cost_rows.append(
            f"<tr>"
            f"<td>{_esc(ln.label)}{note}</td>"
            f"<td>{_esc(key_label)}</td>"
            f"<td class='num'>{_eur(ln.total_amount)}</td>"
            f"<td>{_esc(basis)}</td>"
            f"<td>{_esc(zeit)}</td>"
            f"<td class='num'>{_eur(ln.amount)}</td>"
            f"</tr>"
        )

    advance = next((l for l in ordered if l.line_type == "ADVANCE_PAYMENT"), None)
    balance = next((l for l in ordered if l.line_type == "BALANCE"), None)

    summary_rows = []
    summary_rows.append(
        f"<tr class='summary'>"
        f"<td colspan='5'>Summe umlagefaehige Kosten (Ihr Anteil)</td>"
        f"<td class='num'>{_eur(run.total_cost)}</td></tr>"
    )
    if advance is not None:
        summary_rows.append(
            f"<tr class='summary'>"
            f"<td colspan='5'>abzueglich {_esc(advance.label)}</td>"
            f"<td class='num'>{_eur(advance.amount)}</td></tr>"
        )
    if balance is not None:
        summary_rows.append(
            f"<tr class='balance'>"
            f"<td colspan='5'>{_esc(balance.label)}</td>"
            f"<td class='num'>{_eur(balance.amount)}</td></tr>"
        )

    integrity = ""
    if run.snapshot_sha256:
        integrity = (
            f"<div class='integrity'>Snapshot-Pruefsumme (SHA-256): "
            f"{_esc(run.snapshot_sha256)}<br>Engine-Version: {_esc(run.engine_version)}</div>"
        )

    # Build the property display: "Name, Strasse, PLZ Ort"
    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<style>{_CSS}</style>
</head>
<body>
  <h1>Betriebskostenabrechnung</h1>
  <div class="subtitle">Abrechnungszeitraum {run.period_start.strftime('%d.%m.%Y')}
     bis {run.period_end.strftime('%d.%m.%Y')}</div>

  <table class="meta">
    <tr><td class="label">Objekt</td><td>{property_display}</td></tr>
    <tr><td class="label">Einheit</td><td>{_esc(dn.unit_label)}</td></tr>
    <tr><td class="label">Mieter</td><td>{_esc(dn.tenant_name)}</td></tr>
    <tr><td class="label">Mietvertrag</td><td>{_esc(dn.lease_info)}</td></tr>
  </table>

  <table class="lines">
    <thead>
      <tr>
        <th>Kostenart</th>
        <th>Umlageschluessel</th>
        <th class="num">Gesamtkosten</th>
        <th>Ihr Anteil (Basis)</th>
        <th>Zeitanteil</th>
        <th class="num">Ihr Betrag</th>
      </tr>
    </thead>
    <tbody>
      {''.join(cost_rows)}
      {''.join(summary_rows)}
    </tbody>
  </table>

  <div class="hint">
    Die Gesamtkosten je Kostenart, der jeweils angewandte Umlageschluessel, die
    Berechnung Ihres Anteils (Anteilsbasis und Zeitanteil), der Abzug der
    geleisteten Vorauszahlungen sowie die sich ergebende Nachzahlung bzw. das
    Guthaben sind vorstehend ausgewiesen. Bei extern abgerechneten Positionen
    (z. B. Heizung/Warmwasser) wurde der Mieteranteil aus der Abrechnung des
    Messdienstes uebernommen.
    <br/><br/>
    <em>Hinweis (vorlaeufig, § 556 Abs. 3 BGB): Einwendungen gegen diese
    Abrechnung sind dem Vermieter spaetestens bis zum Ablauf des zwoelften
    Monats nach Zugang der Abrechnung mitzuteilen. Der genaue Hinweistext und
    das endgueltige Layout sind fachlich abzustimmen.</em>
  </div>
  {integrity}
</body>
</html>"""


def render_settlement_pdf(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: SettlementDisplayNames | None = None,
) -> bytes:
    """Render the finalized run + lines to PDF bytes (WeasyPrint, no compute)."""
    html_str = build_settlement_html(run, lines, names)
    return render_pdf(html_str)
