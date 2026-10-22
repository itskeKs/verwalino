"""User endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictStr, field_validator, model_validator
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.tenant import AuthContext, get_current_user, get_db, require_admin_dep
from app.core.live_principal import require_live_principal
from app.core.auth_db import validate_session_principal
from app.core.security import hash_password, normalize_email, validate_new_password
from app.core.rbac import Role
from app.repositories.user_repo import UserRepository
from app.repositories.membership_repo import MembershipRepository
from app.models.user import User
from app.models.membership import Membership
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository as CanonicalAuditRepository

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

router = APIRouter(prefix="/api/users", tags=["users"])


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_active: bool
    role: Role | None = None

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


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: StrictBool | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(
            getattr(self, name) is None for name in self.model_fields_set
        ):
            raise ValueError("Provide role and/or is_active with a non-null value")
        return self


class PasswordReset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: StrictStr

    @field_validator("password")
    @classmethod
    def password_matches_policy(cls, value: str) -> str:
        return validate_new_password(value)


async def user_admin_db(
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> AsyncSession:
    # Acquire exclusive first: never upgrade a shared ACL lock after row locks.
    await require_live_principal(db, auth, writer=False, exclusive=True, administrator=True)
    if await validate_session_principal(db, auth.user_id, auth.organization_id, auth.session_version) is None:
        raise HTTPException(401, "Not authenticated")
    return db


def user_out(user: User, role: str | None) -> UserOut:
    return UserOut(id=str(user.id), email=user.email, display_name=user.display_name,
                   is_active=user.is_active, role=role if role in {item.value for item in Role} else None)


async def user_roles(db: AsyncSession, organization_id: uuid.UUID, users) -> dict:
    if not users:
        return {}
    rows = await db.execute(select(Membership.user_id, Membership.role).where(
        Membership.organization_id == organization_id,
        Membership.user_id.in_([user.id for user in users]),
    ))
    return dict(rows.all())


async def locked_target(db: AsyncSession, auth: AuthContext, user_id: uuid.UUID):
    user = await UserRepository(db, auth.organization_id).get_by_id_for_update(user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    membership = await db.scalar(select(Membership).where(
        Membership.organization_id == auth.organization_id,
        Membership.user_id == user_id,
    ).with_for_update().execution_options(populate_existing=True))
    if membership is None:
        raise HTTPException(404, "User not found")
    return user, membership


async def audit_user(db: AsyncSession, auth: AuthContext, user_id: uuid.UUID,
                     action: str, payload: dict) -> None:
    await CanonicalAuditRepository(db, auth.organization_id).create(AuditEvent(
        actor_user_id=auth.user_id, entity_type="user", entity_id=user_id,
        action=action, payload_diff=payload,
    ))


@router.get("", response_model=list[UserOut])
async def list_users(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = UserRepository(db, auth.organization_id)
    users = await repo.list_all()
    roles = await user_roles(db, auth.organization_id, users)
    return [user_out(user, roles.get(user.id)) for user in users]


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
    roles = await user_roles(db, auth.organization_id, items)
    return Page(items=[user_out(user, roles.get(user.id)) for user in items], next_cursor=cursor)


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
    roles = await user_roles(db, auth.organization_id, [user])
    return user_out(user, roles.get(user.id))


@router.post("", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
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
    try:
        async with db.begin_nested():
            user = await user_repo.create(user)
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
        if getattr(exc.orig, "sqlstate", None) == "23505" and constraint in {
            "uq_user_org_email", "uq_user_org_canonical_email"
        }:
            raise HTTPException(409, "Email already in use") from exc
        raise

    # Create membership
    membership_repo = MembershipRepository(db, auth.organization_id)
    membership = Membership(
        organization_id=auth.organization_id,
        user_id=user.id,
        role=body.role,
    )
    await membership_repo.create(membership)

    await audit_user(db, auth, user.id, "CREATE", {
        "before": None, "after": {"role": membership.role, "is_active": user.is_active},
    })
    return user_out(user, membership.role)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID,
    body: UserPatch,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
):
    user, membership = await locked_target(db, auth, user_id)
    if user_id == auth.user_id and (
        body.is_active is False or (body.role is not None and body.role != Role.OWNER_ADMIN)
    ):
        raise HTTPException(409, "Cannot deactivate or demote yourself")
    before = {"role": membership.role, "is_active": user.is_active}
    if body.role is not None:
        membership.role = body.role.value
    if body.is_active is not None:
        user.is_active = body.is_active
    await db.flush()
    await audit_user(db, auth, user_id, "UPDATE", {
        "before": before, "after": {"role": membership.role, "is_active": user.is_active},
    })
    return user_out(user, membership.role)


@router.post("/{user_id}/reset-password", status_code=204, response_class=Response)
async def reset_password(
    user_id: uuid.UUID,
    body: PasswordReset,
    auth: AuthContext = Depends(require_admin_dep),
    db: AsyncSession = Depends(user_admin_db),
):
    user, membership = await locked_target(db, auth, user_id)
    user.password_hash = hash_password(body.password)
    await db.flush()
    await audit_user(db, auth, user_id, "RESET_PASSWORD", {"action": "reset_password"})
    return Response(status_code=204)
