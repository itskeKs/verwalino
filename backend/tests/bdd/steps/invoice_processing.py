"""An invoice through the worker with in-process fake providers, observed through the HTTP API."""
import io
from contextlib import closing
from decimal import Decimal

import psycopg2
import pytest
from PIL import Image
from pytest_bdd import given, parsers, then, when

from app.core.db import get_worker_session_factory
from app.core.storage import LocalFsDocumentStore, set_store
from app.declarations.fake_provider import FakeDocumentReadingProvider, FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.invoice_pipeline import InvoiceProviders
from app.declarations.provider import ProviderFailure, ProviderUsage
from app.workers.invoice_extraction import process_once
from tests.ai_settings_support import configure_ai
from tests.conftest import MIGRATOR_SYNC_URL
from tests.invoice_model_support import no_invoice_model_data as no_invoice_model_data

READING_USAGE = ProviderUsage(1000, 200, Decimal('0.00165'), 1)
EXTRACTION_USAGE = ProviderUsage(2000, 100, Decimal('0.00085'), 1)


@pytest.fixture
def invoice_workspace(request, tmp_path, no_invoice_model_data):
    """A private document store and no invoice metering data left behind (the migration-chain tests meet none).

    A browser scenario shares the store of the stack it uploads to: the API process writes the originals there.
    The worker claims the oldest waiting attempt of the organization, so the scenario starts with an empty queue:
    an invoice that an earlier scenario of the session uploaded and nobody processed is not this scenario's.
    """
    with closing(psycopg2.connect(MIGRATOR_SYNC_URL)) as connection, connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM public.invoice_processing_attempt WHERE status IN ('queued', 'running')")
    root = request.getfixturevalue('browser_storage') if request.node.get_closest_marker('browser') else str(tmp_path)
    set_store(LocalFsDocumentStore(root))
    yield
    set_store(None)


def _scan() -> bytes:
    """The scanned invoice: one A4 image, no text layer."""
    out = io.BytesIO()
    Image.new('L', (595, 842), 255).save(out, 'PDF', resolution=72)
    return out.getvalue()


def _organization(world) -> str:
    return world.act('GET', '/api/organizations/me').json()['id']


def _settings(world, **fields):
    org = _organization(world)
    response = world.act('PUT', f'/api/organizations/{org}/ai-settings', json={
        'api_key': 'synthetic-invoice-key', 'model': 'gpt-5.6-luna', 'document_model': 'gpt-5.4-mini', 'import_enabled': True,
        'confirmation': True, **fields})
    assert response.status_code == 200, world.describe_response()


@given('the organization has enabled the AI import with its own key and both models')
def ai_import_enabled(world, invoice_workspace):
    _settings(world)


@given('the organization has enabled the AI import with a monthly limit of one cent')
def ai_import_with_limit(world, invoice_workspace):
    _settings(world, monthly_limit_usd='0.01')


@given('the organization enabled the AI import and confirmed the provider before invoices were covered')
def ai_import_confirmed_earlier(world, invoice_workspace):
    # What an upgraded installation holds: a key confirmed with the first text, which did not name invoices.
    configure_ai(_organization(world), text_version='1', import_enabled=True)
    assert world.act('GET', f'/api/organizations/{_organization(world)}/ai-settings').json()['invoice_confirmation_required'] is True


def _process(world, *, reader=None, extractor=None, count=1):
    reader = reader or FakeDocumentReadingProvider(usage=READING_USAGE)
    extractor = extractor or FakeInvoiceExtractionProvider(usage=EXTRACTION_USAGE)
    world.data['built'] = []

    def providers(effective):
        world.data['built'].append(effective)
        return InvoiceProviders(reader=reader if effective.document_model else None, extractor=extractor)

    world.data['reader'], world.data['extractor'] = reader, extractor
    for _ in range(count):
        assert world.run(process_once(get_worker_session_factory(), _organization(world), providers=providers))


@when('the administrator uploads a scanned building-insurance invoice and the worker processes it')
def upload_and_process(world):
    response = world.act('POST', '/api/invoice-documents/upload', files={'file': ('versicherung.pdf', _scan(), 'application/pdf')})
    assert response.status_code == 201, world.describe_response()
    world.data['invoice'] = response.json()['id']
    _process(world)


@when('the administrator uploads a scanned building-insurance invoice whose extraction fails at the provider and the worker processes it')
def upload_and_fail(world):
    response = world.act('POST', '/api/invoice-documents/upload', files={'file': ('versicherung.pdf', _scan(), 'application/pdf')})
    assert response.status_code == 201, world.describe_response()
    world.data['invoice'] = response.json()['id']
    failure = ProviderFailure('PROVIDER_TIMEOUT', False, 'timed out', usage=ProviderUsage(300, 0, Decimal('0.000225'), 1))
    _process(world, reader=FakeDocumentReadingProvider(usage=READING_USAGE), extractor=FakeInvoiceExtractionProvider(failure=failure))


