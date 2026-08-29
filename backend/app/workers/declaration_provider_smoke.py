"""Bounded, non-billable OpenAI reachability check for the declaration worker.

This only resolves the two configured model IDs through OpenAI's Models API.
It never sends document data, prompts, Responses API requests, or tools.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Callable
from typing import Any

from openai import AsyncOpenAI

from app.core.config import settings


_OPENAI_API_BASE_URL = "https://api.openai.com/v1"
_SMOKE_TIMEOUT_SECONDS = 5.0
_SMOKE_MAX_RETRIES = 1


async def verify_openai_models(
    *,
    api_key: str,
    primary_model: str,
    escalation_model: str,
    client_factory: Callable[..., Any] = AsyncOpenAI,
) -> None:
    """Verify only the configured Models endpoints via the fixed API origin."""
    client = client_factory(
        api_key=api_key,
        base_url=_OPENAI_API_BASE_URL,
        timeout=_SMOKE_TIMEOUT_SECONDS,
        max_retries=_SMOKE_MAX_RETRIES,
    )
    try:
        await client.models.retrieve(primary_model)
        await client.models.retrieve(escalation_model)
    finally:
        close = getattr(client, "close", None)
        if close is not None:
            result = close()
            if inspect.isawaitable(result):
                await result


def main() -> int:
    api_key = settings.openai_api_key.get_secret_value().strip() if settings.openai_api_key else ""
    if not api_key:
        print("OpenAI provider connectivity smoke requires a configured API key", file=sys.stderr)
        return 2
    try:
        asyncio.run(
            verify_openai_models(
                api_key=api_key,
                primary_model=settings.declaration_primary_model,
                escalation_model=settings.declaration_escalation_model,
            )
        )
    except Exception:
        # Never print a response body, model metadata, or a request URL here.
        print("OpenAI provider connectivity smoke failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
