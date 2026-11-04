"""Smoke tests for the PDF rendering infrastructure.

These tests verify:
  1. The WeasyPrint renderer produces valid PDF bytes from sample HTML.
  2. The Protocol / swappable abstraction works.
  3. The output starts with the PDF magic bytes (%PDF).

No database or backend server needed -- pure unit tests.
"""

from pathlib import Path
from datetime import date
from decimal import Decimal
from io import BytesIO
from uuid import UUID

import pdfplumber
import pytest

from app.core.pdf import (
    PdfRenderer,
    WeasyPrintRenderer,
    render_pdf,
    get_renderer,
    set_renderer,
)


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

AUTHOR_MARKERS = ("ADR-", "SDD", "[§", "[section", "Ausbaustufe", " section ",
                  "abzustimmen", "vorlaeufig", "provisorisch", "MVP")


def recipient_documents():
    """Synthetic 2026 examples with stored amounts, never a production database."""
    from app.models.dunning_notice import DunningNotice
    from app.models.economic_plan import EconomicPlan
    from app.models.economic_plan_line import EconomicPlanLine
    from app.models.owners_meeting import OwnersMeeting
    from app.models.resolution_register_entry import ResolutionRegisterEntry
    from app.models.settlement_line import SettlementLine
    from app.models.settlement_run import SettlementRun
    from app.services.dunning_pdf import build_dunning_html, DunningDisplayNames
    from app.services.meeting_pdf import (
        build_invitation_html, build_niederschrift_html, build_register_html, MeetingDisplayNames,
    )
    from app.services.vermoegensbericht_pdf import build_vermoegensbericht_html, VermoegensberichtDisplayNames
    from app.services.weg_jahresabrechnung_pdf import build_weg_jahresabrechnung_html, WegDisplayNames
    from app.services.wirtschaftsplan_pdf import build_wirtschaftsplan_html, WirtschaftsplanDisplayNames

    start, end = date(2026, 1, 1), date(2026, 12, 31)
    owner = UUID(int=1)
    run = SettlementRun(period_start=start, period_end=end, snapshot_payload={},
                        snapshot_sha256="a" * 64, engine_version="m4.1.0")
    asset_lines = [SettlementLine(line_type=kind, label=label, amount=Decimal(amount),
                                  sort_order=i, note=note) for i, (kind, label, amount, note) in enumerate([
        ("RESERVE_OPENING", "Anfangsbestand", "20000.00", "Anfangsbestand laut Rücklagenbuch"),
        ("RESERVE_CONTRIBUTION", "Zuführung", "6000.00", None),
        ("RESERVE_WITHDRAWAL", "Entnahmen", "8000.00", "Entnahmen laut Rücklagenbuch"),
        ("RESERVE_CLOSING", "Endbestand", "18000.00", None),
        ("BANK_BALANCE", "Girokonto", "15000.00", "Stichtagssaldo (Verwalter-Angabe, kein Buchungssaldo)"),
        ("RECEIVABLE", "Hausgeldrückstand", "500.00", None),
        ("PAYABLE", "Offene Handwerkerrechnung", "1200.00", None),
    ])]
    legacy_lines = [SettlementLine(line_type=kind, label=label, amount=Decimal(amount),
                                   sort_order=i, note=note) for i, (kind, label, amount, note) in enumerate([
        ("RESERVE_OPENING", "Anfangsbestand", "20000.00", "Anfangsbestand (in dieser Ausbaustufe manuell erfasst)"),
        ("RESERVE_WITHDRAWAL", "Entnahmen", "8000.00", "Entnahmen im Zeitraum (in dieser Ausbaustufe manuell erfasst)"),
    ])]
    plan = EconomicPlan(plan_year=2026, period_start=start, period_end=end,
                        snapshot_sha256="b" * 64, engine_version="m4.1.0")
    plan_lines = [EconomicPlanLine(line_type="PLANNED_COST", label="Gartenpflege", sort_order=0,
                                   planned_amount=Decimal("4000.00")),
                  EconomicPlanLine(line_type="RESERVE_CONTRIBUTION", label="Erhaltungsrücklage", sort_order=1,
                                   planned_amount=Decimal("2000.00"), note="Vermögensbildung (keine Kostenumlage)"),
                  EconomicPlanLine(line_type="OWNER_ADVANCE", owner_profile_id=owner, sort_order=2,
                                   planned_amount=Decimal("6000.00"), monthly_amount=Decimal("500.00"))]
    annual_lines = [SettlementLine(line_type="OWNER_COST_TOTAL", label="Ist-Kosten", sort_order=0,
                                   amount=Decimal("4000.00"), addressee_owner_profile_id=owner)]
    notice = DunningNotice(level=2, issue_date=date(2026, 11, 1), due_date=date(2026, 11, 15),
                           principal_amount=Decimal("100.00"), fee_amount=Decimal("2.50"),
                           total_amount=Decimal("102.50"), interest_snapshot=None, included_items=[{
                               "payment_type": "KALTMIETE", "period_year": 2026, "period_month": 4,
                               "soll": "800.00", "ist": "700.00", "open_amount": "100.00",
                           }])
    interest_notice = DunningNotice(level=2, issue_date=notice.issue_date, due_date=notice.due_date,
        principal_amount=notice.principal_amount, fee_amount=notice.fee_amount,
        total_amount=Decimal("102.68"), interest_amount=Decimal("0.18"),
        included_items=notice.included_items, interest_snapshot={
            "base_interest_rate": "1.52", "annual_rate": "6.52", "reference_date": "2026-10-31", "items": [{
                "payment_type": "KALTMIETE", "period_year": 2026, "period_month": 10,
                "due_date": "2026-10-21", "days": 10, "principal": "100.00", "interest": "0.18",
            }],
        })
    meeting = OwnersMeeting(meeting_date=date(2026, 11, 30), location="Gemeinschaftsraum",
                            meeting_type="ORDENTLICH", status="HELD", protocol_text="Die Sitzung ist eröffnet.",
                            invitation_text="Wir laden Sie zur Versammlung ein.")
    entries = [ResolutionRegisterEntry(sequence_number=1, entry_date=date(2026, 11, 30),
                location=meeting.location, wording="Der Wirtschaftsplan wird beschlossen.", challenge_annotation=None)]
    property_name = "Gartenstraße 5"
    meeting_names = MeetingDisplayNames(property_name=property_name)
    return {
        "asset_report": build_vermoegensbericht_html(run, asset_lines, VermoegensberichtDisplayNames(property_name=property_name)),
        "legacy_asset_report": build_vermoegensbericht_html(run, legacy_lines, VermoegensberichtDisplayNames(property_name=property_name)),
        "economic_plan": build_wirtschaftsplan_html(plan, plan_lines, WirtschaftsplanDisplayNames(property_name=property_name)),
        "annual_settlement": build_weg_jahresabrechnung_html(run, annual_lines, WegDisplayNames(property_name=property_name)),
        "dunning": build_dunning_html(notice, DunningDisplayNames(person_name="Anna Müller")),
        "dunning_interest": build_dunning_html(interest_notice, DunningDisplayNames(person_name="Anna Müller")),
        "minutes": build_niederschrift_html(meeting, [], [], meeting_names),
        "register": build_register_html(entries, meeting_names),
        "invitation": build_invitation_html(meeting, [], [("Anna Müller", property_name, "12345 Musterstadt")], meeting_names, False),
    }


