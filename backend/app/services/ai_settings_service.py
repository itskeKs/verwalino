"""Organization AI settings: credentials never enter response or audit payloads."""
import base64
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy import select
from app.core.config import settings
from app.core.ai_models import EVALUATED_MODELS, model_price
from app.models.audit_event import AuditEvent
from app.models.organization_ai_confirmation import OrganizationAiConfirmation
from app.models.user import User
from app.repositories.audit_repo import AuditRepository
from app.repositories.organization_ai_settings_repo import OrganizationAiSettingsRepository
from app.schemas.ai_settings import AiSettingsOut, AiConfirmationOut, AiModelOut, AiModelsOut

# Version of the German provider-confirmation text the organization agrees to;
# the copy lives in the frontend, the reviewed wording follows #26.
AI_PROVIDER_CONFIRMATION_VERSION = '1'

CONFIRMATION_REQUIRED_MESSAGE = ('Bitte bestätigen Sie vor dem Speichern, dass Ihre Organisation '
    'den Anbieter selbst ausgewählt und beauftragt hat und den Betreiber anweist, '
    'Dokumente und extrahierte Texte an diesen Anbieter zu übermitteln.')


def _cipher(organization_id):
    # Domain and tenant separation bind ciphertext to this deployment and tenant.
    material = f'verwalino:organization-ai:v1:{organization_id}:{settings.session_secret}'.encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


class AiKeyUnreadableError(HTTPException):
    """Unreadable stored key; API callers keep the 503, the worker classifies by type."""

    def __init__(self) -> None:
        super().__init__(503, 'Der gespeicherte KI-Schlüssel kann nicht entschlüsselt werden. Bitte hinterlegen Sie ihn erneut in den Einstellungen.')


@dataclass(frozen=True)
class EffectiveAiSettings:
    provider: str = 'openai'
    api_key: SecretStr | None = field(default=None, repr=False)
    model: str | None = None
    import_enabled: bool = False
    qa_enabled: bool = False
    monthly_limit_usd: Decimal | None = None
    base_url: str | None = None
    request_timeout_seconds: int | None = None


def _confirmation_match(provider, base_url):
    return (OrganizationAiConfirmation.provider == provider,
        OrganizationAiConfirmation.base_url.isnot_distinct_from(base_url))


async def matching_confirmation(db, organization_id, provider, base_url):
    """The newest recorded confirmation for exactly this destination, if any."""
    return (await db.execute(select(OrganizationAiConfirmation, User.display_name)
        .join(User, (User.id == OrganizationAiConfirmation.confirmed_by_user_id)
            & (User.organization_id == OrganizationAiConfirmation.organization_id))
        .where(OrganizationAiConfirmation.organization_id == organization_id, *_confirmation_match(provider, base_url))
        .order_by(OrganizationAiConfirmation.confirmed_at.desc(), OrganizationAiConfirmation.id.desc())
        .limit(1))).first()


async def effective_ai_settings(db, organization_id) -> EffectiveAiSettings:
    row = await OrganizationAiSettingsRepository(db, organization_id).get()
    if row is None:
        return EffectiveAiSettings()
    key = None
    if row.encrypted_api_key:
        try:
            key = SecretStr(_cipher(organization_id).decrypt(row.encrypted_api_key.encode()).decode())
        except (InvalidToken, ValueError):
            raise AiKeyUnreadableError() from None
        if await matching_confirmation(db, organization_id, row.provider, row.base_url) is None:
            # A stored key without a recorded confirmation for its destination
            # stays unused: every AI entry point fails closed on a missing key.
            key = None
    return EffectiveAiSettings(row.provider, key, row.model, row.import_enabled, row.qa_enabled, row.monthly_limit_usd, row.base_url, row.request_timeout_seconds)


async def worker_ai_settings(organization_id):
    """Existing operator smoke/eval tools use the same tenant configuration."""
    from app.core.db import get_worker_tenant_session
    async with get_worker_tenant_session(organization_id) as db:
        effective = await effective_ai_settings(db, organization_id)
    if not effective.import_enabled or not effective.api_key or not effective.model:
        raise ValueError('Organization AI import is not configured')
    return effective


