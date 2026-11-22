"""Document reading stage: one rendered page in, a typed page transcript out, per transport (issue #499, ADR 0017)."""
import base64
import io
import json
from decimal import Decimal

import httpx
import httpx2
import pytest
from openai import APIStatusError, AsyncOpenAI, BadRequestError, InternalServerError, UnprocessableEntityError
from PIL import Image
from pydantic import SecretStr, ValidationError

from app.declarations import provider as provider_contract
from app.declarations.fake_provider import FakeDocumentReadingProvider
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.prompts import PAGE_READING_PROMPT, READING_PROMPT_VERSION
from app.declarations.provider import (
    PROVIDER_IMAGE_INPUT_UNSUPPORTED,
    PROVIDER_INCOMPLETE,
    PROVIDER_REFUSAL,
    PROVIDER_SCHEMA_FAILURE,
    PageImage,
    ProviderFailure,
)
from app.declarations.provider_runtime import ProviderClient, create_reading_provider
from app.declarations.schemas import (
    READING_SCHEMA_VERSION,
    UNREADABLE_MARKER,
    PageReadingOutput,
    PageTranscript,
    ReadingBlock,
)
from app.services.ai_settings_service import EffectiveAiSettings

pytestmark = pytest.mark.no_db

READING_MODEL = 'gpt-5.4-mini'
EXTRACTION_MODEL = 'gpt-5.6-luna'
_buffer = io.BytesIO()
Image.new('RGB', (4, 4), 'white').save(_buffer, format='PNG')
PNG = _buffer.getvalue()
PNG_B64 = base64.b64encode(PNG).decode('ascii')
PAGE = PageImage(document_key='scan-1', pdf_page=7, media_type='image/png', data=PNG)

ANSWER = {'blocks': [
    {'kind': 'TEXT', 'text': 'Teilungserklärung, Seite 7', 'rows': None},
    {'kind': 'TABLE', 'text': None, 'rows': [['Einheit', 'MEA'], ['Whg 1', '123/1000'], ['Whg 2', UNREADABLE_MARKER]]},
    {'kind': 'UNREADABLE', 'text': None, 'rows': None},
]}


class _Encoder:
    @staticmethod
    def encode(value):
        return list(value.encode('utf-8'))


# ---- the typed transcript ----------------------------------------------------------------------

def test_the_transcript_keeps_block_order_tables_as_rows_and_unreadable_material():
    parsed = PageReadingOutput.model_validate(ANSWER)
    assert [block.kind for block in parsed.blocks] == ['TEXT', 'TABLE', 'UNREADABLE']
    assert parsed.blocks[1].rows[2] == ['Whg 2', UNREADABLE_MARKER]
    assert PageReadingOutput.model_validate({'blocks': []}).blocks == []


@pytest.mark.parametrize('block', [
    {'kind': 'TEXT', 'text': None, 'rows': None},
    {'kind': 'TEXT', 'text': '', 'rows': None},
    {'kind': 'TEXT', 'text': 'x', 'rows': [['x']]},
    {'kind': 'TABLE', 'text': None, 'rows': None},
    {'kind': 'TABLE', 'text': None, 'rows': []},
    {'kind': 'TABLE', 'text': 'x', 'rows': [['x']]},
    {'kind': 'UNREADABLE', 'text': 'x', 'rows': None},
    {'kind': 'UNREADABLE', 'text': None, 'rows': [['x']]},
    {'kind': 'NOTE', 'text': 'x', 'rows': None},
    {'kind': 'TEXT', 'text': 'x', 'rows': None, 'confidence': 0.9},
])
def test_a_block_that_is_not_what_its_kind_says_is_refused(block):
    with pytest.raises(ValidationError):
        ReadingBlock.model_validate(block)


def test_the_wire_schema_has_no_union_the_transports_cannot_express():
    from app.declarations.openai_provider import _sdk_output_type
    schema = _sdk_output_type(PageReadingOutput).model_json_schema()
    assert 'oneOf' not in json.dumps(schema) and 'discriminator' not in json.dumps(schema)
    assert set(schema['$defs']['ReadingBlock']['required']) == {'kind', 'text', 'rows'}
    assert 'pdf_page' not in json.dumps(schema), 'page identity is assigned by the caller, never by the model'