RECIPIENT_DOCUMENTS = (
    "asset_report", "legacy_asset_report", "economic_plan", "annual_settlement",
    "dunning", "dunning_interest", "minutes", "register", "invitation",
)


@pytest.mark.parametrize("document", RECIPIENT_DOCUMENTS[:-1])
def test_recipient_html_has_no_author_notes(document):
    html = recipient_documents()[document]
    for marker in AUTHOR_MARKERS:
        assert marker not in html, (document, marker)


@pytest.mark.parametrize("document", RECIPIENT_DOCUMENTS)
def test_rendered_recipient_pdf_keeps_explanations_without_author_notes(document):
    with pdfplumber.open(BytesIO(render_pdf(recipient_documents()[document]))) as pdf:
        text = " ".join(" ".join(page.extract_text().split()) for page in pdf.pages)
    for marker in AUTHOR_MARKERS:
        assert marker not in text, (document, marker)
    expected = {
        "asset_report": ("manuelle Angaben der Verwaltung", "kein automatischer Buchungssaldo", "18.000,00 EUR"),
        "legacy_asset_report": ("Anfangsbestand (Verwalter-Angabe)", "Entnahmen im Zeitraum (Verwalter-Angabe)"),
        "economic_plan": ("Vorschlag für die Beschlussfassung", "kein Beschluss", "500,00 EUR"),
        "annual_settlement": ("§ 28 Abs. 2 WEG", "4.000,00 EUR"),
        "dunning": ("102,50 EUR", "Verzugszinsen sind nicht enthalten."),
        "dunning_interest": ("102,68 EUR", "Verzugszinsen werden anhand des angegebenen Basiszinssatzes ausgewiesen."),
        "minutes": ("automatisch erzeugter Protokollentwurf",),
        "register": ("gemäß § 24 Abs. 7 WEG", "jedem Eigentümer auf Verlangen Einsicht zu gewähren"),
        "invitation": ("Anna Müller", "Einladung zur Eigentümerversammlung"),
    }
    for phrase in expected[document]:
        assert phrase in text, (document, phrase)


def test_legacy_asset_notes_are_presented_without_mutating_stored_lines():
    from app.models.settlement_line import SettlementLine
    from app.services.vermoegensbericht_pdf import _row

    original = "Anfangsbestand (in dieser Ausbaustufe manuell erfasst)"
    line = SettlementLine(line_type="RESERVE_OPENING", label="Anfangsbestand", amount=Decimal("20000.00"), note=original)
    assert "Anfangsbestand (Verwalter-Angabe)" in _row(line)
    assert line.note == original
    line.note = "Beleg <A> mit Vermerk zur Ausbaustufe"
    assert "Beleg &lt;A&gt; mit Vermerk zur Ausbaustufe" in _row(line)


