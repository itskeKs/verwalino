"""Offline wire contracts for tenant-selected AI providers."""
from decimal import Decimal
import httpx
import pytest
from pydantic import BaseModel, SecretStr
from app.declarations.provider_runtime import ProviderClient, create_extraction_provider, validate_base_url
from app.services.ai_settings_service import EffectiveAiSettings

class Output(BaseModel):
    answer: str

@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai_compatible", "anthropic"])
async def test_selected_provider_receives_only_its_wire_contract_and_normalizes_usage(provider):
    seen = []
    def handle(request):
        import json
        body = json.loads(request.content)
        seen.append((request, body))
        if provider == "anthropic":
            assert request.url.path == "/v1/messages"
            assert request.headers["x-api-key"] == "synthetic-key"
            assert body["output_config"]["format"]["type"] == "json_schema"
            return httpx.Response(200, json={"id":"msg_test", "model":"local-model", "stop_reason":"end_turn", "content":[{"type":"text", "text":'{"answer":"ok"}'}], "usage":{"input_tokens":10,"output_tokens":4,"cache_read_input_tokens":2,"cache_creation_input_tokens":3}})
        assert request.url.path == "/v1/chat/completions"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "store" not in body and "reasoning" not in body
        return httpx.Response(200, json={"id":"chat_test", "model":"local-model", "choices":[{"finish_reason":"stop","message":{"content":'{"answer":"ok"}'}}], "usage":{"prompt_tokens":15,"completion_tokens":4}})
    client = ProviderClient(provider=provider, api_key="synthetic-key", base_url="https://example.com/v1", transport=httpx.MockTransport(handle))
    try:
        result = await client.responses.parse(model="local-model", input=[{"role":"developer","content":[{"type":"input_text","text":"instructions"}]},{"role":"user","content":[{"type":"input_text","text":"synthetic"}]}], text_format=Output, max_output_tokens=100, timeout=5)
        assert result.output_parsed.answer == "ok"
        assert result.usage.input_tokens == 15
        assert len(seen) == 1
    finally:
        await client.close()

@pytest.mark.parametrize("url", ["http://169.254.169.254/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=secret", "ftp://example.com/v1"])
def test_endpoint_validation_rejects_metadata_credentials_and_ambiguous_urls(url):
    with pytest.raises(ValueError):
        validate_base_url(url, hosted=False)

def test_local_endpoint_is_explicit_and_hosted_requires_https():
    assert validate_base_url("http://127.0.0.1:11434/v1", hosted=False) == "http://127.0.0.1:11434/v1"
    with pytest.raises(ValueError):
        validate_base_url("http://127.0.0.1:11434/v1", hosted=True)

@pytest.mark.asyncio
async def test_custom_model_named_like_openai_is_unpriced_and_uses_no_openai_model_options():
    effective = EffectiveAiSettings(provider="openai_compatible", api_key=SecretStr("synthetic-key"), model="gpt-5.6-luna", base_url="http://127.0.0.1:11434/v1")
    provider = create_extraction_provider(effective)
    assert provider._rates[effective.model].input_usd_per_million == Decimal("0")
    assert provider._reasoning_effort(effective.model) is None
    assert provider._uses_explicit_cache(effective.model) is False
    await provider._client.close()


@pytest.mark.asyncio
async def test_refused_or_incomplete_or_invalid_schema_preserves_billed_usage():
    for reason, content in [("refusal", ""), ("max_tokens", "{truncated"), ("end_turn", '{"wrong":"schema"}')]:
        def handle(request):
            return httpx.Response(200, json={"id":"msg_test","model":"claude-haiku-4-5","stop_reason":reason,"content":[{"type":"text","text":content}],"usage":{"input_tokens":12,"output_tokens":3}})
        client = ProviderClient(provider="anthropic", api_key="synthetic-key", transport=httpx.MockTransport(handle))
        try:
            result = await client.responses.parse(model="claude-haiku-4-5", input=[],text_format=Output,max_output_tokens=100,timeout=5)
            assert result.output_parsed is None
            assert result.usage.input_tokens == 12
            assert result.usage.output_tokens == 3
            assert (result.refusal is not None) == (reason == "refusal")
        finally:
            await client.close()

