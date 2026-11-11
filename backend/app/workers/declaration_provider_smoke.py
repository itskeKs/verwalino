"""Bounded, non-billable provider reachability check for the declaration worker.

This only resolves the two configured model IDs through OpenAI's Models API.
It never sends document data, prompts, Responses API requests, or tools.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import argparse
import uuid
from collections.abc import Callable
from typing import Any

from app.declarations.openai_models import create_client


_OPENAI_API_BASE_URL = "https://api.openai.com/v1"
_SMOKE_TIMEOUT_SECONDS = 5.0
_SMOKE_MAX_RETRIES = 1


async def verify_openai_models(
    *,
    api_key: str,
    primary_model: str,
    escalation_model: str,
    client_factory: Callable[..., Any] = create_client,
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


async def _verify_organization(organization_id):
    from app.services.ai_settings_service import worker_ai_settings
    effective = await worker_ai_settings(organization_id)
    from app.declarations.openai_models import list_models
    models = await list_models(effective.api_key.get_secret_value(), provider=effective.provider, base_url=effective.base_url)
    if effective.model not in models:
        raise ValueError('Configured model is not listed by the selected provider')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--organization-id', type=uuid.UUID)
    args = parser.parse_args(argv)
    if args.organization_id is None:
        print("Provider connectivity smoke requires --organization-id and configured organization AI settings", file=sys.stderr)
        return 2
    try:
        asyncio.run(_verify_organization(args.organization_id))
    except Exception:
        # Never print a response body, model metadata, or a request URL here.
        print("Provider connectivity smoke failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
