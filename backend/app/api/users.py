"""User endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.core.security import hash_password, normalize_email, validate_new_password
from app.core.rbac import Role
from app.repositories.user_repo import UserRepository
from app.repositories.membership_repo import MembershipRepository
from app.models.user import User
from app.models.membership import Membership

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

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

    @field_validator("email", mode="before")
    @classmethod
    def email_has_canonical_form(cls, value: object) -> str:
        return normalize_email(value)

    @field_validator("password")
    @classmethod
    def password_matches_policy(cls, value: str) -> str:
        return validate_new_password(value)


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


@router.get("/page", response_model=Page[UserOut])
async def page_users(
    eligible_for_scope: bool = False,
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filters = (User.is_active.is_(True), exists(select(Membership.id).where(
        Membership.organization_id == User.organization_id, Membership.user_id == User.id))) if eligible_for_scope else ()
    items, cursor = await page_rows(db, auth, User, User.display_name, [User.display_name, User.email], query,
        filters=filters, context='scope-eligible' if eligible_for_scope else '')
    return Page(items=[UserOut(id=str(u.id),email=u.email,display_name=u.display_name,is_active=u.is_active) for u in items], next_cursor=cursor)


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