@pytest.mark.asyncio
async def test_redirect_is_rejected_without_forwarding_key_and_model_discovery_paginates():
    from openai import APIStatusError
    seen = []
    def redirect(request):
        seen.append(request)
        return httpx.Response(307, headers={"location":"https://other.example/models"})
    client = ProviderClient(provider="anthropic", api_key="synthetic-key", transport=httpx.MockTransport(redirect))
    try:
        with pytest.raises(APIStatusError):
            await client.list_models()
        assert len(seen) == 1
    finally:
        await client.close()
    def models(request):
        if "after_id" not in request.url.params:
            return httpx.Response(200,json={"data":[{"id":"model-one"}],"has_more":True,"last_id":"model-one"})
        assert request.url.params["after_id"] == "model-one"
        return httpx.Response(200,json={"data":[{"id":"model-two"}],"has_more":False})
    client = ProviderClient(provider="anthropic",api_key="synthetic-key",transport=httpx.MockTransport(models))
    try:
        assert await client.list_models() == ["model-one","model-two"]
    finally:
        await client.close()

@pytest.mark.asyncio
async def test_hosted_connection_is_pinned_after_public_address_validation(monkeypatch):
    import socket
    from app.core.config import settings
    monkeypatch.setattr(settings,"deployment_mode","hosted")
    monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("93.184.216.34",443))])
    seen=[]
    client = ProviderClient(provider="openai_compatible",api_key="synthetic-key",base_url="https://example.com/v1",transport=httpx.MockTransport(lambda request:(seen.append(request) or httpx.Response(200,json={"data":[]}))))
    client._injected_transport=False
    try:
        assert await client.list_models() == []
        assert seen[0].url.host == "93.184.216.34"
        assert seen[0].headers["host"] == "example.com"
        assert seen[0].extensions["sni_hostname"] == "example.com"
        monkeypatch.setattr(socket,"getaddrinfo",lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))])
        from openai import APIConnectionError
        with pytest.raises(APIConnectionError):
            await client.list_models()
        assert len(seen) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_compatible_inventory_reservation_schema_and_metadata_match_actual_wire():
    import json
    from app.declarations.provider import DocumentInput, PageInput
    from app.declarations.schemas import DocumentInventoryOutput
    from app.declarations.observability import fingerprint_static_contract
    effective = EffectiveAiSettings(provider="openai_compatible",api_key=SecretStr("local"),model="local-model",base_url="http://localhost:11434/v1")
    provider = create_extraction_provider(effective)
    original = provider._client
    await original.close()
    seen=[]
    def respond(request):
        body=json.loads(request.content);seen.append(body)
        output=DocumentInventoryOutput(documents=[{'document_key':'doc','document_type':'DECLARATION','title':'Synthetic'}])
        return httpx.Response(200,json={"id":"chat_local","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":output.model_dump_json()}}],"usage":{"prompt_tokens":100,"completion_tokens":30}})
    provider._client=ProviderClient(provider="openai_compatible",api_key="local",base_url=effective.base_url,transport=httpx.MockTransport(respond))
    documents=[DocumentInput("doc",[PageInput("doc",1,"Synthetic text",1.0)])]
    try:
        estimate=provider.estimate_reservation("inventory",documents=documents,max_output_tokens=100)
        result=await provider.inventory(documents,max_output_tokens=100)
        assert result.output.documents[0].document_key == "doc"
        assert estimate.input_tokens >= len(json.dumps(seen[0],ensure_ascii=False).encode())
        assert result.metadata.harness_version == "9"
        from app.declarations.prompts import INVENTORY_PROMPT
        from app.declarations.provider_runtime import _schema_instruction
        expected=fingerprint_static_contract(developer_prompt=INVENTORY_PROMPT+"\n\n"+_schema_instruction(seen[0]["response_format"]["json_schema"]["schema"]),schema=seen[0]["response_format"]["json_schema"]["schema"],harness="declaration-openai_compatible-v9|structured-output|schema-in-system|core-contract-reminder|subject-key-syntax|utf8-byte-reservation|no-tools|bounded-schema-repair|fixed-pass-manager|core-subject-prefix")
        assert result.metadata.schema_sha256 == expected.schema_sha256
        assert result.metadata.harness_sha256 == expected.harness_sha256
        assert len(seen)==1
    finally:
        await provider._client.close()


