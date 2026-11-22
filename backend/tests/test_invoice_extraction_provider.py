"""Invoice extraction stage: the text of one invoice in, its typed fields out, per transport (issue #500, ADR 0017)."""
import json
from decimal import Decimal

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from app.declarations.fake_provider import FakeInvoiceExtractionProvider, synthetic_invoice_output
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.prompts import INVOICE_EXTRACTION_PROMPT, INVOICE_PROMPT_VERSION
from app.declarations.provider import (
    INVOICE_PASS_ID,
    DocumentInput,
    InvoiceCategory,
    PageInput,
    ProviderFailure,
    PROVIDER_INCOMPLETE,
    PROVIDER_SCHEMA_FAILURE,
)
from app.declarations.provider_runtime import ProviderClient, create_extraction_provider
from app.declarations.schemas import INVOICE_SCHEMA_VERSION, InvoiceExtractionOutput
from app.services.ai_settings_service import EffectiveAiSettings

pytestmark = pytest.mark.no_db

READING_MODEL = 'gpt-5.4-mini'
EXTRACTION_MODEL = 'gpt-5.6-luna'
CATEGORIES = (InvoiceCategory('GEBAEUDEVERSICHERUNG', 'Gebäudeversicherung'), InvoiceCategory('GRUNDSTEUER', 'Grundsteuer'))
DOCUMENT = DocumentInput('invoice', (
    PageInput('invoice', 1, 'Mustermann Versicherungen AG\nRechnung RE-2026-0042\nGesamtbetrag 312,00 EUR', 1.0),
    PageInput('invoice', 2, 'Zahlbar bis 30.01.2026', 1.0),
))
ANSWER = json.loads(synthetic_invoice_output().model_dump_json())


class _Encoder:
    @staticmethod
    def encode(value):
        return list(value.encode('utf-8'))


# ---- the typed result ---------------------------------------------------------------------------------------

def test_the_result_has_the_ten_fields_each_with_a_value_and_a_certainty():
    parsed = InvoiceExtractionOutput.model_validate(ANSWER)
    assert set(InvoiceExtractionOutput.model_fields) == {
        'vendor_name', 'invoice_date', 'document_number', 'gross_amount', 'vat_amount', 'vat_rate',
        'service_period_from', 'service_period_to', 'suggested_category_code', 'vendor_iban'}
    assert parsed.gross_amount.value == '312.00' and parsed.vat_amount.value is None
    assert parsed.vendor_iban.value == 'DE89370400440532013000' and parsed.vendor_iban.confidence == 0.9
    assert not {'page', 'pdf_page', 'document_key'} & set(InvoiceExtractionOutput.model_json_schema()['properties']), 'the caller owns the identity'


@pytest.mark.parametrize('field,value', [
    ('gross_amount', '312,00'), ('gross_amount', '1.234,56'), ('gross_amount', '312.0000001'), ('gross_amount', '+312'),
    ('gross_amount', 'EUR 312'), ('vat_rate', '19 %'), ('invoice_date', '15.01.2026'), ('invoice_date', '2026-02-30'),
    ('service_period_to', '2026-13-01'), ('vendor_name', ''), ('vendor_name', 'a\x00b'),
], ids=lambda value: str(value)[:12])
def test_a_value_the_review_boundary_would_refuse_is_refused_when_the_answer_is_typed(field, value):
    answer = {**ANSWER, field: {'value': value, 'confidence': 0.5}}
    with pytest.raises(ValidationError):
        InvoiceExtractionOutput.model_validate(answer)


@pytest.mark.parametrize('stated,kept', [
    ('DE89370400440532013000', 'DE89370400440532013000'),
    ('de89 3704 0044 0532 0130 00', 'DE89370400440532013000'),
    ('  DE89 3704 0044 0532 0130 00  ', 'DE89370400440532013000'),
    ('AT61 1904 3002 3457 3201', 'AT611904300234573201'),
    ('DE89370400440532013001', None),
    ('DE8937040044053201300', None),
    ('DE893704004405320130000', None),
    ('DE89 3704 0044 0532 0130 0X', None),
    ('XX89370400440532013000', None),
    ('Konto bei der Commerzbank', None),
    ('', None),
    (None, None),
], ids=['plain', 'printed-lower-case', 'padded', 'austrian', 'check-digits', 'too-short', 'too-long', 'letter-in-german-bban',
        'unknown-country', 'text', 'empty', 'not-stated'])