def test_the_prompt_asks_to_copy_mark_unreadable_and_not_to_interpret():
    text = PAGE_READING_PROMPT.lower()
    assert 'copy' in text and UNREADABLE_MARKER in PAGE_READING_PROMPT
    assert 'do not interpret' in text and 'invent' in text
    assert (READING_PROMPT_VERSION, READING_SCHEMA_VERSION) == ('reading-v1', 'reading-1')


# ---- one transport per provider; the same contract -------------------------------------------

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
    """What the OpenAI SDK hands back for a parsed Responses call."""
    def __init__(self, answer=None, *, usage=(100, 5), status='completed', refusal=None):
        self.output_parsed = PageReadingOutput.model_validate(answer) if answer is not None else None
        self.usage = None if usage is None else type('Usage', (), {
            'input_tokens': usage[0], 'output_tokens': usage[1], 'total_tokens': sum(usage),
            'input_tokens_details': type('Details', (), {'cached_tokens': 0, 'cache_write_tokens': 0})(),
            'output_tokens_details': type('Output', (), {'reasoning_tokens': 0})()})()
        self.status, self.refusal, self.model, self._request_id = status, refusal, READING_MODEL, 'req_reading'
        self.output = []


def _status_error(status, fields=None):
    request = httpx.Request('POST', 'https://api.openai.com/v1/responses')
    cls = {400: BadRequestError, 422: UnprocessableEntityError}.get(status, InternalServerError if status >= 500 else APIStatusError)
    return cls((fields or {}).get('message', 'rejected'), response=httpx.Response(status, request=request), body=fields)


class Transport:
    """One provider transport under test: how it is built, what it sends and how it answers."""
    def __init__(self, provider):
        self.provider = provider
        self.requests = []

    def build(self, answers):
        """`answers`: ('ok', answer) / ('raw', text) / ('refusal',) / ('status', code[, error fields]); the last repeats."""
        raise NotImplementedError


class OpenAiTransport(Transport):
    def build(self, answers):
        outcomes = []
        for kind, *rest in answers:
            if kind == 'ok':
                outcomes.append(_SdkResponse(rest[0]))
            elif kind == 'raw':
                outcomes.append(_SdkResponse(None))
            elif kind == 'refusal':
                outcomes.append(_SdkResponse(None, refusal='refused'))
            else:
                outcomes.append(_status_error(*rest))
        self.client = type('Client', (), {'responses': _Responses(outcomes)})()
        return OpenAiDeclarationProvider(client=self.client, primary_model=READING_MODEL, escalation_model=READING_MODEL, token_encoder=_Encoder())

    def count(self):
        return len(self.client.responses.calls)

    def seen(self):
        call = self.client.responses.calls[0]
        image = next(block for block in call['input'][1]['content'] if block['type'] == 'input_image')
        return {'model': call['model'], 'image': image['image_url'], 'calls': len(self.client.responses.calls)}


