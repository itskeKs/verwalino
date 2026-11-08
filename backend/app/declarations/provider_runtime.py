"""Tenant-selected, tool-free transports behind the existing typed providers.

No redirects, environment proxies, response logging, retries or provider fallback.
Custom DNS results are validated and pinned to the connection (including TLS SNI).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from app.core.config import settings
from app.core.ai_models import model_price

ORIGINS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1"}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_MODEL_PAGES = 20
MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,255}$")


def _valid_address(address: str, hosted: bool) -> bool:
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if not hosted and ip.is_loopback:
        return True
    return not (ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved) and (not hosted or ip.is_global)


def validate_base_url(value: str, *, hosted: bool) -> str:
    try:
        url = urlsplit(value)
        if (url.scheme not in ({"https"} if hosted else {"http", "https"}) or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value)
                or "%" in url.netloc or url.port == 0):
            raise ValueError
        try:
            address = ipaddress.ip_address(url.hostname)
        except ValueError:
            address = None
        if address and not _valid_address(str(address), hosted):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Invalid AI endpoint") from None
    return value.rstrip("/")


def _anthropic_schema(value):
    """Remove unsupported API constraints; original Pydantic output still validates."""
    if isinstance(value, list):
        return [_anthropic_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    unsupported = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "maxItems", "uniqueItems", "format"}
    return {key:_anthropic_schema(item) for key,item in value.items()
            if key not in unsupported and (key != "minItems" or item in (0, 1))}


def _wire_schema(output_type):
    from openai.lib._pydantic import to_strict_json_schema
    return to_strict_json_schema(output_type)


def _schema_instruction(schema):
    instruction = ("Return only one JSON object that matches this exact JSON Schema. "
                   "Do not include Markdown fences, commentary or additional fields.\n"
                   + json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if "FactPayload" not in schema.get("$defs", {}):
        return instruction
    from app.declarations.schemas import FIXED_CORE_FIELDS
    pairs = "\n".join(f"{prefix}: {', '.join(sorted(fields))}" for prefix, fields in FIXED_CORE_FIELDS.items())
    subject_examples = [f"{prefix}:example-id" for prefix in FIXED_CORE_FIELDS]
    examples = [
        {"kind":"TEXT", "value":"Unrelated structural example"},
        {"kind":"INTEGER", "value":7},
        {"kind":"DECIMAL", "value":"12.75", "unit":"example-unit"},
        {"kind":"MEA", "numerator":1, "denominator":7},
    ]
    return (instruction + "\n\nFixed-core structural reminder: pair field with the exact subject_key prefix "
            "before its first colon. Use only these existing pairs:\n" + pairs
            + "\nsubject_key is a technical identifier, not the human unit designation. "
            "Keep the exact case-sensitive prefix from the table. A bare allowed prefix remains valid. "
            "When an identifier is needed, write <prefix>:<stable-identifier> with a literal colon. "
            "Do not attach an identifier directly to the prefix or replace the prefix with a designation. "
            "Reuse the same key for the same entity across passes; distinguish different entities.\n"
            "Valid subject_key syntax examples (not source identifiers):\n"
            + json.dumps(subject_examples, ensure_ascii=False, separators=(",", ":"))
            + "\nChoose identifiers for the actual source subjects; do not copy example identifiers."
            + "\nEvery normalized_value must be a JSON object with a case-sensitive kind, "
            "never a scalar, list or null. Its other fields must match that kind's schema.\n"
            "Structural examples (not source facts):\n"
            + json.dumps(examples, ensure_ascii=False, separators=(",", ":"))
            + "\nThese examples demonstrate structure only. Extract values from the supplied source; "
            "do not emit the example values or invent facts.")


class ProviderClient:
    """Small Responses-shaped facade; each call uses one configured API only."""
    def __init__(self, *, provider, api_key, base_url=None, transport=None):
        self.provider = provider
        self.base_url = validate_base_url(base_url or ORIGINS[provider], hosted=settings.deployment_mode == "hosted")
        self._key = api_key
        self._injected_transport = transport is not None
        self._http = httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False)
        self.responses = self

    async def close(self):
        await self._http.aclose()

    async def _request(self, method, path, *, body=None, timeout=15, params=None):
        url = httpx.URL(self.base_url + path)
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01"} if self.provider == "anthropic" else {"Authorization": "Bearer " + self._key}
        extensions = {}
        original = url
        try:
            if not self._injected_transport:
                addresses = await asyncio.wait_for(asyncio.to_thread(socket.getaddrinfo, url.host, url.port or (443 if url.scheme == "https" else 80), type=socket.SOCK_STREAM), timeout=timeout)
                ips = list(dict.fromkeys(item[4][0] for item in addresses))
                if not ips or any(not _valid_address(ip, settings.deployment_mode == "hosted") for ip in ips):
                    raise ValueError("Unsafe endpoint address")
                headers["Host"] = url.netloc.decode("ascii")
                extensions["sni_hostname"] = url.host
                url = url.copy_with(host=ips[0])
            async with self._http.stream(method, url, json=body, params=params, headers=headers, timeout=timeout, extensions=extensions) as response:
                if response.status_code >= 300:
                    # Discard response bodies: SDK exceptions must never contain credentials/documents.
                    safe_response = httpx.Response(response.status_code, request=httpx.Request(method, original))
                    error_type = RateLimitError if response.status_code == 429 else APIStatusError
                    raise error_type("Provider request failed", response=safe_response, body=None)
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_RESPONSE_BYTES:
                        raise ValueError("Provider response exceeds limit")
                return json.loads(content)
        except (httpx.TimeoutException, TimeoutError):
            raise APITimeoutError(request=httpx.Request(method, original)) from None
        except APIStatusError:
            raise
        except Exception:
            raise APIConnectionError(message="Provider connection failed", request=httpx.Request(method, original)) from None

    @staticmethod
    def envelope(provider, *, model, input, schema, max_output_tokens):
        messages = []
        system = []
        for message in input:
            blocks = []
            for block in message["content"]:
                if block["type"] == "input_text":
                    blocks.append({"type": "text", "text": block["text"]})
                elif block["type"] == "input_image":
                    data_url = block["image_url"]
                    if provider == "anthropic":
                        blocks.append({"type":"image", "source":{"type":"base64", "media_type":"image/png", "data":data_url.split(",", 1)[1] if "," in data_url else data_url}})
                    else:
                        blocks.append({"type":"image_url", "image_url":{"url":data_url}})
            role = message["role"]
            if provider == "anthropic" and role in {"developer", "system"}:
                system.extend(blocks)
            else:
                messages.append({"role":"system" if role == "developer" else role, "content":blocks})
        if provider == "anthropic":
            schema = _anthropic_schema(schema)
            return {"model":model,"system":system,"messages":messages,"max_tokens":max_output_tokens,"output_config":{"format":{"type":"json_schema","schema":schema}}}
        # Some compatible servers accept response_format without enforcing it.
        # The exact same static schema is therefore also visible to the model.
        instruction = {"type":"text", "text":_schema_instruction(schema)}
        if messages and messages[0]["role"] == "system":
            messages[0]["content"].append(instruction)
        else:
            messages.insert(0, {"role":"system", "content":[instruction]})
        return {"model":model,"messages":messages,"max_tokens":max_output_tokens,"response_format":{"type":"json_schema","json_schema":{"name":"response","strict":True,"schema":schema}}}

    async def parse(self, *, model, input, text_format, max_output_tokens, timeout, **unused):
        schema = _wire_schema(text_format)
        body = self.envelope(self.provider, model=model, input=input, schema=schema, max_output_tokens=max_output_tokens)
        raw = await self._request("POST", "/messages" if self.provider == "anthropic" else "/chat/completions", body=body, timeout=timeout)
        usage_raw = raw.get("usage")
        usage = None
        if isinstance(usage_raw, dict):
            if self.provider == "anthropic":
                uncached = usage_raw.get("input_tokens")
                read, write = usage_raw.get("cache_read_input_tokens", 0), usage_raw.get("cache_creation_input_tokens", 0)
                total = uncached + read + write if all(type(x) is int and x >= 0 for x in (uncached, read, write)) else None
                usage = SimpleNamespace(input_tokens=total, output_tokens=usage_raw.get("output_tokens"), input_tokens_details=SimpleNamespace(cached_tokens=read, cache_write_tokens=write))
            else:
                details = usage_raw.get("prompt_tokens_details") or {}
                usage = SimpleNamespace(input_tokens=usage_raw.get("prompt_tokens"), output_tokens=usage_raw.get("completion_tokens"), input_tokens_details=SimpleNamespace(cached_tokens=details.get("cached_tokens", 0), cache_write_tokens=0))
        if self.provider == "anthropic":
            reason = raw.get("stop_reason")
            text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
            refused, complete = reason == "refusal", reason == "end_turn"
        else:
            choices = raw.get("choices") or [{}]
            choice = choices[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            refused, complete = bool(message.get("refusal")) or choice.get("finish_reason") == "content_filter", choice.get("finish_reason") == "stop"
        # Preserve metering on malformed/refused output. Existing bounded repair owns validation.
        diagnostic = None
        try:
            parsed = text_format.model_validate_json(text) if complete and not refused else None
        except ValidationError as error:
            diagnostic = ("ValidationError", [{"type":entry["type"], "loc":entry["loc"]}
                for entry in error.errors(include_url=False, include_context=False, include_input=False)])
            parsed = None
        except (ValueError, TypeError):
            parsed = None
        return SimpleNamespace(_schema_diagnostic=diagnostic, output_parsed=parsed, usage=usage, status="completed" if complete or refused else "incomplete", refusal="refused" if refused else None, model=raw.get("model"), _request_id=raw.get("id"))

    async def list_models(self):
        found = set()
        after = None
        for _ in range(MAX_MODEL_PAGES):
            raw = await self._request("GET", "/models", params={"after_id":after, "limit":100} if self.provider == "anthropic" and after else {"limit":100} if self.provider == "anthropic" else None)
            for item in raw.get("data", []):
                value = item.get("id")
                if isinstance(value, str) and MODEL_ID.fullmatch(value):
                    found.add(value)
            if self.provider != "anthropic" or not raw.get("has_more"):
                return sorted(found)
            next_id = raw.get("last_id")
            if not isinstance(next_id, str) or not MODEL_ID.fullmatch(next_id) or next_id == after:
                raise ValueError("Invalid model pagination")
            after = next_id
        raise ValueError("Model list exceeds limit")


class _ByteEncoder:
    @staticmethod
    def encode(value):
        return list(value.encode("utf-8"))


def create_extraction_provider(effective, *, configured_settings=None):
    from app.declarations.openai_provider import OpenAiDeclarationProvider
    if effective.provider == "openai":
        return OpenAiDeclarationProvider(api_key=effective.api_key.get_secret_value(), primary_model=effective.model, escalation_model=effective.model, configured_settings=configured_settings)
    provider_type = AnthropicDeclarationProvider if effective.provider == "anthropic" else CompatibleDeclarationProvider
    return provider_type(effective, configured_settings=configured_settings)


from app.declarations.openai_provider import OpenAiDeclarationProvider, _Rates


class CompatibleDeclarationProvider(OpenAiDeclarationProvider):
    """Same deterministic passes/repair/accounting, different bounded wire adapter."""
    def __init__(self, effective, *, configured_settings=None):
        self._provider_name = effective.provider
        self._price_provider = effective.provider
        super().__init__(client=ProviderClient(provider=effective.provider, api_key=effective.api_key.get_secret_value(), base_url=effective.base_url), primary_model=effective.model, escalation_model=effective.model, token_encoder=_ByteEncoder(), configured_settings=configured_settings)

    def _configured_rates(self, configured):
        price = model_price(self._primary_model, self._price_provider)
        return {self._primary_model:_Rates(price.input, price.output, price.cached, price.cache_write_multiplier)
                if price else _Rates(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1.25") if self._price_provider == "anthropic" else None)}

    def _validate_configured_rates(self):
        pass

    def _uses_explicit_cache(self, model):
        return False

    def _reasoning_effort(self, model):
        return None

    def _fingerprints(self, developer_prompt, output_type):
        from app.declarations.observability import fingerprint_static_contract
        schema = self._sdk_text_format(output_type)["schema"]
        if self._provider_name == "anthropic":
            schema = _anthropic_schema(schema)
        if self._provider_name == "openai_compatible":
            developer_prompt += "\n\n" + _schema_instruction(schema)
            harness = "declaration-openai_compatible-v7|structured-output|schema-in-system|core-contract-reminder|subject-key-syntax|utf8-byte-reservation|no-tools|bounded-schema-repair|fixed-pass-manager"
        else:
            harness = f"declaration-{self._provider_name}-v4|structured-output|utf8-byte-reservation|no-tools|bounded-schema-repair|fixed-pass-manager"
        return fingerprint_static_contract(developer_prompt=developer_prompt, schema=schema, harness=harness)

    def _estimate_input_tokens(self, pass_id, model, developer_prompt, input_payload, output_type, max_output_tokens):
        body = ProviderClient.envelope(self._provider_name, model=model, input=input_payload, schema=self._sdk_text_format(output_type)["schema"], max_output_tokens=max_output_tokens)
        # UTF-8 bytes conservatively cover tokenizers and base64 vision payloads.
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

class AnthropicDeclarationProvider(CompatibleDeclarationProvider):
    """Distinct provenance for Anthropic's official API and scoped prices."""