class TestWeasyPrintRenderer:
    """Test the default WeasyPrint-based renderer."""

    def test_render_minimal_html(self):
        """Minimal HTML produces non-empty PDF with correct header."""
        html = "<html><body><h1>Test</h1></body></html>"
        renderer = WeasyPrintRenderer()
        pdf_bytes = renderer.render(html)

        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100, "PDF should be more than a few bytes"
        assert pdf_bytes[:5] == b"%PDF-", "Output must start with PDF magic bytes"

    def test_render_smoke_template(self):
        """The smoke-test template renders to a valid PDF."""
        template_path = TEMPLATES_DIR / "smoke_test.html"
        assert template_path.exists(), f"Template not found: {template_path}"

        html = template_path.read_text(encoding="utf-8")
        pdf_bytes = render_pdf(html)

        assert pdf_bytes[:5] == b"%PDF-"
        assert len(pdf_bytes) > 500, "Smoke-test PDF should be substantial"
        # Verify the PDF ends with %%EOF (standard PDF trailer).
        assert b"%%EOF" in pdf_bytes, "PDF should contain the %%EOF trailer"

    def test_render_german_characters(self):
        """German umlauts and special characters render without error."""
        html = """
        <html><body>
          <p>Betriebskostenabrechnung fuer Eigentuemer</p>
          <p>Gebuehren: 1.234,56 EUR</p>
        </body></html>
        """
        pdf_bytes = render_pdf(html)
        assert pdf_bytes[:5] == b"%PDF-"


class TestRenderIsReproducible:
    """A finalized document is re-rendered on every download; the bytes must not change."""

    def test_same_html_renders_to_identical_bytes_when_the_clock_moves(self, monkeypatch):
        """Without libharfbuzz-subset (CI, production image) WeasyPrint subsets fonts
        with fontTools, which stamps the current time into every embedded font."""
        from types import SimpleNamespace

        from fontTools.misc import timeTools

        ticks = iter(range(1_700_000_000, 1_700_001_000))
        monkeypatch.setattr(timeTools, "time", SimpleNamespace(time=lambda: float(next(ticks))))
        monkeypatch.setattr("weasyprint.pdf.fonts.harfbuzz_subset", None)
        html = "<html><body><h1>Wirtschaftsplan</h1><p>Betriebskosten 1.234,56 EUR</p></body></html>"

        assert render_pdf(html) == render_pdf(html)


class TestPdfRendererProtocol:
    """Test the swappable abstraction."""

    def test_weasyprint_satisfies_protocol(self):
        """WeasyPrintRenderer is a valid PdfRenderer."""
        renderer = WeasyPrintRenderer()
        assert isinstance(renderer, PdfRenderer)

    def test_custom_renderer_can_replace_default(self):
        """A custom renderer can be swapped in via set_renderer."""

        class StubRenderer:
            def render(self, html: str, *, base_url: str | None = None) -> bytes:
                return b"%PDF-stub"

        original = get_renderer()
        try:
            set_renderer(StubRenderer())
            result = render_pdf("<html></html>")
            assert result == b"%PDF-stub"
        finally:
            # Restore the original renderer.
            set_renderer(original)

    def test_get_renderer_returns_default(self):
        """By default, the module uses WeasyPrintRenderer."""
        renderer = get_renderer()
        assert isinstance(renderer, WeasyPrintRenderer)


@pytest.mark.parametrize('kind,expected', [
    ('wirtschaftsplan', '3ee0e1cbcf7ae1d1ac68d947ef0f9b837f2a5085cb8064b865f975c8ef58e8f5'),
    ('vermoegensbericht', '057a3c108ccaba1527a65d61887607d79275b90c3940fa5b5c3f57ec35a10715'),
])
def test_legacy_finalized_document_html_keeps_its_original_content(kind, expected):
    from datetime import date
    from hashlib import sha256
    from types import SimpleNamespace
    from app.services.wirtschaftsplan_pdf import build_wirtschaftsplan_html, WirtschaftsplanDisplayNames
    from app.services.vermoegensbericht_pdf import build_vermoegensbericht_html, VermoegensberichtDisplayNames
    # Digests captured from the original renderers, before dated addressees.
    doc = SimpleNamespace(period_start=date(2025, 1, 1), period_end=date(2025, 12, 31), plan_year=2025,
        snapshot_payload={'display_names': {'schema_version': 1, 'values': {}}},
        snapshot_sha256='legacy-digest', engine_version='legacy')
    render, names = ((build_wirtschaftsplan_html, WirtschaftsplanDisplayNames()) if kind == 'wirtschaftsplan'
        else (build_vermoegensbericht_html, VermoegensberichtDisplayNames()))
    assert sha256(render(doc, [], names).encode()).hexdigest() == expected