@pytest.mark.asyncio
async def test_string_shaped_404_is_permanent_sanitized_and_never_retried():
    from openai import APIStatusError
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(404,json={"error":"private provider response and synthetic-secret"})
    client=ProviderClient(provider="openai_compatible",api_key="synthetic-secret",base_url="http://localhost:11434/v1",transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(APIStatusError) as caught:
            await client.responses.parse(model="local-model",input=[],text_format=Output,max_output_tokens=10,timeout=5)
        assert caught.value.status_code == 404
        assert caught.value.body is None
        assert "synthetic-secret" not in str(caught.value)
        assert "private provider response" not in str(caught.value)
        from app.declarations.openai_provider import OpenAiDeclarationProvider
        assert not OpenAiDeclarationProvider._is_retryable_exception(caught.value)
        assert len(seen)==1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_compatible_client_includes_exact_schema_in_system_instructions_and_preserves_safe_validation_diagnostics():
    import json
    seen=[]
    def respond(request):
        body=json.loads(request.content);seen.append(body)
        return httpx.Response(200,json={"id":"chat_test","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":'{"private-secret-field":"private-secret-value"}'}}],"usage":{"prompt_tokens":10,"completion_tokens":5}})
    client=ProviderClient(provider="openai_compatible",api_key="local",base_url="http://localhost:11434/v1",transport=httpx.MockTransport(respond))
    try:
        result=await client.responses.parse(model="local-model",input=[{"role":"user","content":[{"type":"input_text","text":"A harmless request"}]}],text_format=Output,max_output_tokens=100,timeout=5)
        schema=seen[0]["response_format"]["json_schema"]["schema"]
        system=[message for message in seen[0]["messages"] if message["role"]=="system"]
        assert system and any(json.dumps(schema,ensure_ascii=False,sort_keys=True,separators=(",",":")) in block["text"] for message in system for block in message["content"])
        assert result.output_parsed is None
        assert result.usage.input_tokens==10
        assert result._schema_diagnostic
        assert "private-secret-value" not in repr(result._schema_diagnostic)
        assert len(seen)==1
    finally:await client.close()


@pytest.mark.asyncio
async def test_compatible_schema_failure_logs_only_allowlisted_paths_and_keeps_usage(caplog):
    import logging
    from app.declarations.provider import DocumentInput, PageInput, ProviderFailure
    effective=EffectiveAiSettings(provider="openai_compatible",api_key=SecretStr("local"),model="local-model",base_url="http://localhost:11434/v1")
    provider=create_extraction_provider(effective)
    await provider._client.close()
    seen=[]
    def respond(request):
        seen.append(request)
        return httpx.Response(200,json={"id":"chat_test","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":'{"private-secret-field":"private-secret-value"}'}}],"usage":{"prompt_tokens":10,"completion_tokens":5}})
    provider._client=ProviderClient(provider="openai_compatible",api_key="local",base_url=effective.base_url,transport=httpx.MockTransport(respond))
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(ProviderFailure) as caught:
            await provider.inventory([DocumentInput("doc",[PageInput("doc",1,"Synthetic text",1.0)])],max_output_tokens=100)
        assert caught.value.code=="PROVIDER_SCHEMA_FAILURE"
        assert caught.value.usage.input_tokens==20
        assert caught.value.usage.output_tokens==10
        assert len(seen)==2
        assert "DECLARATION_SCHEMA_VALIDATION" in caplog.text
        assert '"stage":"provider"' in caplog.text and '"stage":"schema_repair"' in caplog.text
        assert "private-secret-field" not in caplog.text
        assert "private-secret-value" not in caplog.text
    finally:await provider._client.close()


@pytest.mark.asyncio
async def test_knowledge_reservation_uses_the_actual_strict_wire_schema(monkeypatch):
    import json
    from app.declarations.provider_runtime import create_knowledge_provider
    from app.declarations.knowledge_provider import KnowledgeProviderRequest
    effective=EffectiveAiSettings(provider="openai_compatible",api_key=SecretStr("local"),model="local-model",qa_enabled=True,base_url="http://localhost:11434/v1")
    provider=create_knowledge_provider(effective)
    await provider._client.close()
    envelopes=[]
    original=ProviderClient.envelope
    def record(*args,**kwargs):
        body=original(*args,**kwargs);envelopes.append(body);return body
    monkeypatch.setattr(ProviderClient,"envelope",staticmethod(record))
    wire=[]
    def respond(request):
        wire.append(request)
        return httpx.Response(200,json={"id":"chat_test","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":'{"status":"NOT_DOCUMENTED","answer":"Nicht dokumentiert.","fact_ids":[],"rule_ids":[],"source_ids":[]}'}}],"usage":{"prompt_tokens":10,"completion_tokens":5}})
    provider._client=ProviderClient(provider="openai_compatible",api_key="local",base_url=effective.base_url,transport=httpx.MockTransport(respond))
    request=KnowledgeProviderRequest(normalized_question="Welche Angabe ist dokumentiert?",hits=[],allowed_fact_ids=[],allowed_rule_ids=[],allowed_source_ids=[],schema_version="knowledge-answer-v1",prompt_version="knowledge-qa-v2")
    try:
        estimate=provider.estimate_reservation(request,max_output_tokens=100)
        result=await provider.answer(request,max_output_tokens=100)
        actual=json.loads(wire[0].content)
        assert envelopes[0]["response_format"]==actual["response_format"]
        assert estimate.input_tokens>=len(wire[0].content)
        assert set(actual["response_format"]["json_schema"]["schema"]["required"])=={"status","answer","fact_ids","rule_ids","source_ids"}
        assert result.output.status=="NOT_DOCUMENTED"
    finally:await provider._client.close()


