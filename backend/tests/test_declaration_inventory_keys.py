"""The inventory request says which document keys an answer may use (issue #876).

The model-assisted import starts with an inventory over the supplied pages. Every page is labelled
``document_key=<key>``, and one key is one uploaded file; an answer with an entry for a deed inside a file, or for a
key the import does not hold, failed the whole import with ``UNKNOWN_INVENTORY_DOCUMENT``. These tests pin what every
transport sends for an inventory: the instruction, and a schema whose entry ``document_key`` allows only the keys of
that request. The fingerprints of the pass stay those of the static contract. No test calls a provider: the real SDK
client and the compatible client run over stubbed transports.
"""

from __future__ import annotations

import json

import httpx
import httpx2
import pytest
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import SecretStr, ValidationError

import app.declarations.openai_provider as openai_provider
from app.declarations.observability import fingerprint_static_contract
from app.declarations.openai_provider import OpenAiDeclarationProvider
from app.declarations.prompts import INVENTORY_PROMPT
from app.declarations.provider import DocumentInput, PageInput, ProviderFailure
from app.declarations.provider_runtime import (
    ProviderClient,
    _anthropic_schema,
    _schema_instruction,
    create_extraction_provider,
)
from app.declarations.schemas import (
    DocumentInventoryItem,
    DocumentInventoryOutput,
    DocumentRelationPayload,
    inventory_output_type,
    static_output_type,
)
from app.services.ai_settings_service import EffectiveAiSettings
from tests.test_declaration_openai_provider import CapturingTokenEncoder, _sdk_client, _sdk_response


def _document(key: str, *pages: int) -> DocumentInput:
    return DocumentInput(key, tuple(PageInput(key, page, f"Seite {page} von {key}", 1.0) for page in pages or (1,)))


def _answer(*keys: str) -> DocumentInventoryOutput:
    return DocumentInventoryOutput(
        documents=[{"document_key": key, "document_type": "DECLARATION", "title": "Synthetic"} for key in keys]
    )


def _allowed_keys(wire_schema: dict) -> list[str]:
    """The values that the schema allows for an entry's ``document_key`` (a single value is a ``const``)."""
    schema = wire_schema["$defs"]["DocumentInventoryItem"]["properties"]["document_key"]
    return schema["enum"] if "enum" in schema else [schema["const"]]


# ---- the instruction and the schema descriptions ----------------------------------------------------------------


def test_the_instruction_states_the_rule_for_entry_keys_and_relation_targets() -> None:
    lines = " ".join(INVENTORY_PROMPT.split())
    for sentence in (
        "one key stands for one uploaded file",
        "Return exactly one entry in documents for every document_key that appears in the supplied pages, "
        "and no entry for any other key.",
        "Copy the key character for character into the entry's document_key",
        "never use a title, a file name or an invented key in its place",
        "The pages supplied may be only some pages of a file. That file still gets its one entry under its key.",
        "A file may contain several deeds",
        "That is still one entry for the file: give it the type and title of its main deed.",
        "A deed inside a file never gets an entry of its own",
        "A relation's target_document_key is the document_key of the supplied document it points to, copied exactly.",
        "A relation to a document that was not supplied names that document by the words of the text; "
        "never make up a key for it.",
    ):
        assert sentence in lines, sentence


def test_the_schema_descriptions_state_the_rule_for_the_entry_key_and_the_relation_target() -> None:
    entry = DocumentInventoryItem.model_json_schema()["properties"]["document_key"]["description"]
    target = DocumentRelationPayload.model_json_schema()["properties"]["target_document_key"]["description"]
    assert "document_key of exactly one supplied file" in entry
    assert "a deed inside a file has no entry of its own" in entry
    assert "document_key of a supplied document" in target
    assert "words of the text" in target


# ---- the type of one request ------------------------------------------------------------------------------------


def test_the_type_of_a_request_allows_only_the_keys_of_its_documents() -> None:
    restricted = inventory_output_type(["b-file", "a-file", "a-file"])
    assert restricted is inventory_output_type(("a-file", "b-file")), "one class per set of keys, in any order"
    assert issubclass(restricted, DocumentInventoryOutput) and restricted.__name__ == "DocumentInventoryOutput"
    assert _allowed_keys(type_to_text_format_param(restricted)["schema"]) == ["a-file", "b-file"]
    assert restricted.model_validate(_answer("b-file", "a-file").model_dump()).documents[0].document_key == "b-file"
    with pytest.raises(ValidationError):
        restricted.model_validate(_answer("not-supplied").model_dump())
    # A relation target stays free text: a document that was not supplied is named by the words of the text.
    free_target = {
        "document_key": "a-file", "document_type": "DECLARATION", "title": "Synthetic",
        "relations": [{"relation_type": "REFERENCES", "target_document_key": "Aufteilungsplan",
                       "evidence": [{"document_key": "a-file", "pdf_page": 1, "quote": "Text"}]}],
    }
    assert restricted.model_validate({"documents": [free_target]}).documents[0].relations[0].target_document_key == "Aufteilungsplan"
    # The pipeline and the stage after the inventory keep working with the base type.
    assert static_output_type(restricted) is DocumentInventoryOutput
    assert static_output_type(DocumentInventoryOutput) is DocumentInventoryOutput