class CompatibleTransport(Transport):
    def build(self, answers):
        queue, self.bodies = list(answers), []
        provider = create_reading_provider(EffectiveAiSettings(provider=self.provider, api_key=SecretStr('synthetic-key'), model=EXTRACTION_MODEL,
            document_model=READING_MODEL, base_url='http://localhost:11434/v1' if self.provider == 'openai_compatible' else None))
        def handle(request):
            self.bodies.append(json.loads(request.content))
            kind, *rest = queue.pop(0) if len(queue) > 1 else queue[0]
            if kind == 'status':
                return httpx.Response(rest[0], json={'error': {'message': 'any text', 'type': 'invalid_request_error',
                                                               **(rest[1] if len(rest) > 1 else {})}})
            text = json.dumps(rest[0]) if kind == 'ok' else '' if kind == 'refusal' else '{"blocks": "not a list"}'
            if self.provider == 'anthropic':
                return httpx.Response(200, json={'id': 'msg_reading', 'model': READING_MODEL, 'stop_reason': 'refusal' if kind == 'refusal' else 'end_turn',
                    'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 100, 'output_tokens': 5}})
            message = {'content': text, **({'refusal': 'no'} if kind == 'refusal' else {})}
            return httpx.Response(200, json={'id': 'chat_reading', 'model': READING_MODEL, 'choices': [{'finish_reason': 'stop', 'message': message}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 5}})
        provider._client = ProviderClient(provider=self.provider, api_key='synthetic-key', base_url=provider._client.base_url, transport=httpx.MockTransport(handle))
        return provider

    def count(self):
        return len(self.bodies)

    def seen(self):
        body = self.bodies[0]
        content = [block for message in body['messages'] for block in message['content']]
        if self.provider == 'anthropic':
            image = next(block for block in content if block['type'] == 'image')
            assert image['source']['media_type'] == 'image/png'
            data = 'data:image/png;base64,' + image['source']['data']
        else:
            data = next(block for block in content if block['type'] == 'image_url')['image_url']['url']
        return {'model': body['model'], 'image': data, 'calls': len(self.bodies)}


TRANSPORTS = {'openai': OpenAiTransport, 'openai_compatible': CompatibleTransport, 'anthropic': CompatibleTransport}


@pytest.fixture(params=sorted(TRANSPORTS))
def transport(request):
    return TRANSPORTS[request.param](request.param)


async def _read(provider, page=PAGE, model=READING_MODEL):
    try:
        return await provider.read_page(page, model=model, max_output_tokens=2000)
    finally:
        client = getattr(provider, '_client', None)
        if isinstance(client, ProviderClient):
            await client.close()


@pytest.mark.asyncio
async def test_the_request_carries_the_page_image_and_the_reading_model_and_the_answer_is_typed(transport):
    provider = transport.build([('ok', ANSWER)])
    result = await _read(provider)
    seen = transport.seen()
    assert seen == {'model': READING_MODEL, 'image': 'data:image/png;base64,' + PNG_B64, 'calls': 1}
    assert result.output == PageTranscript(document_key='scan-1', pdf_page=7, blocks=PageReadingOutput.model_validate(ANSWER).blocks)
    assert result.usage.input_tokens == 100 and result.usage.output_tokens == 5 and result.usage.attempt_count == 1
    assert result.usage_complete is True
    meta = result.metadata
    assert (meta.pass_id, meta.configured_model, meta.failure_class) == ('read_page', READING_MODEL, None)
    assert (meta.prompt_version, meta.schema_version) == (READING_PROMPT_VERSION, READING_SCHEMA_VERSION)


@pytest.mark.asyncio
async def test_a_malformed_answer_ends_in_a_typed_error_that_keeps_the_reported_usage(transport):
    provider = transport.build([('raw',)])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider)
    error = failure.value
    assert error.code == PROVIDER_SCHEMA_FAILURE and error.retryable is False
    # The bounded schema-only repair ran once more; both legs' reported usage is kept, none is dropped.
    assert transport.count() == 2
    assert (error.usage.input_tokens, error.usage.output_tokens, error.usage.attempt_count) == (200, 10, 2)
    assert error.usage_complete is True


@pytest.mark.asyncio
async def test_a_page_whose_transcript_the_sdk_cut_off_keeps_its_usage_and_is_read_once():
    """Through the real SDK client: the reading model stopped at its output allowance in the middle of the JSON."""
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx2.Response(200, headers={'x-request-id': 'req_cut_off'}, json={
            'id': 'resp_reading', 'object': 'response', 'created_at': 0, 'model': READING_MODEL, 'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'output': [{'id': 'msg_reading', 'type': 'message', 'role': 'assistant', 'status': 'incomplete',
                        'content': [{'type': 'output_text', 'text': '{"blocks":[{"kind":"TEXT","text":"Teilungs', 'annotations': []}]}],
            'usage': {'input_tokens': 100, 'output_tokens': 2000, 'total_tokens': 2100,
                      'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 0}}})

    async with AsyncOpenAI(api_key='synthetic-sdk-key', base_url='https://api.openai.com/v1', max_retries=0,
                           http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond))) as client:
        provider = OpenAiDeclarationProvider(client=client, primary_model=READING_MODEL, escalation_model=READING_MODEL,
                                             token_encoder=_Encoder())
        with pytest.raises(ProviderFailure) as failure:
            await provider.read_page(PAGE, model=READING_MODEL, max_output_tokens=2000)

    error = failure.value
    assert error.code == PROVIDER_INCOMPLETE
    assert (error.usage.input_tokens, error.usage.output_tokens, error.usage.attempt_count) == (100, 2000, 1)
    assert error.usage_complete is True and error.attempt_count == 1
    assert len(requests) == 1, 'a cut-off transcript is not repaired'
    assert (error.metadata.pass_id, error.metadata.request_id, error.metadata.failure_class) == ('read_page', 'req_cut_off', PROVIDER_INCOMPLETE)