@pytest.mark.asyncio
async def test_compatible_core_wire_clarifies_existing_pairs_and_tagged_object_examples():
    import json
    from pydantic import TypeAdapter
    from app.declarations.provider import DocumentInput, PageInput
    from app.declarations.schemas import FIXED_CORE_FIELDS, NormalizedValue
    effective=EffectiveAiSettings(provider="openai_compatible",api_key=SecretStr("local"),model="local-model",base_url="http://localhost:11434/v1")
    provider=create_extraction_provider(effective);await provider._client.close()
    seen=[]
    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200,json={"id":"chat_test","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":'{"facts":[],"additional_facts":[]}'}}],"usage":{"prompt_tokens":10,"completion_tokens":5}})
    provider._client=ProviderClient(provider="openai_compatible",api_key="local",base_url=effective.base_url,transport=httpx.MockTransport(respond))
    try:
        documents=[DocumentInput("doc",[PageInput("doc",1,"Synthetic unrelated document",1.0)])]
        estimate=provider.estimate_reservation("extract_core",documents=documents,max_output_tokens=100)
        await provider.extract_core(documents,max_output_tokens=100)
        instruction=seen[0]["messages"][0]["content"][-1]["text"]
        assert "normalized_value must be a JSON object" in instruction
        assert "never a scalar, list or null" in instruction
        for prefix,fields in FIXED_CORE_FIELDS.items():
            assert f"{prefix}: {', '.join(sorted(fields))}" in instruction
        assert "subject_key is a technical identifier" in instruction
        assert "A bare allowed prefix remains valid" in instruction
        subject_examples=json.loads(instruction.split("Valid subject_key syntax examples (not source identifiers):\n",1)[1].split("\n",1)[0])
        assert {example.split(":",1)[0] for example in subject_examples}==set(FIXED_CORE_FIELDS)
        assert all(":" in example and example.split(":",1)[1] for example in subject_examples)
        from app.declarations.schemas import FactPayload
        for example in subject_examples:
            prefix=example.split(":",1)[0]
            field="number" if prefix=="unit" else sorted(FIXED_CORE_FIELDS[prefix])[0]
            FactPayload.model_validate({"subject_key":example,"field":field,"raw_value":"Unrelated example","normalized_value":{"kind":"TEXT","value":"Unrelated example"},"confidence":.9,"evidence":[{"document_key":"example-document","pdf_page":1,"quote":"Unrelated example"}]})
        examples_json=instruction.split("Structural examples (not source facts):\n",1)[1].split("\n",1)[0]
        examples=json.loads(examples_json)
        assert {TypeAdapter(NormalizedValue).validate_python(item).kind for item in examples}=={"TEXT","INTEGER","DECIMAL","MEA"}
        assert estimate.input_tokens>=len(json.dumps(seen[0],ensure_ascii=False).encode())
        assert '450' not in examples_json and '1000' not in examples_json
    finally:await provider._client.close()