def test_a_request_without_documents_keeps_the_base_type() -> None:
    assert inventory_output_type([]) is DocumentInventoryOutput


# ---- the native OpenAI transport ---------------------------------------------------------------------------------


def _native_provider(client, encoder=None) -> OpenAiDeclarationProvider:
    return OpenAiDeclarationProvider(
        client=client, primary_model="gpt-5.6-luna", escalation_model="gpt-5.4-mini",
        token_encoder=encoder or CapturingTokenEncoder(),
    )


@pytest.mark.asyncio
async def test_native_request_carries_the_instruction_and_a_schema_limited_to_the_keys_of_that_request() -> None:
    requests = []
    answers = [_answer("file-b", "file-a"), _answer("file-a")]

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        return httpx2.Response(200, json=_sdk_response(answers[len(requests) - 1].model_dump(mode="json"), wire["model"]))

    encoder = CapturingTokenEncoder()
    async with _sdk_client(respond) as client:
        provider = _native_provider(client, encoder)
        both = [_document("file-b", 1), _document("file-a", 1, 2)]
        # A later batch of the same import holds only some pages of one file: its request names that file only.
        only_some_pages = [_document("file-a", 3)]
        provider.estimate_reservation("inventory", documents=both, max_output_tokens=10)
        first = await provider.inventory(both, max_output_tokens=10)
        second = await provider.inventory(only_some_pages, max_output_tokens=10)

    assert [_allowed_keys(wire["text"]["format"]["schema"]) for wire in requests] == [
        ["file-a", "file-b"], ["file-a"],
    ]
    for wire in requests:
        assert wire["input"][0]["content"][0]["text"] == INVENTORY_PROMPT
        assert wire["text"]["format"]["name"] == "DocumentInventoryOutput" and wire["text"]["format"]["strict"] is True
    # The reservation was estimated over the schema that the request carries.
    assert json.loads(encoder.values[0])["text"]["format"] == requests[0]["text"]["format"]
    # The provider hands the pipeline the base type, whatever restricted class parsed the answer.
    assert type(first.output) is DocumentInventoryOutput and first.output == answers[0]
    assert type(second.output) is DocumentInventoryOutput and second.output == answers[1]


@pytest.mark.asyncio
async def test_native_fingerprints_are_those_of_the_static_contract_whatever_the_keys_of_the_request() -> None:
    def respond(request):
        wire = json.loads(request.content)
        keys = _allowed_keys(wire["text"]["format"]["schema"])
        return httpx2.Response(200, json=_sdk_response(_answer(*keys).model_dump(mode="json"), wire["model"]))

    async with _sdk_client(respond) as client:
        provider = _native_provider(client)
        one = await provider.inventory([_document("one-file")], max_output_tokens=10)
        two = await provider.inventory([_document("other-file"), _document("third-file")], max_output_tokens=10)

    base = fingerprint_static_contract(
        developer_prompt=INVENTORY_PROMPT,
        schema=type_to_text_format_param(DocumentInventoryOutput),
        harness=openai_provider._STATIC_HARNESS,
    )
    for result in (one, two):
        assert (result.metadata.prompt_sha256, result.metadata.schema_sha256, result.metadata.harness_sha256) == (
            base.prompt_sha256, base.schema_sha256, base.harness_sha256,
        )


@pytest.mark.asyncio
async def test_native_answer_with_a_key_outside_the_request_is_a_schema_failure_after_one_repair() -> None:
    requests = []

    def respond(request):
        wire = json.loads(request.content)
        requests.append(wire)
        return httpx2.Response(200, json=_sdk_response(_answer("a-deed-inside-the-file").model_dump(mode="json"), wire["model"]))

    async with _sdk_client(respond) as client:
        provider = _native_provider(client)
        with pytest.raises(ProviderFailure) as caught:
            await provider.inventory([_document("the-file", 1, 2, 3)], max_output_tokens=100)

    assert caught.value.code == "PROVIDER_SCHEMA_FAILURE"
    assert len(requests) == 2 and caught.value.attempt_count == 2
    assert caught.value.usage is not None and caught.value.usage.input_tokens == 200
    assert all(_allowed_keys(wire["text"]["format"]["schema"]) == ["the-file"] for wire in requests)
    assert "document_key" not in str(caught.value) and "a-deed-inside-the-file" not in str(caught.value)


# ---- the compatible and the Anthropic transports --------------------------------------------------------------------


def _chat_response(name: str, output: DocumentInventoryOutput) -> httpx.Response:
    text = output.model_dump_json()
    if name == "anthropic":
        return httpx.Response(200, json={"id": "msg_test", "model": "local-model", "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": text}],
                                         "usage": {"input_tokens": 100, "output_tokens": 5}})
    return httpx.Response(200, json={"id": "chat_test", "model": "local-model",
                                     "choices": [{"finish_reason": "stop", "message": {"content": text}}],
                                     "usage": {"prompt_tokens": 100, "completion_tokens": 5}})


