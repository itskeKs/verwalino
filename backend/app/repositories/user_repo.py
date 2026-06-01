"""User repository -- tenant-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.base import TenantRepository


class UserRepository(TenantRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        stmt = self._base_query().where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class UserAuthRepository:
    """Lookup a user by email across orgs -- only for the login flow.

    This intentionally does NOT filter by org_id.  It runs on an
    unscoped session (which sets the impossible UUID, so RLS on
    other tables still blocks).  The `user` table needs a special
    RLS policy for this: see the migration.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email_global(self, email: str) -> User | None:
        """Find a user by email (for login).  Returns None if not found."""
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()
