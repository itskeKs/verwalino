"""Revalidate HTTP principals inside the transaction that reads or changes data."""
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep, require_admin_dep
from app.core.db import get_review_tenant_session


async def require_live_principal(db: AsyncSession, auth: AuthContext, *, writer: bool, exclusive: bool = False, administrator: bool = False) -> None:
    # The authentication dependency used a different, already closed transaction.
    # Take the shared ACL lock before domain row locks, then read a fresh role.
    # Membership/deactivation changes take this lock exclusively (migration 0033).
    lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    await db.execute(text(f"SELECT {lock}(hashtextextended(:key,0))"),
                     {"key": f"scope-acl:{auth.organization_id}"})
    role = await db.scalar(text("SELECT scope_actor_role(:org)"), {"org": auth.organization_id})
    if role is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if administrator and role != 'OWNER_ADMIN':
        raise HTTPException(status_code=403, detail="Administrator required")
    if writer and role not in {"OWNER_ADMIN", "CLERK"}:
        raise HTTPException(status_code=403, detail="Writer required")


async def get_live_writer_db(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=True)
    return db


async def get_live_review_db(auth: AuthContext = Depends(require_write_dep)):
    """One isolated transaction for canonical correction and graph validation."""
    try:
        async with get_review_tenant_session(auth.organization_id) as db:
            await db.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {'actor': str(auth.user_id)})
            await require_live_principal(db, auth, writer=True)
            yield db
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={'code': 'REVIEW_RUNTIME_UNAVAILABLE'}) from exc


async def get_live_publication_db(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    # Publication may create grants; acquire exclusive BEFORE any shared ACL or
    # source row locks to avoid concurrent shared-to-exclusive upgrade deadlocks.
    await require_live_principal(db, auth, writer=True, exclusive=True)
    return db


async def get_live_member_db(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=False)
    return db


async def get_live_admin_db(
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=False, administrator=True)
    return db