@pytest.mark.asyncio
async def test_a_refused_answer_ends_in_a_typed_error_that_keeps_the_reported_usage(transport):
    provider = transport.build([('refusal',)])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider)
    assert failure.value.code == PROVIDER_REFUSAL and failure.value.retryable is False
    assert (failure.value.usage.input_tokens, failure.value.usage.output_tokens) == (100, 5)
    assert transport.count() == 1


GENERAL_REJECTION = 'OPENAI_REQUEST_FAILED'  # the code an extraction pass gets for a rejected request


async def _failure_of(transport, answer):
    provider = transport.build([answer])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider)
    # Nothing else is tried: no other model, provider or text path, and a rejected request is not repeated.
    assert transport.count() == 1 and failure.value.retryable is False
    return failure.value


@pytest.mark.asyncio
async def test_status_415_says_the_model_cannot_take_the_image(transport):
    failure = await _failure_of(transport, ('status', 415))
    assert failure.code == PROVIDER_IMAGE_INPUT_UNSUPPORTED
    assert failure.metadata.failure_class == PROVIDER_IMAGE_INPUT_UNSUPPORTED


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 422])
@pytest.mark.parametrize('fields', [
    {'param': 'messages.0.content.1.image_url.url'},
    {'param': 'input[0].content[1].image_url'},
    {'param': 'image'},
    {'code': 'invalid_image_format', 'type': 'invalid_request_error'},
], ids=['chat-image-url', 'responses-image-url', 'image', 'image-code'])
async def test_a_rejection_whose_structured_fields_point_at_the_image_says_the_model_cannot_take_it(transport, status, fields):
    failure = await _failure_of(transport, ('status', status, fields))
    assert failure.code == PROVIDER_IMAGE_INPUT_UNSUPPORTED
    assert failure.metadata.failure_class == PROVIDER_IMAGE_INPUT_UNSUPPORTED


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [400, 422])
@pytest.mark.parametrize('fields', [
    {},
    {'param': 'max_output_tokens', 'code': 'unsupported_parameter'},
    {'param': 'response_format', 'code': 'invalid_value'},
    {'param': 'messages.0.content.0.text', 'code': 'string_above_max_length', 'type': 'invalid_request_error'},
    {'param': 'imagery_mode'},
], ids=['no-fields', 'output-limit', 'output-format', 'text-part', 'similar-name'])
async def test_any_other_rejected_request_keeps_the_general_code_even_when_its_message_names_images(transport, status, fields):
    # The messages the mocks send are 'any text'; a real one may say anything, and a message never decides.
    failure = await _failure_of(transport, ('status', status, fields))
    assert failure.code == GENERAL_REJECTION
    assert failure.metadata.failure_class == GENERAL_REJECTION


@pytest.mark.asyncio
async def test_a_message_about_images_without_a_structured_pointer_is_not_evidence(transport):
    failure = await _failure_of(transport, ('status', 400, {'message': 'Invalid image: this model does not support image inputs'}))
    assert failure.code == GENERAL_REJECTION


@pytest.mark.asyncio
async def test_the_classification_follows_the_status_code_never_the_message(transport):
    # A server error is still a server error, whatever its text says about images.
    provider = transport.build([('status', 503)])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider)
    assert failure.value.code == 'PROVIDER_SERVER_ERROR' and failure.value.retryable is True


