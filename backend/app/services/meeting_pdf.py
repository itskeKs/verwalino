"""Owners-meeting PDF rendering (M8).

Two documents:
  1. Niederschrift (minutes/protocol): TOPs + Beschlüsse + Ergebnisse.
  2. Beschluss-Sammlung (resolution register extract): per WEG property.

STRICTLY separated from the service (SDD section 16): this layer READS
the provided data and renders it; it computes NOTHING.

All addressees use NAMES (no UUIDs in the rendered document), consistent
with settlement_pdf.py and dunning_pdf.py.

[section 18/fachlich] The exact layout and legal wording are product/legal
decisions. A functional standard layout is provided here.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from pathlib import Path

from app.core.pdf import render_pdf
from app.models.agenda_item import AgendaItem
from app.models.owners_meeting import OwnersMeeting
from app.models.resolution import Resolution
from app.models.resolution_register_entry import ResolutionRegisterEntry


# ---------------------------------------------------------------------------
# Display names (resolved at render time from Stammdaten)
# ---------------------------------------------------------------------------
@dataclass
class MeetingDisplayNames:
    property_name: str = "---"
    property_address: str = ""


# ---------------------------------------------------------------------------
# CSS (loaded once)
# ---------------------------------------------------------------------------
_NIEDERSCHRIFT_CSS = (
    Path(__file__).resolve().parent.parent / "templates" / "niederschrift.css"
).read_text(encoding="utf-8")

_REGISTER_CSS = (
    Path(__file__).resolve().parent.parent / "templates" / "beschluss_sammlung.css"
).read_text(encoding="utf-8")


def _esc(text: str | None) -> str:
    return html.escape(text or "")


_MEETING_TYPE_LABELS = {
    "ORDENTLICH": "Ordentliche Eigentuemerversammlung",
    "AUSSERORDENTLICH": "Ausserordentliche Eigentuemerversammlung",
}

_RESULT_LABELS = {
    "ANGENOMMEN": "Angenommen",
    "ABGELEHNT": "Abgelehnt",
}


# ===================================================================
# 1. Niederschrift (minutes/protocol)
# ===================================================================
def build_niederschrift_html(
    meeting: OwnersMeeting,
    agenda_items: list[AgendaItem],
    resolutions: list[Resolution],
    names: MeetingDisplayNames | None = None,
) -> str:
    """Build the Niederschrift HTML for a meeting."""
    dn = names or MeetingDisplayNames()
    type_label = _MEETING_TYPE_LABELS.get(meeting.meeting_type, meeting.meeting_type)

    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    # Build a lookup: agenda_item_id -> [resolutions]
    res_by_item: dict[str, list[Resolution]] = {}
    standalone_resolutions: list[Resolution] = []
    for r in resolutions:
        if r.agenda_item_id:
            key = str(r.agenda_item_id)
            res_by_item.setdefault(key, []).append(r)
        else:
            standalone_resolutions.append(r)

    # Build TOP sections
    top_sections = []
    for ai in sorted(agenda_items, key=lambda a: a.item_number):
        res_html = ""
        linked = res_by_item.get(str(ai.id), [])
        for r in linked:
            res_html += _render_resolution_block(r)

        desc = ""
        if ai.description:
            desc = f"<div class='top-description'>{_esc(ai.description)}</div>"

        top_sections.append(
            f"<div class='top'>"
            f"<div class='top-header'>TOP {ai.item_number}: {_esc(ai.title)}</div>"
            f"{desc}"
            f"{res_html}"
            f"</div>"
        )

    # Standalone resolutions
    standalone_html = ""
    if standalone_resolutions:
        standalone_html = "<div class='standalone-resolutions'><h2>Weitere Beschluesse</h2>"
        for r in standalone_resolutions:
            standalone_html += _render_resolution_block(r)
        standalone_html += "</div>"

    # Protocol text
    protocol_section = ""
    if meeting.protocol_text:
        protocol_section = (
            f"<h2>Protokoll</h2>"
            f"<div>{_esc(meeting.protocol_text)}</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<style>{_NIEDERSCHRIFT_CSS}</style>
</head>
<body>
  <h1>Niederschrift</h1>
  <div class="subtitle">{_esc(type_label)}</div>

  <table class="meta">
    <tr><td class="label">Objekt</td><td>{property_display}</td></tr>
    <tr><td class="label">Datum</td><td>{meeting.meeting_date.strftime('%d.%m.%Y')}</td></tr>
    <tr><td class="label">Ort</td><td>{_esc(meeting.location)}</td></tr>
    <tr><td class="label">Art</td><td>{_esc(type_label)}</td></tr>
    <tr><td class="label">Status</td><td>{_esc(meeting.status)}</td></tr>
  </table>

  <h2>Tagesordnung</h2>
  {''.join(top_sections)}

  {standalone_html}
  {protocol_section}

  <div class="hint">
    <em>[section 18/fachlich] Teilnehmerliste, Vollmachten, Stimmrechtsverhaeltnisse
    und ggf. Unterschriften sind fachlich/juristisch abzustimmen. Dieses Dokument
    ist ein automatisch erzeugter Protokollentwurf.</em>
  </div>
</body>
</html>"""