def _sent_schema(name: str, body: dict) -> dict:
    return body["output_config"]["format"]["schema"] if name == "anthropic" else body["response_format"]["json_schema"]["schema"]


def _sent_instruction_texts(name: str, body: dict) -> list[str]:
    blocks = body["system"] if name == "anthropic" else body["messages"][0]["content"]
    return [block["text"] for block in blocks]


async def _run_wire_provider(name: str, answers, calls):
    """Run the inventory of each call against a provider whose client answers with the given outputs in turn."""
    seen: list[dict] = []
    effective = EffectiveAiSettings(
        provider=name, api_key=SecretStr("synthetic-key"), model="local-model",
        base_url="http://localhost:11434/v1" if name == "openai_compatible" else None,
    )
    provider = create_extraction_provider(effective)
    await provider._client.close()

    def respond(request):
        seen.append(json.loads(request.content))
        return _chat_response(name, answers[min(len(seen), len(answers)) - 1])

    provider._client = ProviderClient(provider=name, api_key="synthetic-key", base_url=effective.base_url,
                                      transport=httpx.MockTransport(respond))
    results, failure = [], None
    try:
        for documents in calls:
            try:
                results.append((provider.estimate_reservation("inventory", documents=documents, max_output_tokens=100),
                                await provider.inventory(documents, max_output_tokens=100)))
            except ProviderFailure as error:
                failure = error
    finally:
        await provider._client.close()
    return provider, seen, results, failure


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai_compatible", "anthropic"])
async def test_wire_request_carries_the_instruction_and_a_schema_limited_to_the_keys_of_that_request(name) -> None:
    both = [_document("file-b", 1), _document("file-a", 1, 2)]
    only_some_pages = [_document("file-a", 3)]
    _provider, seen, results, failure = await _run_wire_provider(
        name, [_answer("file-b", "file-a"), _answer("file-a")], [both, only_some_pages]
    )

    assert failure is None and len(seen) == 2
    assert [_allowed_keys(_sent_schema(name, body)) for body in seen] == [["file-a", "file-b"], ["file-a"]]
    for body in seen:
        texts = _sent_instruction_texts(name, body)
        assert texts[0] == INVENTORY_PROMPT
        if name == "openai_compatible":
            # The compatible transport also shows the exact schema to the model, so the key list is in the text.
            assert json.dumps(_sent_schema(name, body), ensure_ascii=False, sort_keys=True, separators=(",", ":")) in texts[1]
    # The reservation was estimated over the request that was sent, schema included.
    assert results[0][0].input_tokens >= len(json.dumps(seen[0], ensure_ascii=False).encode())
    assert [type(result.output) for _estimate, result in results] == [DocumentInventoryOutput] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai_compatible", "anthropic"])
async def test_wire_fingerprints_are_those_of_the_static_contract_whatever_the_keys_of_the_request(name) -> None:
    provider, _seen, results, failure = await _run_wire_provider(
        name, [_answer("one-file"), _answer("other-file", "third-file")],
        [[_document("one-file")], [_document("other-file"), _document("third-file")]],
    )

    assert failure is None
    schema = type_to_text_format_param(DocumentInventoryOutput)["schema"]
    if name == "anthropic":
        schema = _anthropic_schema(schema)
        prompt, harness = INVENTORY_PROMPT, "declaration-anthropic-v6|structured-output|utf8-byte-reservation|no-tools|bounded-schema-repair|fixed-pass-manager|inventory-document-key-enum"
    else:
        prompt = INVENTORY_PROMPT + "\n\n" + _schema_instruction(schema)
        harness = "declaration-openai_compatible-v9|structured-output|schema-in-system|core-contract-reminder|subject-key-syntax|utf8-byte-reservation|no-tools|bounded-schema-repair|fixed-pass-manager|inventory-document-key-enum"
    expected = fingerprint_static_contract(developer_prompt=prompt, schema=schema, harness=harness)
    for _estimate, result in results:
        assert (result.metadata.prompt_sha256, result.metadata.schema_sha256, result.metadata.harness_sha256) == (
            expected.prompt_sha256, expected.schema_sha256, expected.harness_sha256,
        )
        assert result.metadata.harness_version == provider._harness_version


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["openai_compatible", "anthropic"])
async def test_wire_answer_with_a_key_outside_the_request_is_a_schema_failure_after_one_repair(name) -> None:
    _provider, seen, results, failure = await _run_wire_provider(
        name, [_answer("a-deed-inside-the-file")], [[_document("the-file", 1, 2)]]
    )

    assert results == [] and failure is not None and failure.code == "PROVIDER_SCHEMA_FAILURE"
    assert len(seen) == 2 and failure.attempt_count == 2
    assert all(_allowed_keys(_sent_schema(name, body)) == ["the-file"] for body in seen)
