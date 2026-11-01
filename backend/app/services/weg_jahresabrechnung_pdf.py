"""WEG-Hausgeld-/Jahresabrechnung PDF rendering (M4, m4 section 8.1).

STRICTLY separated from the engine (SDD section 16): READS the finalized
settlement_run + settlement_line and renders them; computes NOTHING (a test
asserts PDF values == settlement_line values). Renders only a finalized run.

Formal WEG requirements (m4 section 8.1, SDD section 11): object + period;
owner + units; Gesamtkosten je Kostenart; angewandter Schluessel; MEA-Anteil;
getrennte Subtotals umlagefaehig/nicht/Ruecklage; Soll-Vorschuesse;
ABRECHNUNGSSPITZE (Nachschuss/Guthaben, beschlussrelevant, hervorgehoben);
Rücklagen-Entwicklung; Snapshot-Pruefsumme/Engine-Version. Names, not UUIDs.

[§18/fachlich F4-11] The exact layout + a possible § 35a EStG-Ausweis are a
product/fachliche decision; a functional, WEG-complete standard layout is used
with a clearly-marked provisional note. Beschlussgegenstand = the Spitze (WEMoG).
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.settlement_line import SettlementLine
from app.models.settlement_run import SettlementRun

_CSS = (Path(__file__).resolve().parent.parent / "templates" / "weg_abrechnung.css").read_text(
    encoding="utf-8"
)

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

# Object-wide reserve development line types (not per-owner).
_RESERVE_DEV_TYPES = ("RESERVE_OPENING", "RESERVE_WITHDRAWAL", "RESERVE_CLOSING", "SUBTOTAL",
                      "RESERVE_LATE_CASH", "RESERVE_ADJUSTMENT")


@dataclass
class WegDisplayNames:
    """Human-readable names resolved at render time (never stored in snapshot)."""

    property_name: str = "—"
    property_address: str = ""
    owner_names: dict[str, str] = field(default_factory=dict)


def _eur(value: Decimal | None) -> str:
    if value is None:
        return "-"
    q = value.quantize(Decimal("0.01"))
    neg = q < 0
    s = f"{abs(q):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return ("-" if neg else "") + s + " EUR"


def _quota(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f").replace(".", ",")


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def build_weg_jahresabrechnung_html(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: WegDisplayNames | None = None,
) -> str:
    dn = names or WegDisplayNames()
    ordered = sorted(lines, key=lambda l: l.sort_order)

    # Group per-owner lines; collect object-wide reserve development separately.
    per_owner: dict[str, list[SettlementLine]] = {}
    reserve_dev: list[SettlementLine] = []
    reserve_contrib_total: SettlementLine | None = None
    for ln in ordered:
        if ln.addressee_owner_profile_id is None:
            if ln.line_type in _RESERVE_DEV_TYPES:
                reserve_dev.append(ln)
            elif ln.line_type == "RESERVE_CONTRIBUTION":
                reserve_contrib_total = ln
            continue
        per_owner.setdefault(str(ln.addressee_owner_profile_id), []).append(ln)

    owner_blocks = []
    for owner_id, olines in per_owner.items():
        owner_name = dn.owner_names.get(owner_id, "Eigentuemer")
        rows = []
        for ln in olines:
            if ln.line_type in ("COST", "RESERVE_CONTRIBUTION"):
                key_label = _KEY_LABELS.get(ln.distribution_key or "", ln.distribution_key or "")
                basis = ""
                if ln.distribution_base_share is not None and ln.distribution_base_total is not None:
                    basis = f"{_quota(ln.distribution_base_share)} / {_quota(ln.distribution_base_total)}"
                zeit = ""
                if ln.time_share_days is not None and ln.time_share_total_days is not None:
                    zeit = f"{ln.time_share_days} / {ln.time_share_total_days} Tage"
                note = f'<div class="note">{_esc(ln.note)}</div>' if ln.note else ""
                cls = "reserve" if ln.line_type == "RESERVE_CONTRIBUTION" else ""
                rows.append(
                    f"<tr class='{cls}'><td>{_esc(ln.label)}{note}</td>"
                    f"<td>{_esc(key_label)}</td>"
                    f"<td class='num'>{_eur(ln.total_amount)}</td>"
                    f"<td>{_esc(basis)}</td><td>{_esc(zeit)}</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
            elif ln.line_type == "SUBTOTAL":
                rows.append(
                    f"<tr class='subtotal'><td colspan='5'>{_esc(ln.label)}</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
            elif ln.line_type == "OWNER_COST_TOTAL":
                rows.append(
                    f"<tr class='cost-total'><td colspan='5'>{_esc(ln.label)}</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
            elif ln.line_type == "PLAN_ADVANCE":
                rows.append(
                    f"<tr class='plan'><td colspan='5'>abzueglich {_esc(ln.label)}</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
            elif ln.line_type == "ABRECHNUNGSSPITZE":
                label = "Nachschuss" if ln.amount > 0 else ("Guthaben" if ln.amount < 0 else "ausgeglichen")
                rows.append(
                    f"<tr class='spitze'><td colspan='5'>Abrechnungsspitze "
                    f"({label}, beschlussrelevant)</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
            elif ln.line_type == "ADVANCE_PAYMENT":
                rows.append(
                    f"<tr class='plan'><td colspan='5'>{_esc(ln.label)}</td>"
                    f"<td class='num'>{_eur(ln.amount)}</td></tr>"
                )
        owner_blocks.append(
            f"<div class='owner-block'>"
            f"<div class='owner-name'>{_esc(owner_name)}</div>"
            f"<table class='lines'><thead><tr>"
            f"<th>Kostenart</th><th>Schluessel</th><th class='num'>Gesamtkosten</th>"
            f"<th>Anteil (Basis)</th><th>Zeitanteil</th><th class='num'>Ihr Betrag</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )

    # Object-wide reserve development table.
    reserve_rows = []
    if reserve_contrib_total is not None:
        reserve_dev_all = sorted(
            reserve_dev + [reserve_contrib_total], key=lambda l: l.sort_order
        )
    else:
        reserve_dev_all = reserve_dev
    for ln in reserve_dev_all:
        cls = "closing" if ln.line_type == "RESERVE_CLOSING" else ""
        note = f"<div class='note'>{_esc(ln.note)}</div>" if ln.note else ""
        reserve_rows.append(
            f"<tr class='{cls}'><td>{_esc(ln.label)}{note}</td>"
            f"<td class='num'>{_eur(ln.amount)}</td></tr>"
        )
    reserve_section = ""
    if reserve_rows:
        overdrawn = any(ln.line_type == 'RESERVE_CLOSING' and ln.amount < 0 for ln in reserve_dev)
        reserve_section = (
            "<h2>Entwicklung der Erhaltungsruecklage</h2>"
            + ("<p class='hint'><strong>Die Rücklage ist überzogen.</strong> Bitte prüfen Sie die Finanzierung und die Belege.</p>" if overdrawn else "") +
            "<table class='lines'><tbody>" + "".join(reserve_rows) + "</tbody></table>"
        )

    integrity = ""
    if run.snapshot_sha256:
        integrity = (
            f"<div class='integrity'>Snapshot-Pruefsumme (SHA-256): "
            f"{_esc(run.snapshot_sha256)}<br>Engine-Version: {_esc(run.engine_version)}</div>"
        )

    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"/><style>{_CSS}</style></head>
<body>
  <h1>WEG-Jahresabrechnung (Hausgeldabrechnung)</h1>
  <div class="subtitle">Abrechnungszeitraum {run.period_start.strftime('%d.%m.%Y')}
     bis {run.period_end.strftime('%d.%m.%Y')}</div>

  <table class="meta">
    <tr><td class="label">Objekt</td><td>{property_display}</td></tr>
  </table>

  {''.join(owner_blocks)}

  {reserve_section}

  <div class="hint">
    Ausgewiesen sind die Gesamtkosten je Kostenart, der jeweils angewandte
    Verteilerschluessel, der Anteil jedes Eigentuemers (Anteilsbasis und ggf.
    Zeitanteil bei unterjaehrigem Eigentuemerwechsel), die getrennten Summen
    umlagefaehiger und nicht umlagefaehiger Kosten sowie die Zufuehrung zur
    Erhaltungsruecklage (Vermoegensbildung, keine Kostenumlage). Die
    <strong>Abrechnungsspitze</strong> (anteilige Ist-Kosten abzueglich der im
    Wirtschaftsplan beschlossenen Soll-Vorschuesse) ist nach dem WEMoG
    (§ 28 Abs. 2 WEG) der beschlussrelevante Betrag (Nachschuss bzw.
    Anpassungsguthaben); ein etwaiger Zahlungsstand wird hiervon getrennt
    gefuehrt.
  </div>
  <div class="disclaimer">
    Hinweis (vorlaeufig, [§18/fachlich]): Das endgueltige Layout, der genaue
    Beschlusswortlaut und ein etwaiger Ausweis haushaltsnaher Dienstleistungen
    nach § 35a EStG sind fachlich abzustimmen. Diese Abrechnung ist vor
    Beschlussfassung durch fachkundige Stelle (Steuerberater/Fachanwalt fuer
    WEG-Recht) zu pruefen.
  </div>
  {integrity}
</body>
</html>"""


def render_weg_jahresabrechnung_pdf(
    run: SettlementRun,
    lines: list[SettlementLine],
    names: WegDisplayNames | None = None,
) -> bytes:
    return render_pdf(build_weg_jahresabrechnung_html(run, lines, names))
