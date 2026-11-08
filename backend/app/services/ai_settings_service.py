"""Organization AI settings: credentials never enter response or audit payloads."""
import base64
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import SecretStr
from app.core.config import settings
from app.core.ai_models import EVALUATED_MODELS, model_price
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.schemas.ai_settings import AiSettingsOut, AiModelOut, AiModelsOut


def _cipher(organization_id):
    # Domain and tenant separation bind ciphertext to this deployment and tenant.
    material = f'verwalino:organization-ai:v1:{organization_id}:{settings.session_secret}'.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


@dataclass(frozen=True)
class EffectiveAiSettings:
    provider: str = 'openai'
    api_key: SecretStr | None = field(default=None, repr=False)
    model: str | None = None
    import_enabled: bool = False
    qa_enabled: bool = False
    monthly_limit_usd: Decimal | None = None


async def effective_ai_settings(db, organization_id) -> EffectiveAiSettings:
    row = await OrganizationAiSettingsRepository(db, organization_id).get()
    if row is None:
        return EffectiveAiSettings()
    key = None
    if row.encrypted_api_key:
        try:
            key = SecretStr(_cipher(organization_id).decrypt(row.encrypted_api_key.encode()).decode())
        except (InvalidToken, ValueError):
            raise HTTPException(503, 'Der gespeicherte KI-Schlüssel kann nicht entschlüsselt werden. Bitte hinterlegen Sie ihn erneut in den Einstellungen.') from None
    return EffectiveAiSettings(row.provider, key, row.model, row.import_enabled, row.qa_enabled, row.monthly_limit_usd)


async def worker_ai_settings(organization_id):
    """Existing operator smoke/eval tools use the same tenant configuration."""
    from app.core.db import get_worker_tenant_session
    async with get_worker_tenant_session(organization_id) as db:
        effective = await effective_ai_settings(db, organization_id)
    if not effective.import_enabled or not effective.api_key or not effective.model:
        raise ValueError('Organization AI import is not configured')
    return effective


def public_settings(row) -> AiSettingsOut:
    return AiSettingsOut(provider=row.provider if row else 'openai', key_set=bool(row and row.encrypted_api_key),
        model=row.model if row else None, import_enabled=bool(row and row.import_enabled),
        qa_enabled=bool(row and row.qa_enabled), monthly_limit_usd=row.monthly_limit_usd if row else None,
        price_known=bool(row and model_price(row.model)))


async def read_settings(db, organization_id):
    return public_settings(await OrganizationAiSettingsRepository(db, organization_id).get())


def entered_key(value):
    key = value.get_secret_value().strip() if value else ''
    if not key or len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
        raise HTTPException(422, 'Bitte geben Sie einen gültigen API-Schlüssel ohne Leerzeichen ein.')
    return key


async def write_settings(db, organization_id, body):
    repo = OrganizationAiSettingsRepository(db, organization_id)
    await repo.lock_budget()
    previous = await repo.get()
    encrypted = previous.encrypted_api_key if previous else None
    if 'api_key' in body.model_fields_set:
        encrypted = _cipher(organization_id).encrypt(entered_key(body.api_key).encode()).decode() if body.api_key is not None else None
    if (body.import_enabled or body.qa_enabled) and (not encrypted or not body.model):
        raise HTTPException(422, 'Bitte hinterlegen Sie zuerst einen API-Schlüssel und wählen Sie ein Modell.')
    values = body.model_dump(exclude={'api_key'})
    return public_settings(await repo.save({**values, 'encrypted_api_key': encrypted}))


async def discover_models(db, organization_id, body):
    from app.declarations.openai_models import list_models
    if body.api_key is not None:
        key = entered_key(body.api_key)
    else:
        effective = await effective_ai_settings(db, organization_id)
        key = effective.api_key.get_secret_value() if effective.api_key else ''
    if not key:
        raise HTTPException(422, 'Bitte geben Sie zuerst Ihren OpenAI-API-Schlüssel ein.')
    return AiModelsOut(models=[AiModelOut(id=model, evaluated=model in EVALUATED_MODELS,
        price_known=model_price(model) is not None) for model in await list_models(key)])


async def require_organization_ai_budget(db, organization_id, amount, *, model=None):
    repo = OrganizationAiSettingsRepository(db, organization_id)
    await repo.lock_budget()
    row = await repo.get()
    if row is None or row.monthly_limit_usd is None or not model_price(model or row.model):
        return
    used = await repo.monthly_usage()
    if used + amount > row.monthly_limit_usd or used >= row.monthly_limit_usd:
        raise HTTPException(409, {'code': 'ORGANIZATION_AI_BUDGET_EXCEEDED',
            'message': 'Das monatliche KI-Budget Ihrer Organisation ist ausgeschöpft. Bitte passen Sie das Limit in den Einstellungen an.'})
