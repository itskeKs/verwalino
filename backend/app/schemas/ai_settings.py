"""Write-only credential inputs and public AI settings."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AiSettingsBody(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    provider: Literal['openai', 'openai_compatible', 'anthropic'] = 'openai'
    base_url: str | None = Field(None, max_length=2048)
    api_key: SecretStr | None = None
    model: str | None = Field(None, min_length=1, max_length=255, pattern=r'^[A-Za-z0-9._:/-]+$')
    import_enabled: bool = False
    qa_enabled: bool = False
    request_timeout_seconds: int | None = Field(None, strict=True, ge=1, le=3600)
    monthly_limit_usd: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)


class AiModelsBody(BaseModel):
    provider: Literal['openai', 'openai_compatible', 'anthropic'] | None = None
    base_url: str | None = Field(None, max_length=2048)
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_key: SecretStr | None = None


class AiSettingsOut(BaseModel):
    request_timeout_seconds: int | None = None
    base_url: str | None = None
    provider: Literal['openai', 'openai_compatible', 'anthropic'] = 'openai'
    key_set: bool
    model: str | None
    import_enabled: bool
    qa_enabled: bool
    monthly_limit_usd: Decimal | None
    price_known: bool


class AiModelOut(BaseModel):
    id: str
    evaluated: bool
    price_known: bool


class AiModelsOut(BaseModel):
    models: list[AiModelOut]