@when('the administrator uploads a scanned building-insurance invoice whose IBAN the extraction model misread and the worker processes it')
def upload_with_misread_iban(world):
    response = world.act('POST', '/api/invoice-documents/upload', files={'file': ('versicherung.pdf', _scan(), 'application/pdf')})
    assert response.status_code == 201, world.describe_response()
    world.data['invoice'] = response.json()['id']
    # One digit off in the check digits: what a model that misreads a character answers.
    misread = synthetic_invoice_output(vendor_iban=('DE89 3704 0044 0532 0130 01', 0.9))
    _process(world, extractor=FakeInvoiceExtractionProvider(output=misread, usage=EXTRACTION_USAGE))


def _invoice(world):
    return world.look(world.actor, f"/api/invoice-documents/{world.data['invoice']}").json()


@then(parsers.parse('the invoice proposes 312,00 EUR dated 15 January 2026 from "{vendor}"'))
def proposal(world, vendor):
    invoice = _invoice(world)
    assert invoice['processing_status'] == 'succeeded', invoice
    fields = invoice['extraction_payload']
    assert (fields['gross_amount'], fields['invoice_date'], fields['vendor_name']) == ('312.00', '2026-01-15', vendor)


@then(parsers.parse('the invoice proposes the vendor\'s IBAN "{iban}" for review'))
def proposes_the_iban(world, iban):
    fields = _invoice(world)['extraction_payload']
    assert fields['vendor_iban'] == iban.replace(' ', '') and fields['field_confidences']['vendor_iban'] > 0


@then('the invoice proposes no vendor IBAN')
def proposes_no_iban(world):
    fields = _invoice(world)['extraction_payload']
    assert fields['vendor_iban'] is None and fields['field_confidences']['vendor_iban'] == 0


@then('nothing is posted')
def nothing_posted(world):
    assert _invoice(world)['cost_entry_id'] is None


@then('nothing was sent to a provider')
def nothing_sent(world):
    assert world.data['reader'].reads == [] and world.data['extractor'].calls == []


def _proposes_nothing(world, status, code):
    invoice = _invoice(world)
    assert (invoice['processing_status'], invoice['processing_error_code']) == (status, code), invoice
    fields = invoice['extraction_payload'] or {}
    assert 'attempt_id' not in fields and not fields.get('gross_amount')


@then('the invoice says that the organization has to confirm again and proposes nothing')
def confirm_again(world):
    _proposes_nothing(world, 'unavailable', 'ai_invoice_confirmation_missing')


@then('the invoice says that the processing failed and proposes nothing')
def processing_failed(world):
    _proposes_nothing(world, 'failed', 'model_extraction_failed')


@then('the invoice says that the monthly budget is exhausted and proposes nothing')
def budget_exhausted(world):
    _proposes_nothing(world, 'unavailable', 'organization_ai_budget_exceeded')


@then('the original can still be downloaded')
def original_downloadable(world):
    response = world.look(world.actor, f"/api/invoice-documents/{world.data['invoice']}/content")
    assert response.status_code == 200 and response.content.startswith(b'%PDF')


@when('the administrator confirms the provider again and starts the processing once more')
def confirm_and_retry(world):
    org = _organization(world)
    # The settings are saved as the settings page saves them: every choice again, with the tick, without the key.
    current = world.act('GET', f'/api/organizations/{org}/ai-settings').json()
    saved = world.act('PUT', f'/api/organizations/{org}/ai-settings', json={
        'model': current['model'], 'document_model': current['document_model'], 'import_enabled': current['import_enabled'], 'confirmation': True})
    assert saved.status_code == 200 and saved.json()['invoice_confirmation_required'] is False, world.describe_response()
    assert world.act('POST', f"/api/invoice-documents/{world.data['invoice']}/retry").status_code == 200, world.describe_response()
    _process(world)


def _usage(world):
    response = world.look(world.actor, f"/api/invoice-documents/{world.data['invoice']}/model-usage")
    assert response.status_code == 200, response.text
    return response.json()


@then('the invoice shows the usage of the reading and of the extraction model')
def both_stages(world):
    usage = _usage(world)
    assert [(s['stage'], s['model'], s['usage_status']) for s in usage['stages']] == [
        ('READING', 'gpt-5.4-mini', 'REPORTED'), ('EXTRACTION', 'gpt-5.6-luna', 'REPORTED')]
    assert Decimal(usage['total']['estimated_cost_usd']) == Decimal('0.0025')


@then('the invoice shows the usage of the reading that was already paid')
def reading_paid(world):
    usage = _usage(world)
    stages = {s['stage']: s for s in usage['stages']}
    assert Decimal(stages['READING']['estimated_cost_usd']) == Decimal('0.00165')
    assert Decimal(usage['total']['estimated_cost_usd']) == Decimal('0.00165') + Decimal('0.000225')


@then('the invoice shows no usage')
def no_usage(world):
    assert _usage(world) == {'stages': [], 'total': None}


@when('Verwaltung Beta asks for the usage of that invoice')
def beta_asks(world):
    world.send(world.administrator_of('Verwaltung Beta'), 'GET', f"/api/invoice-documents/{world.data['invoice']}/model-usage")


@then('the usage is not found')
def usage_not_found(world):
    assert world.response.status_code == 404, world.describe_response()
