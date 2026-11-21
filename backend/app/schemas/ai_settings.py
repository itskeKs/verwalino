"""Write-only credential inputs and public AI settings."""
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, SecretStr


# One rule for both model roles: the extraction model and the document reading model.
ModelChoice = Annotated[str | None, Field(min_length=1, max_length=255, pattern=r'^[A-Za-z0-9._:/-]+$')]


class AiSettingsBody(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    provider: Literal['openai', 'openai_compatible', 'anthropic'] = 'openai'
    base_url: str | None = Field(None, max_length=2048)
    api_key: SecretStr | None = None
    model: ModelChoice = None
    document_model: ModelChoice = None
    import_enabled: bool = False
    qa_enabled: bool = False
    request_timeout_seconds: int | None = Field(None, strict=True, ge=1, le=3600)
    monthly_limit_usd: Decimal | None = Field(None, ge=0, max_digits=14, decimal_places=2)
    confirmation: bool = False


class AiModelsBody(BaseModel):
    provider: Literal['openai', 'openai_compatible', 'anthropic'] | None = None
    base_url: str | None = Field(None, max_length=2048)
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    api_key: SecretStr | None = None


class AiConfirmationOut(BaseModel):
    confirmed_by_name: str
    confirmed_at: datetime


class AiSettingsOut(BaseModel):
    request_timeout_seconds: int | None = None
    base_url: str | None = None
    provider: Literal['openai', 'openai_compatible', 'anthropic'] = 'openai'
    key_set: bool
    model: str | None
    document_model: str | None
    import_enabled: bool
    qa_enabled: bool
    monthly_limit_usd: Decimal | None
    price_known: bool
    document_price_known: bool
    confirmation: AiConfirmationOut | None = None
    confirmation_required: bool = False
    # A stored key whose confirmation does not name invoices (none, or one of an earlier text): invoices stay on the host.
    invoice_confirmation_required: bool = False


class AiModelOut(BaseModel):
    id: str
    evaluated: bool
    price_known: bool


class AiModelsOut(BaseModel):
    models: list[AiModelOut]
