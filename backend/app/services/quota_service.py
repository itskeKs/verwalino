"""Write-time organization quotas, serialized in the caller's transaction."""
from dataclasses import asdict
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.quotas import check_quota, get_quota_plan
from app.core.storage import get_store, LocalFsDocumentStore


async def lock_quota(db, org_id, *, budget=False):
    # Source authorization always takes the ACL first. All quota writers then
    # share one organization lock, retained until commit or rollback.
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
                     {"key": f"scope-acl:{org_id}"})
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                     {"key": f"{'hosted-ai' if budget else 'organization-quota'}:{org_id}"})


async def database_usage(db, org_id):
    return await db.scalar(text("SELECT organization_quota_usage(:org)"), {"org": org_id})


def require_quota(usage, kind, delta, *, plan=None):
    decision = check_quota(usage, plan or get_quota_plan(), kind, delta)
    if not decision.allowed:
        raise HTTPException(413 if kind in {"storage_bytes", "upload_bytes"} else 409,
                            decision.message)


async def require_counts(db, org_id, *, properties=0, units=0):
    plan = get_quota_plan()
    if plan.name == "unlimited":
        return
    await lock_quota(db, org_id)
    usage = await database_usage(db, org_id)
    for kind, delta in (("properties", properties), ("units", units)):
        if delta:
            require_quota(usage[kind], kind, delta, plan=plan)


async def require_ai_budget(db, org_id, amount):
    plan = get_quota_plan()
    if plan.name == "unlimited":
        return
    # Even a zero-cost estimate cannot enable a plan whose budget is zero.
    require_quota(0, "ai_budget_usd", max(amount, Decimal("0.000001")), plan=plan)
    await lock_quota(db, org_id, budget=True)
    usage = await database_usage(db, org_id)
    require_quota(Decimal(usage["ai_budget_usd"]), "ai_budget_usd", amount, plan=plan)


async def quota_summary(db, org_id):
    plan = get_quota_plan()
    usage = await database_usage(db, org_id)
    store = get_store()
    if not isinstance(store, LocalFsDocumentStore):
        raise HTTPException(503, "Speichernutzung nicht verfügbar.")
    try:
        usage["storage_bytes"] = await run_in_threadpool(store.organization_bytes, str(org_id))
    except OSError:
        raise HTTPException(503, "Speichernutzung nicht verfügbar.") from None
    limits = asdict(plan)
    limits.pop("name")
    limits["ai_budget_usd"] = str(plan.ai_budget_usd) if plan.ai_budget_usd is not None else None
    return {"plan": plan.name, "usage": usage, "limits": limits}