def public_settings(row, confirmation=None) -> AiSettingsOut:
    active = None
    if row and row.encrypted_api_key and confirmation:
        record, confirmer_name = confirmation
        active = AiConfirmationOut(confirmed_by_user_id=record.confirmed_by_user_id,
            confirmed_by_name=confirmer_name, provider=record.provider, base_url=record.base_url,
            text_version=record.text_version, confirmed_at=record.confirmed_at)
    return AiSettingsOut(request_timeout_seconds=row.request_timeout_seconds if row else None, provider=row.provider if row else 'openai', base_url=row.base_url if row else None, key_set=bool(row and row.encrypted_api_key),
        model=row.model if row else None, import_enabled=bool(row and row.import_enabled),
        qa_enabled=bool(row and row.qa_enabled), monthly_limit_usd=row.monthly_limit_usd if row else None,
        price_known=bool(row and model_price(row.model, row.provider)),
        confirmation=active, confirmation_required=bool(row and row.encrypted_api_key and confirmation is None))


async def read_settings(db, organization_id):
    row = await OrganizationAiSettingsRepository(db, organization_id).get()
    confirmation = None
    if row and row.encrypted_api_key:
        confirmation = await matching_confirmation(db, organization_id, row.provider, row.base_url)
    return public_settings(row, confirmation)


def entered_key(value):
    key = value.get_secret_value().strip() if value else ''
    if not key or len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
        raise HTTPException(422, 'Bitte geben Sie einen gültigen API-Schlüssel ohne Leerzeichen ein.')
    return key


def _audit_diff(previous, body, encrypted, organization_id):
    """What changed for the audit event; never the key, ciphertext, hash or length."""
    diff = {}
    had_key = bool(previous and previous.encrypted_api_key)
    has_key = encrypted is not None
    if had_key != has_key:
        diff['api_key'] = 'set' if has_key else 'removed'
    elif has_key and 'api_key' in body.model_fields_set:
        try:
            same_key = _cipher(organization_id).decrypt(previous.encrypted_api_key.encode()).decode() == entered_key(body.api_key)
        except (InvalidToken, ValueError):
            same_key = False
        if not same_key:
            diff['api_key'] = 'replaced'
    for switch in ('import_enabled', 'qa_enabled'):
        before = bool(previous and getattr(previous, switch))
        after = bool(getattr(body, switch))
        if before != after:
            diff[switch] = {'from': before, 'to': after}
    if previous and previous.provider != body.provider:
        diff['provider'] = {'from': previous.provider, 'to': body.provider}
    if previous and previous.base_url != body.base_url:
        diff['base_url'] = {'from': previous.base_url, 'to': body.base_url}
    return diff