@pytest.mark.asyncio
async def test_scalar_normalized_value_logs_dict_type_without_private_values_or_paths(caplog):
    import json,logging
    from app.declarations.provider import DocumentInput, PageInput, ProviderFailure
    effective=EffectiveAiSettings(provider="openai_compatible",api_key=SecretStr("local"),model="local-model",base_url="http://localhost:11434/v1")
    provider=create_extraction_provider(effective);await provider._client.close()
    base={"subject_key":"unit:example","field":"number","raw_value":"Synthetic","normalized_value":"private-secret-value","confidence":.9,"evidence":[{"document_key":"doc","pdf_page":1,"quote":"Synthetic"}],"issues":[]}
    second={**base,"normalized_value":{"kind":"TEXT","value":"Example","private-secret-field":"private-secret-value"}}
    def respond(request):
        return httpx.Response(200,json={"id":"chat_test","model":"local-model","choices":[{"finish_reason":"stop","message":{"content":json.dumps({"facts":[base,second],"additional_facts":[]})}}],"usage":{"prompt_tokens":10,"completion_tokens":5}})
    provider._client=ProviderClient(provider="openai_compatible",api_key="local",base_url=effective.base_url,transport=httpx.MockTransport(respond))
    try:
        with caplog.at_level(logging.WARNING),pytest.raises(ProviderFailure) as caught:
            await provider.extract_core([DocumentInput("doc",[PageInput("doc",1,"Synthetic",1.0)])],max_output_tokens=100)
        assert caught.value.code=="PROVIDER_SCHEMA_FAILURE"
        assert '"code":"dict_type"' in caplog.text
        assert '"location_truncated":true' in caplog.text
        assert "private-secret-value" not in caplog.text and "private-secret-field" not in caplog.text
        assert caught.value.usage.input_tokens==20 and caught.value.usage.output_tokens==10
    finally:await provider._client.close()



def test_existing_fact_validator_accepts_bare_prefix_but_rejects_unknown_prefix():
    from pydantic import ValidationError
    from app.declarations.schemas import FactPayload
    fact={"subject_key":"unit","field":"number","raw_value":"Unrelated example","normalized_value":{"kind":"TEXT","value":"Unrelated example"},"confidence":.9,"evidence":[{"document_key":"example-document","pdf_page":1,"quote":"Unrelated example"}]}
    assert FactPayload.model_validate(fact).subject_key=="unit"
    with pytest.raises(ValidationError) as caught:
        FactPayload.model_validate({**fact,"subject_key":"unrecognized-example:identifier"})
    assert caught.value.errors(include_input=False)[0]["type"]=="fact_core_field_unsupported"


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected", [("openai", None), ("openai_compatible", 180), ("anthropic", None)])
async def test_endpoint_timeout_defaults_and_explicit_override(name, expected):
    from app.core.config import settings
    from app.declarations.provider_runtime import create_knowledge_provider
    base = dict(provider=name, api_key=SecretStr("synthetic-key"), model="gpt-5.6-luna" if name == "openai" else "local-model", qa_enabled=True,
                base_url="http://localhost:11434/v1" if name == "openai_compatible" else None)
    for override in (None, 240):
        effective = EffectiveAiSettings(**base, request_timeout_seconds=override)
        extraction = create_extraction_provider(effective)
        knowledge = create_knowledge_provider(effective)
        try:
            assert extraction._timeout_seconds == (override or expected or settings.declaration_provider_timeout_seconds)
            assert knowledge._timeout_seconds == (override or expected or settings.declaration_qa_timeout_seconds)
            assert extraction._harness_version == ("9" if name == "openai_compatible" else settings.declaration_harness_version)
        finally:
            await extraction._client.close()
            await knowledge._client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,expected", [("openai", "3"), ("openai_compatible", "9"), ("anthropic", "3")])
async def test_persisted_run_header_uses_selected_provider_harness(monkeypatch, name, expected):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4
    from app.core.config import Settings
    from app.declarations.pipeline import DeclarationPipeline
    import app.declarations.pipeline as module
    import app.services.ai_settings_service as settings_service
    effective = EffectiveAiSettings(provider=name,api_key=SecretStr("synthetic-key"),model="gpt-5.6-luna" if name=='openai' else 'local-model',base_url='http://localhost:11434/v1' if name=='openai_compatible' else None)
    provider = create_extraction_provider(effective,configured_settings=Settings(declaration_harness_version='3'))
    session = SimpleNamespace(add=Mock(),flush=AsyncMock())
    @asynccontextmanager
    async def tenant_session(org):
        yield session
    monkeypatch.setattr(module,'DeclarationImportRepository',lambda *args:SimpleNamespace(lock_current_lease=AsyncMock(return_value=SimpleNamespace(max_cost_usd=Decimal('1')))))
    monkeypatch.setattr(settings_service,'require_organization_ai_budget',AsyncMock())
    pipeline = DeclarationPipeline(provider,configured=Settings(declaration_harness_version='3'),tenant_session_factory=tenant_session)
    try:
        await pipeline._start_run(SimpleNamespace(organization_id=uuid4(),import_id=uuid4()))
        assert session.add.call_args.args[0].harness_version == expected == provider._harness_version
    finally:
        await provider._client.close()
