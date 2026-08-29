"""Non-billable OpenAI connectivity smoke contracts."""

from __future__ import annotations

import asyncio

from app.workers.declaration_provider_smoke import verify_openai_models


def test_provider_smoke_uses_fixed_origin_and_only_retrieves_configured_models() -> None:
    constructed: dict[str, object] = {}
    retrieved: list[str] = []

    class Models:
        async def retrieve(self, model: str) -> None:
            retrieved.append(model)

    class Client:
        def __init__(self, **kwargs: object) -> None:
            constructed.update(kwargs)
            self.models = Models()
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    asyncio.run(
        verify_openai_models(
            api_key="unit-test-key",
            primary_model="gpt-5.6-luna",
            escalation_model="gpt-5.4-mini",
            client_factory=Client,
        )
    )

    assert constructed == {
        "api_key": "unit-test-key",
        "base_url": "https://api.openai.com/v1",
        "timeout": 5.0,
        "max_retries": 1,
    }
    assert retrieved == ["gpt-5.6-luna", "gpt-5.4-mini"]
