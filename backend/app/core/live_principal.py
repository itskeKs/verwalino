"""Revalidate HTTP principals inside the transaction that reads or changes data."""
from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep


async def require_live_principal(db: AsyncSession, auth: AuthContext, *, writer: bool) -> None:
    # The authentication dependency used a different, already closed transaction.
    # Take the shared ACL lock before domain row locks, then read a fresh role.
    # Membership/deactivation changes take this lock exclusively (migration 0033).
    await db.execute(text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key,0))"),
                     {"key": f"scope-acl:{auth.organization_id}"})
    role = await db.scalar(text("SELECT scope_actor_role(:org)"), {"org": auth.organization_id})
    if role is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if writer and role not in {"OWNER_ADMIN", "CLERK"}:
        raise HTTPException(status_code=403, detail="Writer required")


async def get_live_writer_db(
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=True)
    return db


async def get_live_member_db(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncSession:
    await require_live_principal(db, auth, writer=False)
    return db
