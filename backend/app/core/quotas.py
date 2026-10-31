"""Documented shared plans; all quota decisions use the deployment policy.

The hosted free allowance is 2 GiB retained storage, 25 MiB per upload,
20 properties, 200 units and USD 0 per calendar month (UTC) for model calls.
Change FREE_PLAN in code to change the hosted offer; per-organization overrides
and paid plans are deliberately deferred. None means unlimited, including AI.

Lock order for writers: source ACL / financial-input locks first, organization
quota advisory lock next, then property rows (SHARE or UPDATE), then the hosted
filesystem lock. Keep filesystem locks through post-commit/rollback cleanup.
Selfhost skips quota advisory and filesystem locks.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.core.deployment_policy import get_deployment_policy
from app.core.document_limits import MAX_UPLOAD_BYTES, format_upload_limit

QuotaKind = Literal["storage_bytes", "upload_bytes", "properties", "units", "ai_budget_usd"]


@dataclass(frozen=True)
class QuotaPlan:
    name: Literal["free", "unlimited"]
    storage_bytes: int | None
    upload_bytes: int | None
    properties: int | None
    units: int | None
    ai_budget_usd: Decimal | None


FREE_PLAN = QuotaPlan("free", 2 * 1024**3, MAX_UPLOAD_BYTES, 20, 200, Decimal("0"))
UNLIMITED_PLAN = QuotaPlan("unlimited", None, None, None, None, None)


def get_quota_plan() -> QuotaPlan:
    return FREE_PLAN if get_deployment_policy().quota_enforcement else UNLIMITED_PLAN


def _size(value: int) -> str:
    for unit, divisor in (("GiB", 1024**3), ("MiB", 1024**2)):
        if value >= divisor and value % divisor == 0:
            return f"{value // divisor} {unit}"
    return f"{value} Bytes"


@dataclass(frozen=True)
class QuotaDecision:
    allowed: bool
    message: str | None = None
    code: str | None = None


def check_quota(usage: int | Decimal, plan: QuotaPlan, kind: QuotaKind,
                delta: int | Decimal) -> QuotaDecision:
    """Pure inclusive boundary check; no storage, session or clock dependencies."""
    limit = getattr(plan, kind)
    if limit is None or usage + delta <= limit:
        return QuotaDecision(True)
    messages = {
        "storage_bytes": lambda: f"Speicherkontingent erschöpft ({_size(limit)}). Sie können keine weiteren Dokumente speichern.",
        "upload_bytes": lambda: f"Datei zu groß (maximal {format_upload_limit(limit)}). Bitte wählen Sie eine kleinere Datei.",
        "properties": lambda: f"Objektkontingent erschöpft ({limit} Objekte). Sie können kein weiteres Objekt anlegen.",
        "units": lambda: f"Einheitenkontingent erschöpft ({limit} Einheiten). Sie können keine weitere Einheit anlegen.",
        "ai_budget_usd": lambda: "Das monatliche KI-Budget ist ausgeschöpft. Bitte wenden Sie sich an den Betreiber.",
    }
    codes = {
        "storage_bytes": "QUOTA_STORAGE_EXCEEDED",
        "upload_bytes": "UPLOAD_TOO_LARGE",
        "properties": "QUOTA_PROPERTIES_EXCEEDED",
        "units": "QUOTA_UNITS_EXCEEDED",
        "ai_budget_usd": "ORGANIZATION_AI_BUDGET_EXCEEDED",
    }
    return QuotaDecision(False, messages[kind](), codes[kind])