def test_a_vendor_iban_is_kept_normalised_or_not_stated_and_never_fails_the_proposal(stated, kept):
    answer = {**ANSWER, 'vendor_iban': {'value': stated, 'confidence': 0.7}}
    parsed = InvoiceExtractionOutput.model_validate(answer)
    assert parsed.vendor_iban.value == kept
    assert parsed.gross_amount.value == '312.00' and parsed.vendor_name.value == 'Mustermann Versicherungen AG'


@pytest.mark.parametrize('answer', [
    {**ANSWER, 'confidence': 0.9},
    {key: value for key, value in ANSWER.items() if key != 'gross_amount'},
    {key: value for key, value in ANSWER.items() if key != 'vendor_iban'},
    {**ANSWER, 'gross_amount': {'value': '1', 'confidence': 1.5}},
    {**ANSWER, 'vendor_iban': {'value': 'DE89370400440532013000', 'confidence': 1.5}},
    {**ANSWER, 'gross_amount': {'value': '1'}},
    {**ANSWER, 'vendor_iban': {'value': 'DE89370400440532013000'}},
], ids=['extra-field', 'missing-field', 'missing-iban-field', 'confidence-over-one', 'iban-confidence-over-one', 'missing-confidence',
        'iban-missing-confidence'])
def test_an_answer_that_is_not_the_schema_is_refused(answer):
    with pytest.raises(ValidationError):
        InvoiceExtractionOutput.model_validate(answer)


def test_a_credit_note_has_a_negative_gross_amount():
    answer = {**ANSWER, 'gross_amount': {'value': '-249.90', 'confidence': 0.9}}
    assert InvoiceExtractionOutput.model_validate(answer).gross_amount.value == '-249.90'


def test_the_prompt_names_every_field_the_boundary_and_the_category_list():
    for name in InvoiceExtractionOutput.model_fields:
        assert name in INVOICE_EXTRACTION_PROMPT, name
    assert 'untrusted' in INVOICE_EXTRACTION_PROMPT and 'allowed_category_codes' in INVOICE_EXTRACTION_PROMPT
    assert 'null' in INVOICE_EXTRACTION_PROMPT and 'Never calculate' in INVOICE_EXTRACTION_PROMPT
    assert (INVOICE_PROMPT_VERSION, INVOICE_SCHEMA_VERSION, INVOICE_PASS_ID) == ('invoice-v2', 'invoice-2', 'extract_invoice')
    assert 'source_page' in INVOICE_EXTRACTION_PROMPT and 'source_quote' in INVOICE_EXTRACTION_PROMPT and 'character for character' in INVOICE_EXTRACTION_PROMPT


def test_every_field_says_where_it_was_found_and_an_answer_without_a_source_states_none():
    from openai.lib._pydantic import to_strict_json_schema
    wire = to_strict_json_schema(InvoiceExtractionOutput)
    for kind in ('InvoiceTextField', 'InvoiceDateField', 'InvoiceAmountField', 'InvoiceIbanField'):
        assert wire['$defs'][kind]['required'] == ['value', 'confidence', 'source_page', 'source_quote'], kind
        assert set(wire['$defs'][kind]['properties']) == {'value', 'confidence', 'source_page', 'source_quote'}
    stated = {**ANSWER, 'gross_amount': {'value': '312.00', 'confidence': 0.9, 'source_page': 2, 'source_quote': 'Gesamtbetrag 312,00 EUR'}}
    parsed = InvoiceExtractionOutput.model_validate(stated)
    assert (parsed.gross_amount.source_page, parsed.gross_amount.source_quote) == (2, 'Gesamtbetrag 312,00 EUR')
    bare = InvoiceExtractionOutput.model_validate({**ANSWER, 'gross_amount': {'value': '312.00', 'confidence': 0.9}})
    assert (bare.gross_amount.source_page, bare.gross_amount.source_quote) == (None, None), 'a missing source is no failure'


