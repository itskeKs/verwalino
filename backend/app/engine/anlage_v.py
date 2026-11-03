"""Pure, conservative Anlage V mapping of the existing property year overview.

No booking, payment-date inference, owner allocation or tax deduction is performed.
Known exclusions stay in the unassigned list so both report totals reconcile.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Iterable, Literal
from uuid import UUID

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.services.report_service import PropertyYearReport

Section = Literal['Einnahmen', 'Werbungskosten']
Money = Annotated[str, Field(pattern=r"^-?(?:0|[1-9][0-9]*)\.[0-9]{2}$")]


@dataclass(frozen=True)
class AnlageVLine:
    key: str
    label: str
    section: Section


ANLAGE_V_LINES = (
    AnlageVLine('rent', 'Mieteinnahmen für Wohnungen (ohne Umlagen)', 'Einnahmen'),
    AnlageVLine('levies', 'Einnahmen aus umgelegten Neben-/Betriebskosten (Umlagen)', 'Einnahmen'),
    AnlageVLine('other_rent', 'Einnahmen aus Vermietung von Garagen, Werbeflächen und anderen Räumen', 'Einnahmen'),
    AnlageVLine('prior_rent', 'Vereinnahmte Mieten für frühere Jahre / Vorauszahlungen', 'Einnahmen'),
    AnlageVLine('vat', 'Vereinnahmte Umsatzsteuer', 'Einnahmen'),
    AnlageVLine('depreciation', 'Absetzung für Abnutzung (AfA)', 'Werbungskosten'),
    AnlageVLine('interest', 'Schuldzinsen', 'Werbungskosten'),
    AnlageVLine('financing', 'Geldbeschaffungskosten', 'Werbungskosten'),
    AnlageVLine('maintenance', 'Erhaltungsaufwendungen', 'Werbungskosten'),
    AnlageVLine('property_tax', 'Grundsteuer', 'Werbungskosten'),
    AnlageVLine('waste', 'Straßenreinigung / Müllabfuhr', 'Werbungskosten'),
    AnlageVLine('water', 'Wasserversorgung / Entwässerung', 'Werbungskosten'),
    AnlageVLine('lighting', 'Hausbeleuchtung', 'Werbungskosten'),
    AnlageVLine('heating', 'Heizung / Warmwasser', 'Werbungskosten'),
    AnlageVLine('chimney', 'Schornsteinreinigung', 'Werbungskosten'),
    AnlageVLine('insurance', 'Hausversicherungen', 'Werbungskosten'),
    AnlageVLine('caretaker', 'Hauswart', 'Werbungskosten'),
    AnlageVLine('cleaning', 'Treppenreinigung', 'Werbungskosten'),
    AnlageVLine('lift', 'Fahrstuhl', 'Werbungskosten'),
    AnlageVLine('administration', 'Verwaltungskosten', 'Werbungskosten'),
    AnlageVLine('other', 'Sonstige Kosten', 'Werbungskosten'),
)


@dataclass(frozen=True)
class CategoryMapping:
    code: str
    line_key: str | None
    note: str


def build_category_mapping(rows: Iterable[CategoryMapping]) -> MappingProxyType[str, CategoryMapping]:
    """Validate before making the import-time table immutable; never lose duplicates."""
    keys = {line.key for line in ANLAGE_V_LINES}
    result = {}
    for row in rows:
        if row.code in result:
            raise ValueError(f'Duplicate Anlage V category: {row.code}')
        if row.line_key is not None and row.line_key not in keys:
            raise ValueError(f'Unknown Anlage V line: {row.line_key}')
        if not row.note.startswith('[§/fachlich] '):
            raise ValueError(f'Anlage V mapping needs a judgement note: {row.code}')
        result[row.code] = row
    return MappingProxyType(result)


DEFAULT_CATEGORY_MAPPING = build_category_mapping([
    CategoryMapping('KALTMIETE', 'rent', '[§/fachlich] Wohnraummiete als Vorschlag; andere Nutzungen, Angehörige und Vorjahresmieten gesondert prüfen.'),
    CategoryMapping('NK_VORAUSZAHLUNG', 'levies', '[§/fachlich] Laufende Umlagen; Abrechnungserstattungen und Nachzahlungen gesondert prüfen.'),
    CategoryMapping('HK_VORAUSZAHLUNG', 'levies', '[§/fachlich] Laufende Umlagen; Abrechnungserstattungen und Nachzahlungen gesondert prüfen.'),
    CategoryMapping('KAUTION', None, '[§/fachlich] Ausgeschlossen: verwahrte Kaution, keine Miete; eine spätere Verrechnung ist gesondert zu prüfen.'),
    CategoryMapping('HAUSGELD_VORSCHUSS', None, '[§/fachlich] Ausgeschlossen: WEG-Eigentümervorschuss, keine Vermietungseinnahme.'),
    CategoryMapping('RUECKLAGENANTEIL', None, '[§/fachlich] Ausgeschlossen: Rücklagenzuführung, keine Vermietungseinnahme.'),
    CategoryMapping('SONDERZAHLUNG', None, '[§/fachlich] Nicht als Miete angesetzt: Eigentümer-Sonderzahlung; Verwendungszweck gesondert prüfen.'),
    CategoryMapping('GRUNDSTEUER', 'property_tax', '[§/fachlich] Nur vermietungsbezogene Grundsteuer; andere öffentliche Lasten prüfen.'),
    CategoryMapping('KALTWASSER', 'water', '[§/fachlich] Vermietungsbezogene Wasserkosten; Abfluss und Umlage prüfen.'),
    CategoryMapping('ENTWAESSERUNG', 'water', '[§/fachlich] Vermietungsbezogene Abwasserkosten; Abfluss und Umlage prüfen.'),
    CategoryMapping('HEIZUNG_ZENTRAL', 'heating', '[§/fachlich] Laufender Heizungsbetrieb; Investitionen und doppelte Erfassung ausschließen.'),
    CategoryMapping('WARMWASSER_ZENTRAL', 'heating', '[§/fachlich] Laufende Warmwasserkosten; doppelte Erfassung mit Heizung ausschließen.'),
    CategoryMapping('MUELLBESEITIGUNG', 'waste', '[§/fachlich] Laufende Müllabfuhr; Abfluss und Umlage prüfen.'),
    CategoryMapping('STRASSENREINIGUNG', 'waste', '[§/fachlich] Laufende Straßenreinigung; Abfluss und Umlage prüfen.'),
    CategoryMapping('WINTERDIENST', 'waste', '[§/fachlich] Winterdienst der Straßenreinigung zugeordnet; Leistungsumfang prüfen.'),
    CategoryMapping('GEBAEUDEREINIGUNG', 'cleaning', '[§/fachlich] Treppenreinigung als Vorschlag; sonstige Gebäudereinigung gesondert prüfen.'),
    CategoryMapping('GARTENPFLEGE', 'other', '[§/fachlich] Laufende Gartenpflege als sonstige Kosten; Neuanlage nicht automatisch abziehbar.'),
    CategoryMapping('ALLGEMEINSTROM', 'lighting', '[§/fachlich] Hausbeleuchtung als Vorschlag; andere Stromnutzung gesondert prüfen.'),
    CategoryMapping('SCHORNSTEINREINIGUNG', 'chimney', '[§/fachlich] Laufende Schornsteinreinigung; Abfluss und Umlage prüfen.'),
    CategoryMapping('GEBAEUDEVERSICHERUNG', 'insurance', '[§/fachlich] Gebäudeversicherung mit Vermietungsbezug; private Anteile prüfen.'),
    CategoryMapping('HAFTPFLICHTVERSICHERUNG', 'insurance', '[§/fachlich] Nur Haus- und Grundbesitzerhaftpflicht mit Vermietungsbezug.'),
    CategoryMapping('AUFZUG', 'lift', '[§/fachlich] Betrieb und Wartung des Fahrstuhls; Ersatzinvestitionen gesondert prüfen.'),
    CategoryMapping('WASCHRAUM', 'other', '[§/fachlich] Laufender Betrieb als sonstige Kosten; Anschaffungen gesondert prüfen.'),
    CategoryMapping('HAUSMEISTER', 'caretaker', '[§/fachlich] Hauswart als Vorschlag; Verwaltungs- und Reparaturanteile gesondert prüfen.'),
    CategoryMapping('SONSTIGE_BK', 'other', '[§/fachlich] Sonstige laufende Kosten; Beleg, Vermietungsbezug und Umlage prüfen.'),
    CategoryMapping('KABEL_BREITBAND', 'other', '[§/fachlich] Vermietungsbezogene Kosten; Vertragsgrundlage und Umlage gesondert prüfen.'),
    CategoryMapping('MESSDIENST', None, '[§/fachlich] Keine eindeutige Zuordnung: Wasser, Heizung oder Abrechnung; Doppelansatz vermeiden.'),
    CategoryMapping('CO2_KOSTEN', 'heating', '[§/fachlich] Heizungsbezogene CO₂-Kosten; Aufteilung und Doppelansatz mit Brennstoffkosten prüfen.'),
    CategoryMapping('WEG_VERWALTERKOSTEN', 'administration', '[§/fachlich] Verwaltung statt sonstige Kosten als Detailvorschlag; nur vermietungsbezogener Eigentümeranteil.'),
    CategoryMapping('BUCHHALTUNG', 'administration', '[§/fachlich] Vermietungsbezogene Buchhaltung als Verwaltung; private Steuerberatung nicht eingeschlossen.'),
    CategoryMapping('EIGENTUEMERVERSAMMLUNG', 'administration', '[§/fachlich] Verwaltungsbezug als Vorschlag; Vermietungsbezug und Eigentümeranteil prüfen.'),
    CategoryMapping('BANKGEBUEHREN', 'administration', '[§/fachlich] Laufende Kontoführung als Verwaltung; keine Geldbeschaffungskosten ohne Finanzierungsnachweis.'),
    CategoryMapping('RECHTSBERATUNG', None, '[§/fachlich] Keine eindeutige Zuordnung: Mietstreit, Erwerb oder privater Anlass; Beleg prüfen.'),
    CategoryMapping('INSTANDHALTUNG', 'maintenance', '[§/fachlich] Erhaltungsaufwand als Vorschlag; Herstellungskosten, 15-%-Grenze und Verteilung prüfen.'),
    CategoryMapping('INSTANDSETZUNG', 'maintenance', '[§/fachlich] Reparatur als Vorschlag; Herstellungskosten, 15-%-Grenze und Verteilung prüfen.'),
    CategoryMapping('ERHALTUNGSRUECKLAGE', None, '[§/fachlich] Ausgeschlossen: Rücklagenzuführung ist kein abziehbarer Erhaltungsaufwand; tatsächliche Verwendung zählt.'),
    CategoryMapping('SONDERUMLAGE', None, '[§/fachlich] Keine eindeutige Zuordnung: Mittelzuführung ohne Nachweis der tatsächlichen Verwendung.'),
])


class AnlageVAmount(BaseModel):
    key: str
    label: str
    section: Section
    amount: Money
    note: str


class UnmappedAmount(BaseModel):
    code: str
    label: str
    section: Section
    amount: Money
    note: str


class AnlageVReconciliation(BaseModel):
    section: Section
    mapped_total: Money
    unmapped_total: Money
    report_total: Money
    difference: Money


class AnlageVSummary(BaseModel):
    property_id: UUID
    property_name: str
    year: int
    accounting_scope_id: UUID | None
    lines: list[AnlageVAmount]
    unmapped: list[UnmappedAmount]
    reconciliation: list[AnlageVReconciliation]
    footnote: str = (
        'Zuordnungshilfe, keine Steuerberatung. Zeilennummern je Formularjahr prüfen. '
        'Detailzeilen sind keine eigenen amtlichen Formularfelder: umgelegte, nicht umgelegte '
        'und sonstige Kosten gesondert prüfen. Einnahmen nach Wertstellung, Bruttokosten nach '
        'Rechnungsdatum; steuerlichen Zu-/Abfluss, Eigentümeranteil und Abziehbarkeit prüfen. '
        'Null bedeutet keinen zugeordneten Berichtsbetrag, nicht nachgewiesene steuerliche Vollständigkeit.'
    )


def map_property_year(report: PropertyYearReport) -> AnlageVSummary:
    """Classify exactly the report inputs; unassigned includes explicit exclusions.

    Existing filters exclude drafts, reversals, cancelled payments, raw bank
    transactions and superseded/unconfirmed costs. No additional data is queried.
    Tenant-recoverable expenses and advances are retained, not netted away.
    """
    sources = [
        ('Einnahmen', row.payment_type, row.payment_type, row.amount) for row in report.income
    ] + [
        ('Werbungskosten', row.category_code, row.category_name, row.amount) for row in report.expenses
    ]
    values = [Decimal(row[3]) for row in sources] + [Decimal(report.income_total), Decimal(report.expense_total)]
    with localcontext() as context:
        context.prec = max(28, max((len(value.as_tuple().digits) for value in values), default=1)
                           + len(str(len(values) + 1)) + 2)
        totals = {line.key: Decimal('0.00') for line in ANLAGE_V_LINES}
        notes: dict[str, list[str]] = {line.key: [] for line in ANLAGE_V_LINES}
        sections = {line.key: line.section for line in ANLAGE_V_LINES}
        unmapped = []
        for section, code, label, amount in sources:
            mapping = DEFAULT_CATEGORY_MAPPING.get(code)
            key = mapping.line_key if mapping else None
            if key is None or sections[key] != section:
                unmapped.append(UnmappedAmount(code=code, label=label, section=section, amount=amount,
                    note=mapping.note if mapping and key is None else
                    '[§/fachlich] Keine eindeutige Zuordnung; Herkunft und Beleg gesondert prüfen.'))
            else:
                totals[key] += Decimal(amount)
                if mapping.note not in notes[key]:
                    notes[key].append(mapping.note)
        reconciliation = []
        for section, report_total in [('Einnahmen', report.income_total), ('Werbungskosten', report.expense_total)]:
            mapped = sum((totals[key] for key in totals if sections[key] == section), Decimal('0.00'))
            unassigned = sum((Decimal(row.amount) for row in unmapped if row.section == section), Decimal('0.00'))
            difference = Decimal(report_total) - mapped - unassigned
            if difference:
                raise ValueError(f'Anlage V amounts do not reconcile: {section}')
            reconciliation.append(AnlageVReconciliation(section=section,
                mapped_total=f'{mapped:.2f}', unmapped_total=f'{unassigned:.2f}',
                report_total=report_total, difference=f'{difference:.2f}'))
        return AnlageVSummary(property_id=report.property_id, property_name=report.property_name,
            year=report.year, accounting_scope_id=report.accounting_scope_id,
            lines=[AnlageVAmount(key=line.key, label=line.label, section=line.section,
                amount=f'{totals[line.key]:.2f}', note=' '.join(notes[line.key]) or
                '[§/fachlich] Kein zugeordneter Berichtsbetrag; fehlende Steuerdaten gesondert prüfen.')
                for line in ANLAGE_V_LINES],
            unmapped=unmapped, reconciliation=reconciliation)
