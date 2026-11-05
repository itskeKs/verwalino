"""Document-rate regressions that also run without a database or app fixtures."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import lease_document_service, letter_service
from app.services.lease_document_service import CONTRACT_TEMPLATE, LeaseDocumentService
from app.services.letter_service import LetterService, render_letter, replace_tokens


# Frozen pre-#219 text: compare the full document, including whitespace and bytes.
LEGACY_CONTRACT_TEXT = """Vorlage; rechtliche Prüfung erforderlich

Vermieter:
[Freitext: Vermieter ergänzen]
[Freitext: Vermieteranschrift ergänzen]

Mieter:
Anna Beispiel
Musterweg 1
12345 Teststadt

Mietobjekt: Sonnenhof, Sonnenweg 8
12345 Teststadt
Einheit 1 · Erdgeschoss
Fläche: 100,00 m²

Mietbeginn: 01.01.2026
Mietende: Unbefristet
Monatliche Kaltmiete (Originalvertrag): 750,00 €
Monatliche Vorauszahlung (Originalvertrag): 150,00 €
Kaution: Nicht erfasst
Übergabedatum: 02.01.2026

Freitext: Weitere Vereinbarungen
Gartenmitbenutzung prüfen.

Freitext: Befristungsgrund, Nutzung und sonstige Vertragsbedingungen
[Bitte ergänzen und rechtlich prüfen lassen.]