def _render_resolution_block(r: Resolution) -> str:
    """Render a single resolution as an HTML block."""
    result_label = _RESULT_LABELS.get(r.result, r.result)
    css_class = "result-angenommen" if r.result == "ANGENOMMEN" else "result-abgelehnt"

    votes = (
        f"Ja: {r.votes_yes} / Nein: {r.votes_no} / Enthaltung: {r.votes_abstain}"
    )
    if r.votes_yes_mea is not None:
        mea_info = (
            f" (MEA: Ja {r.votes_yes_mea}, Nein {r.votes_no_mea}, "
            f"Enthaltung {r.votes_abstain_mea})"
        )
        votes += mea_info

    majority = ""
    if r.majority_type:
        majority = f" | Mehrheit: {_esc(r.majority_type)}"

    return (
        f"<div class='resolution'>"
        f"<div class='wording'>{_esc(r.wording)}</div>"
        f"<div class='votes'>{_esc(votes)}{majority}</div>"
        f"<div class='result {css_class}'>Ergebnis: {_esc(result_label)}</div>"
        f"</div>"
    )


def render_niederschrift_pdf(
    meeting: OwnersMeeting,
    agenda_items: list[AgendaItem],
    resolutions: list[Resolution],
    names: MeetingDisplayNames | None = None,
) -> bytes:
    """Render the Niederschrift to PDF bytes (WeasyPrint)."""
    html_str = build_niederschrift_html(meeting, agenda_items, resolutions, names)
    return render_pdf(html_str)


# ===================================================================
# 2. Beschluss-Sammlung (resolution register extract)
# ===================================================================
def build_register_html(
    entries: list[ResolutionRegisterEntry],
    names: MeetingDisplayNames | None = None,
) -> str:
    """Build the Beschluss-Sammlung HTML for a WEG property."""
    dn = names or MeetingDisplayNames()

    property_display = _esc(dn.property_name)
    if dn.property_address:
        property_display += f"<br/><span class='meta-sub'>{_esc(dn.property_address)}</span>"

    rows = []
    for e in sorted(entries, key=lambda x: x.sequence_number):
        challenge = ""
        if e.challenge_annotation:
            challenge = (
                f"<div class='challenge'>Anfechtungs-/Aufhebungsvermerk: "
                f"{_esc(e.challenge_annotation)}</div>"
            )
        rows.append(
            f"<tr>"
            f"<td class='num'>{e.sequence_number}</td>"
            f"<td class='date'>{e.entry_date.strftime('%d.%m.%Y')}</td>"
            f"<td>{_esc(e.location)}</td>"
            f"<td class='wording'>{_esc(e.wording)}{challenge}</td>"
            f"</tr>"
        )

    count_text = f"{len(entries)} Eintraege" if entries else "keine Eintraege"

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8"/>
<style>{_REGISTER_CSS}</style>
</head>
<body>
  <h1>Beschluss-Sammlung</h1>
  <div class="subtitle">gemaess WEG section 24 Abs. 7 ({count_text})</div>

  <table class="meta">
    <tr><td class="label">Objekt / WEG</td><td>{property_display}</td></tr>
    <tr><td class="label">Stand</td><td>{_esc(count_text)}</td></tr>
  </table>

  <table class="register">
    <thead>
      <tr>
        <th class="num">Nr.</th>
        <th class="date">Datum</th>
        <th>Ort der Versammlung</th>
        <th>Wortlaut des Beschlusses</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>

  <div class="hint">
    <em>[section 18/fachlich] Die Beschluss-Sammlung ist gemaess WEG section 24 Abs. 7
    vom Verwalter zu fuehren und jedem Eigentuemer auf Verlangen Einsicht zu gewaehren.
    Anfechtungs- und Aufhebungsvermerke werden nachgetragen, wenn ein Beschluss
    gerichtlich angefochten oder aufgehoben wird.</em>
  </div>
</body>
</html>"""


def render_register_pdf(
    entries: list[ResolutionRegisterEntry],
    names: MeetingDisplayNames | None = None,
) -> bytes:
    """Render the Beschluss-Sammlung to PDF bytes (WeasyPrint)."""
    html_str = build_register_html(entries, names)
    return render_pdf(html_str)
