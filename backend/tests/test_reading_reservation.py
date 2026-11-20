"""Image input of the reading stage is reserved from the page's pixel dimensions (issue #499, ADR 0017).

A scanned A4 page at the worker's resolution must pass every transport's reservation; the base64 length of the PNG
never decides. No test makes a provider call.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os

import pytest
from PIL import Image
from pydantic import SecretStr

from app.core.config import settings
from app.declarations.openai_provider import OpenAiDeclarationProvider, png_dimensions, reserved_image_tokens
from app.declarations.preflight import READING_MAX_IMAGE_PIXELS, PdfPreflight
from app.declarations.provider import PageImage, ProviderFailure
from app.declarations.provider_runtime import AnthropicDeclarationProvider, CompatibleDeclarationProvider, _ByteEncoder
from app.services.ai_settings_service import EffectiveAiSettings
from tests.declaration_fixtures import make_text_pdf

pytestmark = pytest.mark.no_db

A4_AT_180_DPI = (1_489, 2_105)  # 595.28 x 841.89 pt rendered at the worker's declaration_ocr_dpi


def _png(width: int, height: int, *, noise: bool = False) -> bytes:
    image = Image.frombytes("L", (width, height), os.urandom(width * height)) if noise else Image.new("L", (width, height), 255)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _page(png: bytes) -> PageImage:
    return PageImage(document_key="doc", pdf_page=1, media_type="image/png", data=png)


def test_the_documented_formulas_reserve_an_a4_page_at_the_worker_dpi() -> None:
    width, height = A4_AT_180_DPI
    # Anthropic: ceil(w/28) x ceil(h/28) visual tokens, capped at 4,784 (the high-resolution tier).
    assert reserved_image_tokens("anthropic", "claude-haiku-4-5", width, height) == 54 * 76
    assert reserved_image_tokens("anthropic", "claude-haiku-4-5", 2_000, 2_000) == 4_784
    # OpenAI patch models with a documented high-detail budget: the verified 2,500 patches x 1.2 (+1 for rounding).
    assert reserved_image_tokens("openai", "gpt-5.4-mini", width, height) == 3_001
    assert reserved_image_tokens("openai", "gpt-5.6-luna", 20_000, 20_000) == 3_001
    # Any other OpenAI model: the worst documented family. 1,489 x 2,105 fits 2,048 x 2,048 as 1,449 x 2,048, the
    # shortest side is scaled to 768 (768 x 1,086): 2 x 3 tiles at 2,833 + 5,667 per tile; the patch family
    # (47 x 66 patches x 2.46) is smaller.
    assert reserved_image_tokens("openai", "some-new-vision-model", width, height) == 2_833 + 6 * 5_667
    # Compatible endpoints publish no formula: one token per 14 x 14 pixel patch.
    assert reserved_image_tokens("openai_compatible", "local-vl", width, height) == 107 * 151


def test_the_compatible_bound_at_the_render_ceiling_is_far_below_the_import_budget() -> None:
    side = int(READING_MAX_IMAGE_PIXELS ** 0.5)
    tokens = reserved_image_tokens("openai_compatible", "local-vl", side, side)
    assert tokens <= 21_000
    assert 2 * tokens * 10 < settings.declaration_max_input_tokens, "two attempts per page leave room for a long document"


def test_png_dimensions_come_from_the_header_and_an_invalid_image_is_refused() -> None:
    data_url = "data:image/png;base64," + base64.b64encode(_png(37, 11)).decode()
    assert png_dimensions(data_url) == (37, 11)
    with pytest.raises(ProviderFailure) as refused:
        png_dimensions("data:image/png;base64," + base64.b64encode(b"not a png at all, just text").decode())
    assert refused.value.code == "OPENAI_IMAGE_INPUT_INVALID"


def _transports() -> dict[str, object]:
    def effective(provider: str, model: str, base_url: str | None = None) -> EffectiveAiSettings:
        return EffectiveAiSettings(provider=provider, api_key=SecretStr("synthetic-key"), model=model, base_url=base_url)

    return {
        "openai-known-model": OpenAiDeclarationProvider(
            client=object(), primary_model="gpt-5.4-mini", escalation_model="gpt-5.4-mini", token_encoder=_ByteEncoder()),
        "openai-unknown-model": OpenAiDeclarationProvider(
            client=object(), primary_model="some-new-vision-model", escalation_model="some-new-vision-model",
            token_encoder=_ByteEncoder()),
        "compatible": CompatibleDeclarationProvider(effective("openai_compatible", "local-vl", "http://localhost:11434/v1")),
        "anthropic": AnthropicDeclarationProvider(effective("anthropic", "claude-haiku-4-5")),
    }


@pytest.mark.asyncio
async def test_a_page_image_at_the_pixel_bound_passes_every_transports_reservation() -> None:
    side = int(READING_MAX_IMAGE_PIXELS ** 0.5)
    noisy, flat = _page(_png(side, side, noise=True)), _page(_png(side, side))
    assert len(base64.b64encode(noisy.data)) > settings.declaration_max_input_tokens, "the old base64 count would refuse it"
    transports = _transports()
    try:
        for name, provider in transports.items():
            model = provider._primary_model
            estimate = provider.estimate_reservation("read_page", max_output_tokens=12_000, page=noisy)
            assert estimate.input_tokens <= settings.declaration_max_input_tokens, name
            provider._validate_reservation_budget(model, estimate, 12_000)  # the transport's own gate raises otherwise
            # Only the dimensions decide: the same size of a flat image reserves the same.
            assert provider.estimate_reservation("read_page", max_output_tokens=12_000, page=flat) == estimate, name
    finally:
        for provider in transports.values():
            close = getattr(provider._client, "close", None)
            if close is not None:
                await close()


@pytest.mark.asyncio
async def test_a_rendered_a4_scan_passes_every_transports_reservation_and_is_not_scaled() -> None:
    data = make_text_pdf("kurz")  # a page without a text layer worth reading: marked for reading
    preflight = PdfPreflight()
    budget = preflight.new_import_budget()
    result = preflight.run_bytes(data, expected_sha256=hashlib.sha256(data).hexdigest(),
                                 expected_size=len(data), budget=budget)
    png = preflight.render_page_for_reading(data, result.pages[0], budget=budget)
    assert Image.open(io.BytesIO(png)).size == A4_AT_180_DPI
    transports = _transports()
    try:
        for name, provider in transports.items():
            estimate = provider.estimate_reservation("read_page", max_output_tokens=12_000, page=_page(png))
            provider._validate_reservation_budget(provider._primary_model, estimate, 12_000)
            assert 0 < estimate.input_tokens <= settings.declaration_max_input_tokens, name
    finally:
        for provider in transports.values():
            close = getattr(provider._client, "close", None)
            if close is not None:
                await close()


@pytest.mark.asyncio
async def test_an_oversized_render_is_scaled_to_the_ceiling_before_it_is_reserved() -> None:
    from reportlab.lib.pagesizes import A3
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    sheet = canvas.Canvas(buffer, pagesize=A3)  # 842 x 1191 pt: 2,105 x 2,976 pixels at 180 dpi, 6.3 million
    sheet.showPage()
    sheet.save()
    data = buffer.getvalue()
    preflight = PdfPreflight()
    budget = preflight.new_import_budget()
    result = preflight.run_bytes(data, expected_sha256=hashlib.sha256(data).hexdigest(),
                                 expected_size=len(data), budget=budget)

    png = preflight.render_page_for_reading(data, result.pages[0], budget=budget)

    width, height = Image.open(io.BytesIO(png)).size
    assert width * height <= READING_MAX_IMAGE_PIXELS
    assert budget.rendered_pixels > READING_MAX_IMAGE_PIXELS, "the render was larger; it is scaled, not refused"
    transports = _transports()
    try:
        for name, provider in transports.items():
            estimate = provider.estimate_reservation("read_page", max_output_tokens=12_000, page=_page(png))
            provider._validate_reservation_budget(provider._primary_model, estimate, 12_000)
    finally:
        for provider in transports.values():
            close = getattr(provider._client, "close", None)
            if close is not None:
                await close()


def test_a_non_png_page_is_refused_before_any_estimate() -> None:
    provider = OpenAiDeclarationProvider(client=object(), primary_model="gpt-5.4-mini", token_encoder=_ByteEncoder())
    page = PageImage(document_key="doc", pdf_page=1, media_type="image/jpeg", data=b"\xff\xd8")
    with pytest.raises(ProviderFailure) as refused:
        provider.estimate_reservation("read_page", max_output_tokens=12_000, page=page)
    assert refused.value.code == "OPENAI_IMAGE_INPUT_INVALID"
