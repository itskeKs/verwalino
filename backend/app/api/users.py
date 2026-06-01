"""User endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.core.security import hash_password
from app.core.rbac import Role
from app.repositories.user_repo import UserRepository
from app.repositories.membership_repo import MembershipRepository
from app.models.user import User
from app.models.membership import Membership

router = APIRouter(prefix="/api/users", tags=["users"])


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    email: str
    password: str
    display_name: str = ""
    role: str = "CLERK"


@router.get("", response_model=list[UserOut])
async def list_users(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db, auth.organization_id)
    users = await repo.list_all()
    return [
        UserOut(
            id=str(u.id),
            email=u.email,
            display_name=u.display_name,
            is_active=u.is_active,
        )
        for u in users
    ]


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db, auth.organization_id)
    user = await repo.get_by_id(user_id)
    if user is None:
        # Return 404, not 403, to avoid revealing existence (architecture.md 7.4)
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
    )


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Create a user -- OWNER_ADMIN only."""
    # Validate role
    if body.role not in {r.value for r in Role}:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")

    user_repo = UserRepository(db, auth.organization_id)

    # Check uniqueness
    existing = await user_repo.get_by_email(body.email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already in use")

    user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        organization_id=auth.organization_id,
    )
    user = await user_repo.create(user)

    # Create membership
    membership_repo = MembershipRepository(db, auth.organization_id)
    membership = Membership(
        organization_id=auth.organization_id,
        user_id=user.id,
        role=body.role,
    )
    await membership_repo.create(membership)

    return UserOut(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        is_active=user.is_active,
    )
