"""Non-billable OpenAI connectivity smoke contracts."""

from __future__ import annotations

import asyncio
import uuid
import pytest

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


def test_smoke_requires_an_explicit_organization_without_reading_environment_key(capsys):
    from app.workers.declaration_provider_smoke import main
    assert main([]) == 2
    assert '--organization-id' in capsys.readouterr().err


async def test_worker_tools_reject_unknown_disabled_and_unconfigured_organizations(seed_data, configured_ai, client_a_admin):
    from app.services.ai_settings_service import worker_ai_settings
    with pytest.raises(ValueError):
        await worker_ai_settings(uuid.uuid4())
    configured_ai(seed_data['org_a_id'], import_enabled=False)
    with pytest.raises(ValueError):
        await worker_ai_settings(seed_data['org_a_id'])
    response=await client_a_admin.put(f"/api/organizations/{seed_data['org_a_id']}/ai-settings",json={'api_key':None})
    assert response.status_code==200
    with pytest.raises(ValueError):
        await worker_ai_settings(seed_data['org_a_id'])