Ort, Datum: ____________________
Unterschrift Vermieter: ____________________
Unterschrift Mieter: ____________________"""


@pytest.fixture
def document_context(monkeypatch):
    from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository

    unit = SimpleNamespace(id='unit', number='1', location='Erdgeschoss', area=Decimal('100'))
    monkeypatch.setattr(letter_service.UnitRepository, 'get_by_id', AsyncMock(return_value=unit))
    history = AsyncMock(return_value=[])
    monkeypatch.setattr(LeaseRentChangeRepository, 'list_for_lease', history)
    lease = SimpleNamespace(id='lease', unit_id=unit.id, valid_from=date(2026, 1, 1), valid_to=None,
        base_rent=Decimal('750'), advance_payment=Decimal('150'), heating_advance_payment=Decimal('0'), deposit=None)
    person = SimpleNamespace(company_name='', first_name='Anna', last_name='Beispiel',
        street='Musterweg 1', zip_code='12345', city='Teststadt')
    prop = SimpleNamespace(name='Sonnenhof', street='Sonnenweg 8', zip_code='12345', city='Teststadt')
    auth = SimpleNamespace(organization_id='org', user_id='user')
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))))
    return SimpleNamespace(lease=lease, person=person, prop=prop, unit=unit, auth=auth, db=db, history=history)


async def variables(context, day):
    return (await LetterService(context.db, context.auth).variables(context.lease, context.person,
        context.prop, day, handover_date=date(2026, 1, 2), agreements='Gartenmitbenutzung prüfen.'))[0]


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [date(2025, 12, 31), date(2026, 6, 1)])
async def test_zero_heating_keeps_the_entire_legacy_contract_and_pdf_bytes(document_context, day):
    values = await variables(document_context, day)
    rendered = replace_tokens(CONTRACT_TEMPLATE, values)
    assert rendered == LEGACY_CONTRACT_TEXT
    assert values['vertrag.vorauszahlung'] == '150,00 €'
    args = ('Anna Beispiel', 'Musterweg 1\n12345 Teststadt', 'Mietvertrag · Einheit 1')
    assert render_letter(*args, rendered, day) == render_letter(*args, LEGACY_CONTRACT_TEXT, day)


@pytest.mark.asyncio
@pytest.mark.parametrize('day,expected', [
    (date(2026, 3, 14), ('750,00 €', '150,00 €', '90,00 €', '990,00 €')),
    (date(2026, 3, 15), ('800,01 €', '160,02 €', '100,03 €', '1.060,06 €')),
    (date(2026, 6, 1), ('800,01 €', '160,02 €', '100,03 €', '1.060,06 €')),
])
async def test_letters_show_separate_advances_and_exact_total_at_the_document_date(document_context, day, expected):
    c = document_context
    c.lease.heating_advance_payment = Decimal('90')
    c.history.return_value = [SimpleNamespace(valid_from=date(2026, 3, 15), base_rent=Decimal('800.01'),
        advance_payment=Decimal('160.02'), heating_advance_payment=Decimal('100.03'))]
    values = await variables(c, day)
    net, operating, heating, total = expected
    assert values['vertrag.kaltmiete'] == net
    assert values['vertrag.vorauszahlung'] == (
        f'Betriebskosten-Vorauszahlung: {operating}\nHeizkosten-Vorauszahlung: {heating}\nMonatliche Gesamtmiete: {total}')
    summary = values['vertrag.mietaufstellung']
    for line in (f'Monatliche Kaltmiete (Stand {day:%d.%m.%Y}): {net}',
                 f'Betriebskosten-Vorauszahlung: {operating}', f'Heizkosten-Vorauszahlung: {heating}',
                 f'Monatliche Gesamtmiete: {total}'):
        assert line in summary
    assert 'Originalvertrag' not in summary
    c.history.assert_awaited_once_with(c.lease.id)


@pytest.mark.asyncio
async def test_changed_zero_heating_rate_is_not_labelled_as_the_original_contract(document_context):
    c = document_context
    c.history.return_value = [SimpleNamespace(valid_from=date(2026, 3, 15), base_rent=Decimal('800'),
        advance_payment=Decimal('160'), heating_advance_payment=Decimal('0'))]
    values = await variables(c, date(2026, 3, 15))
    rendered = replace_tokens(CONTRACT_TEMPLATE, values)
    assert 'Monatliche Kaltmiete (Stand 15.03.2026): 800,00 €' in rendered
    assert 'Monatliche Vorauszahlung (Stand 15.03.2026): 160,00 €' in rendered
    assert 'Originalvertrag' not in rendered
    assert 'Heizkosten' not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize('day', [date(2026, 2, 28), date(2026, 4, 1)])
async def test_end_of_tenancy_rent_includes_last_day_but_excludes_first_day_after_end(document_context, day):
    c = document_context
    c.lease.valid_to = date(2026, 3, 1)
    c.history.return_value = [
        SimpleNamespace(valid_from=date(2026, 2, 28), base_rent=Decimal('700'),
                        advance_payment=Decimal('150'), heating_advance_payment=Decimal('0')),
        SimpleNamespace(valid_from=date(2026, 3, 1), base_rent=Decimal('750'),
                        advance_payment=Decimal('150'), heating_advance_payment=Decimal('90')),
    ]
    values = await variables(c, day)
    assert values['datum'] == day.strftime('%d.%m.%Y')
    assert values['vertrag.kaltmiete'] == '700,00 €'
    assert values['vertrag.vorauszahlung'] == '150,00 €'
    assert values['vertrag.mietaufstellung'] == (
        'Monatliche Kaltmiete (Stand 28.02.2026): 700,00 €\n'
        'Monatliche Vorauszahlung (Stand 28.02.2026): 150,00 €\n'
        'Monatliche Gesamtmiete: 850,00 €'
    )


@pytest.mark.asyncio
async def test_lease_contract_uses_the_same_dated_rent_summary(document_context, monkeypatch):
    c = document_context
    c.lease.heating_advance_payment = Decimal('90')
    service = LeaseDocumentService(c.db, c.auth)
    monkeypatch.setattr(service, 'context', AsyncMock(return_value=(c.lease, c.unit, c.person, c.prop)))
    monkeypatch.setattr(service, 'replay', AsyncMock(return_value=None))
    save = AsyncMock(return_value={'id': 'saved'})
    monkeypatch.setattr(service.letters, 'save_original', save)
    renderer = Mock(return_value=b'pdf')
    monkeypatch.setattr(lease_document_service, 'render_letter', renderer)
    body = SimpleNamespace(as_of=date(2026, 6, 1), handover_date=date(2026, 1, 2),
        agreements='Gartenmitbenutzung prüfen.', request_id='request', model_dump=lambda **kwargs: {})
    assert await service.contract(c.lease.id, body) == {'id': 'saved'}
    rendered = renderer.call_args.args[3]
    for line in ('Betriebskosten-Vorauszahlung: 150,00 €', 'Heizkosten-Vorauszahlung: 90,00 €',
                 'Monatliche Gesamtmiete: 990,00 €'):
        assert line in rendered
    assert 'Originalvertrag' not in rendered
    assert save.await_args.kwargs['pdf'] == b'pdf'