BAD_SOURCES = {'empty-quote': {'source_page': 1, 'source_quote': ''}, 'quote-301-characters': {'source_page': 1, 'source_quote': 'x' * 301},
               'page-0': {'source_page': 0, 'source_quote': 'Gesamtbetrag 312,00 EUR'}, 'page-201': {'source_page': 201, 'source_quote': 'Gesamtbetrag 312,00 EUR'},
               'quote-of-the-wrong-type': {'source_page': 1, 'source_quote': 5}, 'page-of-the-wrong-type': {'source_page': 'one', 'source_quote': 'Gesamtbetrag 312,00 EUR'},
               'page-without-quote': {'source_page': 1, 'source_quote': None}}


@pytest.mark.parametrize('source', list(BAD_SOURCES.values()), ids=list(BAD_SOURCES))
def test_a_source_the_schema_cannot_use_states_no_source_and_never_costs_the_answer(source):
    answer = {**ANSWER, 'gross_amount': {'value': '312.00', 'confidence': 0.9, **source}}
    for parsed in (InvoiceExtractionOutput.model_validate(answer), InvoiceExtractionOutput.model_validate_json(json.dumps(answer))):
        assert (parsed.gross_amount.value, parsed.gross_amount.confidence) == ('312.00', 0.9)
        assert (parsed.gross_amount.source_page, parsed.gross_amount.source_quote) == (None, None)
        assert parsed.vendor_name == InvoiceExtractionOutput.model_validate(ANSWER).vendor_name


def test_a_source_at_the_limits_is_kept_and_a_bad_value_is_still_refused_beside_a_bad_source():
    edge = {'source_page': 200, 'source_quote': 'x' * 300}
    kept = InvoiceExtractionOutput.model_validate({**ANSWER, 'gross_amount': {'value': '312.00', 'confidence': 0.9, **edge}})
    assert (kept.gross_amount.source_page, kept.gross_amount.source_quote) == (200, 'x' * 300)
    for field, value in (('gross_amount', '312,00'), ('invoice_date', '15.01.2026'), ('vendor_name', '')):
        with pytest.raises(ValidationError):
            InvoiceExtractionOutput.model_validate({**ANSWER, field: {'value': value, 'confidence': 0.5, 'source_page': 0, 'source_quote': ''}})


# ---- one transport per provider; the same contract ----------------------------------------------------------

class _Responses:
    def __init__(self, outcomes):
        self.outcomes, self.calls = list(outcomes), []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class _SdkResponse:
    def __init__(self, answer=None, *, usage=(1200, 90)):
        self.output_parsed = InvoiceExtractionOutput.model_validate(answer) if answer is not None else None
        self.usage = type('Usage', (), {
            'input_tokens': usage[0], 'output_tokens': usage[1], 'total_tokens': sum(usage),
            'input_tokens_details': type('Details', (), {'cached_tokens': 0, 'cache_write_tokens': 0})(),
            'output_tokens_details': type('Output', (), {'reasoning_tokens': 0})()})()
        self.status, self.refusal, self.model, self._request_id = 'completed', None, EXTRACTION_MODEL, 'req_invoice'
        self.output = []


class OpenAiTransport:
    def __init__(self, provider):
        self.provider = provider

    def build(self, answers):
        async def close(_self):
            """The worker closes the client it was given when the run ends."""
        self.client = type('Client', (), {'responses': _Responses([_SdkResponse(answer) for answer in answers]), 'close': close})()
        return OpenAiDeclarationProvider(client=self.client, primary_model=EXTRACTION_MODEL, escalation_model=EXTRACTION_MODEL, token_encoder=_Encoder())

    def count(self):
        return len(self.client.responses.calls)

    def seen(self):
        call = self.client.responses.calls[0]
        text = '\n'.join(block['text'] for block in call['input'][1]['content'])
        return {'model': call['model'], 'text': text, 'images': [block for block in call['input'][1]['content'] if block['type'] != 'input_text']}