async def write_settings(db, organization_id, body, auth):
    repo = OrganizationAiSettingsRepository(db, organization_id)
    await repo.lock_budget()
    previous = await repo.get()
    if body.provider == 'openai_compatible':
        from app.declarations.provider_runtime import validate_base_url
        try:
            body.base_url = validate_base_url(body.base_url, hosted=settings.deployment_mode == 'hosted')
        except ValueError:
            raise HTTPException(422, 'Bitte geben Sie eine gültige API-Basis-URL ein. Im gehosteten Betrieb sind nur öffentliche HTTPS-Ziele erlaubt.') from None
    elif body.base_url is not None:
        raise HTTPException(422, 'Eine eigene Basis-URL ist nur für OpenAI-kompatible Anbieter vorgesehen.')
    destination_changed = previous and (previous.provider, previous.base_url) != (body.provider, body.base_url)
    encrypted = previous.encrypted_api_key if previous and not destination_changed else None
    entered_new_key = False
    if 'api_key' in body.model_fields_set:
        if body.api_key is not None:
            entered = entered_key(body.api_key)
            if encrypted is not None:
                try:
                    entered_new_key = _cipher(organization_id).decrypt(encrypted.encode()).decode() != entered
                except (InvalidToken, ValueError):
                    entered_new_key = True
            else:
                entered_new_key = True
            encrypted = _cipher(organization_id).encrypt(entered.encode()).decode()
        else:
            encrypted = None
    if (body.import_enabled or body.qa_enabled) and (not encrypted or not body.model):
        raise HTTPException(422, 'Bitte hinterlegen Sie zuerst einen API-Schlüssel und wählen Sie ein Modell.')
    record_confirmation = False
    if encrypted is not None:
        existing_confirmation = await matching_confirmation(db, organization_id, body.provider, body.base_url)
        if entered_new_key and not body.confirmation:
            raise HTTPException(422, {'code': 'ORGANIZATION_AI_CONFIRMATION_REQUIRED', 'message': CONFIRMATION_REQUIRED_MESSAGE})
        # Confirming an already confirmed, unchanged key records nothing; a key
        # kept without any confirmation (stored before this change) can be
        # confirmed once, without re-entering it.
        record_confirmation = bool(body.confirmation and (entered_new_key or existing_confirmation is None))
    values = body.model_dump(exclude={'api_key', 'confirmation'})
    diff = _audit_diff(previous, body, encrypted, organization_id)
    if record_confirmation:
        diff['confirmation'] = 'recorded'
    row = await repo.save({**values, 'encrypted_api_key': encrypted})
    confirmation = None
    if encrypted is not None:
        if record_confirmation:
            db.add(OrganizationAiConfirmation(organization_id=organization_id, confirmed_by_user_id=auth.user_id,
                provider=body.provider, base_url=body.base_url, text_version=AI_PROVIDER_CONFIRMATION_VERSION))
            await db.flush()
        # Re-read after save(): the repository expires all state it returns.
        confirmation = await matching_confirmation(db, organization_id, body.provider, body.base_url)
    if diff:
        await AuditRepository(db, organization_id).create(AuditEvent(
            actor_user_id=auth.user_id, entity_type='organization_ai_settings', entity_id=organization_id,
            action='ai_settings.updated', payload_diff=diff))
    return public_settings(row, confirmation)


async def discover_models(db, organization_id, body):
    from app.declarations.openai_models import list_models
    effective = await effective_ai_settings(db, organization_id)
    provider = body.provider if body.provider is not None else effective.provider
    base_url = body.base_url if 'base_url' in body.model_fields_set else effective.base_url
    if provider == 'openai_compatible':
        from app.declarations.provider_runtime import validate_base_url
        try:
            base_url = validate_base_url(base_url, hosted=settings.deployment_mode == 'hosted')
        except ValueError:
            raise HTTPException(422, 'Bitte geben Sie eine gültige API-Basis-URL ein.') from None
    elif base_url is not None:
        raise HTTPException(422, 'Eine eigene Basis-URL ist nur für OpenAI-kompatible Anbieter vorgesehen.')
    if body.api_key is not None:
        key = entered_key(body.api_key)
    else:
        if (provider, base_url) != (effective.provider, effective.base_url):
            raise HTTPException(422, 'Bitte geben Sie für den neuen Anbieter einen eigenen API-Schlüssel ein.')
        key = effective.api_key.get_secret_value() if effective.api_key else ''
    if not key:
        raise HTTPException(422, 'Bitte geben Sie zuerst Ihren API-Schlüssel ein.')
    return AiModelsOut(models=[AiModelOut(id=model, evaluated=provider == 'openai' and model in EVALUATED_MODELS,
        price_known=model_price(model, provider) is not None) for model in await list_models(key, provider=provider, base_url=base_url, timeout_seconds=effective.request_timeout_seconds if (provider, base_url) == (effective.provider, effective.base_url) else None)])


async def require_organization_ai_budget(db, organization_id, amount, *, model=None, provider=None):
    repo = OrganizationAiSettingsRepository(db, organization_id)
    await repo.lock_budget()
    row = await repo.get()
    if row is None or row.monthly_limit_usd is None or not model_price(model or row.model, provider or row.provider):
        return
    used = await repo.monthly_usage()
    if used + amount > row.monthly_limit_usd or used >= row.monthly_limit_usd:
        raise HTTPException(409, {'code': 'ORGANIZATION_AI_BUDGET_EXCEEDED',
            'message': 'Das monatliche KI-Budget Ihrer Organisation ist ausgeschöpft. Bitte passen Sie das Limit in den Einstellungen an.'})