def create_knowledge_provider(effective):
    if not effective.qa_enabled or not effective.api_key or not effective.model:
        return None
    if effective.provider == "openai":
        from app.declarations.openai_knowledge_provider import create_knowledge_provider as create_openai
        return create_openai(effective)
    return CompatibleKnowledgeProvider(effective)


from app.declarations.openai_knowledge_provider import OpenAIKnowledgeProvider


class CompatibleKnowledgeProvider(OpenAIKnowledgeProvider):
    def __init__(self, effective):
        self._price_provider = effective.provider
        price = model_price(effective.model, effective.provider)
        super().__init__(model=effective.model, client=ProviderClient(provider=effective.provider, api_key=effective.api_key.get_secret_value(), base_url=effective.base_url), input_usd_per_million=price.input if price else Decimal("0"), output_usd_per_million=price.output if price else Decimal("0"))

    def _cache_request_options(self):
        return {}

    def _reserved_input_tokens(self, user_payload):
        from app.declarations.knowledge_prompts import KNOWLEDGE_QA_DEVELOPER_PROMPT
        from app.declarations.knowledge_provider import ProviderKnowledgeAnswer
        body = ProviderClient.envelope(self._price_provider, model=self._model, input=[
            {"role":"developer", "content":[{"type":"input_text", "text":KNOWLEDGE_QA_DEVELOPER_PROMPT}]},
            {"role":"user", "content":[{"type":"input_text", "text":user_payload}]},
        ], schema=_wire_schema(ProviderKnowledgeAnswer), max_output_tokens=self._max_output_tokens)
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