@pytest.mark.asyncio
async def test_only_the_configured_reading_model_is_ever_asked(transport):
    provider = transport.build([('ok', ANSWER)])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider, model=EXTRACTION_MODEL)
    assert failure.value.code == 'MODEL_RATE_NOT_CONFIGURED'
    assert transport.count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('page', [
    PageImage('scan-1', 7, 'image/png', b'not a png'),
    PageImage('scan-1', 7, 'image/png', b''),
    PageImage('scan-1', 7, 'image/jpeg', PNG),
])
async def test_an_image_the_transport_cannot_carry_is_refused_before_any_request(transport, page):
    provider = transport.build([('ok', ANSWER)])
    with pytest.raises(ProviderFailure) as failure:
        await _read(provider, page=page)
    assert failure.value.code == 'OPENAI_IMAGE_INPUT_INVALID' and failure.value.retryable is False
    assert transport.count() == 0


# ---- the factory: the reading model, never a fallback --------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['openai', 'openai_compatible', 'anthropic'])
async def test_the_reading_provider_is_bound_to_the_document_model_and_never_falls_back(name):
    effective = EffectiveAiSettings(provider=name, api_key=SecretStr('synthetic-key'), model=EXTRACTION_MODEL, document_model=READING_MODEL,
        base_url='http://localhost:11434/v1' if name == 'openai_compatible' else None)
    provider = create_reading_provider(effective)
    try:
        assert provider._primary_model == provider._escalation_model == READING_MODEL
    finally:
        if getattr(provider, '_client', None) is not None:
            await provider._client.close()
    with pytest.raises(ProviderFailure) as failure:
        create_reading_provider(EffectiveAiSettings(provider=name, api_key=SecretStr('synthetic-key'), model=EXTRACTION_MODEL, document_model=None,
            base_url='http://localhost:11434/v1' if name == 'openai_compatible' else None))
    assert failure.value.code == 'DOCUMENT_MODEL_NOT_CONFIGURED'


# ---- the deterministic fake ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_fake_reading_provider_records_which_model_was_asked_for_which_page():
    fake = FakeDocumentReadingProvider(transcripts={('scan-1', 7): [ReadingBlock(kind='TEXT', text='Seite sieben')]})
    other = PageImage('scan-2', 1, 'image/png', PNG)
    first = await fake.read_page(PAGE, model=READING_MODEL, max_output_tokens=100)
    second = await fake.read_page(other, model='another-reader', max_output_tokens=100)
    assert [(r.model, r.document_key, r.pdf_page) for r in fake.reads] == [(READING_MODEL, 'scan-1', 7), ('another-reader', 'scan-2', 1)]
    assert first.output == PageTranscript(document_key='scan-1', pdf_page=7, blocks=[ReadingBlock(kind='TEXT', text='Seite sieben')])
    assert second.output.pdf_page == 1 and second.output.blocks, 'an unscripted page still gets a deterministic transcript'
    assert first.usage.cost_usd == Decimal('0.000001') and first.metadata.pass_id == 'read_page'
    assert first.metadata.configured_model == READING_MODEL and second.metadata.configured_model == 'another-reader'


@pytest.mark.asyncio
async def test_the_fake_reading_provider_can_fail_a_page_with_the_usage_it_was_given():
    failure = ProviderFailure(PROVIDER_IMAGE_INPUT_UNSUPPORTED, False, 'The model cannot take the image input.')
    fake = FakeDocumentReadingProvider(failures={('scan-1', 7): failure})
    with pytest.raises(ProviderFailure) as raised:
        await fake.read_page(PAGE, model=READING_MODEL, max_output_tokens=100)
    assert raised.value is failure
    assert [(r.model, r.pdf_page) for r in fake.reads] == [(READING_MODEL, 7)], 'the asked page is recorded even when it fails'


def test_the_contract_names_one_reading_call_per_page():
    assert provider_contract.DocumentReadingProvider.read_page
    assert PageImage.__dataclass_fields__.keys() == {'document_key', 'pdf_page', 'media_type', 'data'}