class CompatibleTransport:
    def __init__(self, provider):
        self.provider = provider

    def build(self, answers):
        queue, self.bodies = list(answers), []
        provider = create_extraction_provider(EffectiveAiSettings(provider=self.provider, api_key=SecretStr('synthetic-key'), model=EXTRACTION_MODEL,
            document_model=READING_MODEL, base_url='http://localhost:11434/v1' if self.provider == 'openai_compatible' else None))

        def handle(request):
            self.bodies.append(json.loads(request.content))
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
            text = json.dumps(answer) if answer is not None else '{"vendor_name": 1}'
            if self.provider == 'anthropic':
                return httpx.Response(200, json={'id': 'msg_invoice', 'model': EXTRACTION_MODEL, 'stop_reason': 'end_turn',
                    'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 1200, 'output_tokens': 90}})
            return httpx.Response(200, json={'id': 'chat_invoice', 'model': EXTRACTION_MODEL, 'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                'usage': {'prompt_tokens': 1200, 'completion_tokens': 90}})
        provider._client = ProviderClient(provider=self.provider, api_key='synthetic-key', base_url=provider._client.base_url, transport=httpx.MockTransport(handle))
        return provider

    def count(self):
        return len(self.bodies)

    def seen(self):
        body = self.bodies[0]
        content = [block for message in body['messages'] for block in message['content']]
        return {'model': body['model'], 'text': '\n'.join(block['text'] for block in content if block['type'] == 'text'),
                'images': [block for block in content if block['type'] != 'text']}


TRANSPORTS = {'openai': OpenAiTransport, 'openai_compatible': CompatibleTransport, 'anthropic': CompatibleTransport}


@pytest.fixture(params=sorted(TRANSPORTS))
def transport(request):
    return TRANSPORTS[request.param](request.param)


async def _extract(provider, model=EXTRACTION_MODEL):
    try:
        return await provider.extract_invoice([DOCUMENT], categories=CATEGORIES, model=model, max_output_tokens=2000)
    finally:
        client = getattr(provider, '_client', None)
        if isinstance(client, ProviderClient):
            await client.close()


@pytest.mark.asyncio
async def test_the_request_carries_the_pages_text_the_category_list_and_the_extraction_model(transport):
    provider = transport.build([ANSWER])
    result = await _extract(provider)
    seen = transport.seen()
    assert seen['model'] == EXTRACTION_MODEL and seen['images'] == [], 'the extraction stage never gets a page image'
    assert 'GEBAEUDEVERSICHERUNG: Gebäudeversicherung' in seen['text'] and 'GRUNDSTEUER: Grundsteuer' in seen['text']
    assert 'pdf_page=1' in seen['text'] and 'Gesamtbetrag 312,00 EUR' in seen['text'] and 'pdf_page=2' in seen['text']
    assert result.output == InvoiceExtractionOutput.model_validate(ANSWER)
    assert (result.usage.input_tokens, result.usage.output_tokens, result.usage.attempt_count) == (1200, 90, 1)
    assert result.usage_complete is True
    meta = result.metadata
    assert (meta.pass_id, meta.configured_model, meta.failure_class) == (INVOICE_PASS_ID, EXTRACTION_MODEL, None)
    assert (meta.prompt_version, meta.schema_version) == (INVOICE_PROMPT_VERSION, INVOICE_SCHEMA_VERSION)


@pytest.mark.asyncio
async def test_the_vendor_iban_is_read_as_the_invoice_prints_it_and_comes_back_normalised_through_every_transport(transport):
    answer = {**ANSWER, 'vendor_iban': {'value': 'de02 1203 0000 0000 2020 51', 'confidence': 0.9}}
    result = await _extract(transport.build([answer]))
    assert (result.output.vendor_iban.value, result.output.vendor_iban.confidence) == ('DE02120300000000202051', 0.9)
    assert transport.count() == 1


@pytest.mark.asyncio
async def test_a_wrong_vendor_iban_is_not_stated_and_costs_no_repair_request_through_every_transport(transport):
    answer = {**ANSWER, 'vendor_iban': {'value': 'DE02 1203 0000 0000 2020 52', 'confidence': 0.9}}
    result = await _extract(transport.build([answer]))
    assert result.output.vendor_iban.value is None and result.output.gross_amount.value == '312.00'
    assert transport.count() == 1, 'a wrong IBAN is not a malformed answer'


@pytest.mark.asyncio
async def test_a_malformed_answer_ends_in_a_typed_error_that_keeps_the_reported_usage(transport):
    provider = transport.build([None])
    with pytest.raises(ProviderFailure) as failure:
        await _extract(provider)
    error = failure.value
    assert error.code == PROVIDER_SCHEMA_FAILURE and error.retryable is False
    assert transport.count() == 2, 'one bounded schema-only repair'
    assert (error.usage.input_tokens, error.usage.output_tokens, error.usage.attempt_count) == (2400, 180, 2)
    assert error.usage_complete is True


@pytest.mark.asyncio
async def test_an_answer_the_sdk_cut_off_keeps_its_usage_costs_no_repair_and_is_booked_as_reported():
    """Through the real SDK client: the extraction model stopped at its output allowance in the middle of the JSON."""
    from app.declarations.invoice_pipeline import _failed_amounts
    from app.declarations.pipeline import _Reservation
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={'x-request-id': 'req_cut_off'}, json={
            'id': 'resp_invoice', 'object': 'response', 'created_at': 0, 'model': EXTRACTION_MODEL, 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'output': [{'id': 'msg_invoice', 'type': 'message', 'role': 'assistant', 'status': 'incomplete',
                        'content': [{'type': 'output_text', 'text': '{"vendor_name":{"value":"Mustermann Vers', 'annotations': []}]}],
            'usage': {'input_tokens': 1200, 'output_tokens': 2000, 'total_tokens': 3200,
                      'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 0}}})

    async with AsyncOpenAI(api_key='synthetic-sdk-key', base_url='https://api.openai.com/v1', max_retries=0,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))) as client:
        provider = OpenAiDeclarationProvider(client=client, primary_model=EXTRACTION_MODEL, escalation_model=EXTRACTION_MODEL,
                                             token_encoder=_Encoder())
        with pytest.raises(ProviderFailure) as failure:
            await provider.extract_invoice([DOCUMENT], categories=CATEGORIES, model=EXTRACTION_MODEL, max_output_tokens=2000)

    error = failure.value
    assert error.code == PROVIDER_INCOMPLETE and error.retryable is False
    assert (error.usage.input_tokens, error.usage.output_tokens, error.usage.attempt_count) == (1200, 2000, 1)
    assert error.usage_complete is True and error.attempt_count == 1
    assert len(requests) == 1, 'a cut-off answer is not repaired'
    assert (error.metadata.pass_id, error.metadata.request_id, error.metadata.failure_class) == (INVOICE_PASS_ID, 'req_cut_off', PROVIDER_INCOMPLETE)
    # The ledger of the invoice and inbox stages books the reported usage of the failed request, not its reservation.
    booked = _failed_amounts(error, _Reservation(input_tokens=9000, output_tokens=2000, cost_usd=Decimal('0.5')))
    assert booked == (1200, 2000, error.usage.cost_usd, True)


@pytest.mark.asyncio
@pytest.mark.parametrize('model,effort', [('gpt-6.2-sol', 'low'), ('gpt-5.6-luna', 'medium'), ('gpt-5.4-mini', 'none')],
                         ids=['unknown-model', 'luna', 'mini'])
async def test_the_real_sdk_extraction_request_carries_a_low_effort_for_an_unknown_model_and_the_known_efforts_otherwise(model, effort):
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={
            'id': 'resp_invoice', 'object': 'response', 'created_at': 0, 'model': model, 'status': 'completed',
            'output': [{'id': 'msg_invoice', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': json.dumps(ANSWER), 'annotations': []}]}],
            'usage': {'input_tokens': 1200, 'output_tokens': 90, 'total_tokens': 1290,
                      'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 0}}})

    async with AsyncOpenAI(api_key='synthetic-sdk-key', base_url='https://api.openai.com/v1', max_retries=0,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))) as client:
        provider = OpenAiDeclarationProvider(client=client, primary_model=model, escalation_model=model, token_encoder=_Encoder())
        result = await provider.extract_invoice([DOCUMENT], categories=CATEGORIES, model=model, max_output_tokens=2000)

    body, = bodies
    assert body['reasoning'] == {'effort': effort}
    assert result.metadata.reasoning_effort == effort


@pytest.mark.asyncio
async def test_no_other_model_is_asked(transport):
    provider = transport.build([ANSWER])
    with pytest.raises(ProviderFailure) as failure:
        await _extract(provider, model='some-other-model')
    assert failure.value.code == 'MODEL_RATE_NOT_CONFIGURED'
    assert transport.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [BAD_SOURCES[name] for name in ('empty-quote', 'quote-301-characters', 'page-0', 'page-201')],
                         ids=['empty-quote', 'quote-301-characters', 'page-0', 'page-201'])
async def test_a_badly_filled_source_costs_no_repair_request_and_no_answer_through_every_transport(transport, source):
    answer = {**ANSWER, 'gross_amount': {'value': '312.00', 'confidence': 0.9, **source},
              'vendor_name': {'value': 'Mustermann Versicherungen AG', 'confidence': 0.9, 'source_page': 1, 'source_quote': 'Mustermann Versicherungen AG'}}
    result = await _extract(transport.build([answer]))
    assert transport.count() == 1, 'a bad source is not a malformed answer'
    assert result.usage_complete is True and result.metadata.failure_class is None
    assert (result.output.gross_amount.value, result.output.gross_amount.source_page, result.output.gross_amount.source_quote) == ('312.00', None, None)
    assert (result.output.vendor_name.source_page, result.output.vendor_name.source_quote) == (1, 'Mustermann Versicherungen AG'), 'a good source beside it stays'


# ---- the reservation ----------------------------------------------------------------------------------------

def test_the_reservation_covers_both_possible_requests_and_grows_with_the_text(transport):
    provider = transport.build([ANSWER])
    estimate = provider.estimate_reservation('extract_invoice', max_output_tokens=3000, documents=[DOCUMENT], categories=CATEGORIES)
    assert estimate.attempt_count == 2 and estimate.input_tokens == sum(estimate.attempt_input_tokens)
    longer = DocumentInput('invoice', (*DOCUMENT.pages, PageInput('invoice', 3, 'Position ' * 2000, 1.0)))
    bigger = provider.estimate_reservation('extract_invoice', max_output_tokens=3000, documents=[longer], categories=CATEGORIES)
    assert bigger.input_tokens > estimate.input_tokens + 2000
    more = provider.estimate_reservation('extract_invoice', max_output_tokens=3000, documents=[DOCUMENT], categories=(*CATEGORIES, InvoiceCategory('AUFZUG', 'Aufzug')))
    assert more.input_tokens > estimate.input_tokens


def test_a_page_image_is_not_accepted_by_the_extraction_stage(transport):
    provider = transport.build([ANSWER])
    with_image = DocumentInput('invoice', (PageInput('invoice', 1, 'text', 1.0, image_data_url='data:image/png;base64,AAAA'),))
    with pytest.raises(ProviderFailure):
        provider.estimate_reservation('extract_invoice', max_output_tokens=3000, documents=[with_image], categories=CATEGORIES)


# ---- the fake ------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_fake_records_which_model_was_asked_about_which_pages_and_fails_on_request():
    fake = FakeInvoiceExtractionProvider()
    result = await fake.extract_invoice([DOCUMENT], categories=CATEGORIES, model='the-extraction-model', max_output_tokens=100)
    assert result.output.gross_amount.value == '312.00' and result.metadata.pass_id == INVOICE_PASS_ID
    call = fake.calls[0]
    assert (call.model, call.pages, call.categories) == ('the-extraction-model', (1, 2), ('GEBAEUDEVERSICHERUNG', 'GRUNDSTEUER'))
    assert 'Gesamtbetrag 312,00 EUR' in call.text
    failing = FakeInvoiceExtractionProvider(failure=ProviderFailure('PROVIDER_TIMEOUT', False, 'timed out'))
    with pytest.raises(ProviderFailure):
        await failing.extract_invoice([DOCUMENT], categories=CATEGORIES, model='m', max_output_tokens=100)
    assert len(failing.calls) == 1
    assert synthetic_invoice_output(gross_amount=('-5.00', 0.5)).gross_amount.value == '-5.00'
    assert Decimal(fake.estimate_reservation('extract_invoice', max_output_tokens=1).input_tokens) > 0
