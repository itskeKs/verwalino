"""Wirtschaftsplan PDF rendering (M4, m4 section 8.2).

STRICTLY separated from the engine: READS the finalized economic_plan +
economic_plan_line, computes NOTHING (a test asserts PDF values == line values).
Renders only a finalized plan.

Shows: Planbetrag je Kategorie (+ Subtotals), je-Eigentuemer-Jahresvorschuss +
monthly Hausgeldbetrag, Ruecklagenzufuehrung separat; the note "Vorschlag/
Unterlage fuer die Beschlussfassung, kein Beschluss" (SDD section 10). Names,
not UUIDs.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.economic_plan import EconomicPlan
from app.models.economic_plan_line import EconomicPlanLine

_CSS = (Path(__file__).resolve().parent.parent / "templates" / "weg_abrechnung.css").read_text(
    encoding="utf-8"
)


@dataclass
class WirtschaftsplanDisplayNames:
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


def _esc(text: str | None) -> str:
    return html.escape(text or "")


def build_wirtschaftsplan_html(
    plan: EconomicPlan,
    lines: list[EconomicPlanLine],
    names: WirtschaftsplanDisplayNames | None = None,
) -> str:
    dn = names or WirtschaftsplanDisplayNames()
    ordered = sorted(lines, key=lambda l: l.sort_order)

    cost_rows = []
    reserve_line: EconomicPlanLine | None = None
    advance_lines: list[EconomicPlanLine] = []
    for ln in ordered:
        if ln.line_type == "PLANNED_COST":
            note = f"<div class='note'>{_esc(ln.note)}</div>" if ln.note else ""
            cost_rows.append(
                f"<tr><td>{_esc(ln.label)}{note}</td>"
                f"<td class='num'>{_eur(ln.planned_amount)}</td></tr>"
            )
        elif ln.line_type == "RESERVE_CONTRIBUTION":
            reserve_line = ln
        elif ln.line_type == "OWNER_ADVANCE":
            advance_lines.append(ln)

    if reserve_line is not None:
        cost_rows.append(
            f"<tr class='reserve'><td>{_esc(reserve_line.label)}"
            f"<div class='note'>{_esc(reserve_line.note)}</div></td>"
            f"<td class='num'>{_eur(reserve_line.planned_amount)}</td></tr>"
        )

    advance_rows = []
    for ln in advance_lines:
        owner_name = dn.owner_names.get(str(ln.owner_profile_id), "Eigentuemer")
        advance_rows.append(
            f"<tr><td>{_esc(owner_name)}</td>"
            f"<td class='num'>{_eur(ln.planned_amount)}</td>"
            f"<td class='num'>{_eur(ln.monthly_amount)}</td></tr>"
        )

    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    integrity = ""
    if plan.snapshot_sha256:
        integrity = (
            f"<div class='integrity'>Snapshot-Pruefsumme (SHA-256): "
            f"{_esc(plan.snapshot_sha256)}<br>Engine-Version: {_esc(plan.engine_version)}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head><meta charset="utf-8"/><style>{_CSS}</style></head>
<body>
  <h1>Wirtschaftsplan {plan.plan_year}</h1>
  <div class="subtitle">Planzeitraum {plan.period_start.strftime('%d.%m.%Y')}
     bis {plan.period_end.strftime('%d.%m.%Y')}</div>

  <table class="meta">
    <tr><td class="label">Objekt</td><td>{property_display}</td></tr>
  </table>

  <h2>Geplante Kosten je Kostenart</h2>
  <table class="lines">
    <thead><tr><th>Kostenart</th><th class="num">Planbetrag (Jahr)</th></tr></thead>
    <tbody>{''.join(cost_rows)}</tbody>
  </table>

  <h2>Hausgeldvorschuesse je Eigentuemer</h2>
  <table class="lines">
    <thead><tr><th>Eigentuemer</th><th class="num">Jahresvorschuss</th>
      <th class="num">Monatlich (Hausgeld)</th></tr></thead>
    <tbody>{''.join(advance_rows)}</tbody>
  </table>

  <div class="hint">
    Die Hausgeldvorschuesse sind nach Miteigentumsanteilen (MEA) verteilt und in
    12 gleiche Monatsraten umgelegt (kaufmaennisch cent-genau; die Summe der 12
    Raten ergibt exakt den Jahresvorschuss). Die Zufuehrung zur
    Erhaltungsruecklage ist Vermoegensbildung und keine Kostenumlage.
  </div>
  <div class="disclaimer">
    Hinweis (SDD section 10): Der Wirtschaftsplan ist eine <strong>Unterlage/ein
    Vorschlag fuer die Beschlussfassung</strong> der Eigentuemerversammlung und
    selbst <strong>kein Beschluss</strong>. Das endgueltige Layout ist fachlich
    abzustimmen.
  </div>
  {integrity}
</body>
</html>"""


def render_wirtschaftsplan_pdf(
    plan: EconomicPlan,
    lines: list[EconomicPlanLine],
    names: WirtschaftsplanDisplayNames | None = None,
) -> bytes:
    return render_pdf(build_wirtschaftsplan_html(plan, lines, names))
