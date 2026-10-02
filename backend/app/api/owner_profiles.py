"""OwnerProfile endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.person_source_access import require_person_prepare
from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.models.owner_profile import OwnerProfile

from app.models.person import Person
from app.repositories.pagination import Page, PageQuery, page_query, page_rows

router = APIRouter(prefix="/api/owner-profiles", tags=["owner-profiles"])


class OwnerProfileOut(BaseModel):
    id: str
    person_id: str
    notes: str

    model_config = {"from_attributes": True}


class OwnerProfileCreate(BaseModel):
    person_id: str
    notes: str = ""


@router.get("", response_model=list[OwnerProfileOut])
async def list_owner_profiles(
    person_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = OwnerProfileRepository(db, auth.organization_id)
    items = await repo.list_all()
    if person_id:
        items = [i for i in items if i.person_id == person_id]
    return [_to_out(p) for p in items]


class OwnerChoice(OwnerProfileOut):
    display_name: str


@router.get('/page', response_model=Page[OwnerChoice])
async def page_owners(query: PageQuery = Depends(page_query),
                      auth: AuthContext = Depends(get_current_user), db: AsyncSession = Depends(get_live_member_db)):
    display = Person.last_name + ', ' + Person.first_name
    items, cursor = await page_rows(db, auth, OwnerProfile, display,
        [Person.last_name, Person.first_name], query,
        joins=((Person, and_(Person.organization_id == OwnerProfile.organization_id, Person.id == OwnerProfile.person_id)),))
    people = (await db.scalars(select(Person).where(Person.organization_id == auth.organization_id,
        Person.id.in_([item.person_id for item in items])))).all() if items else []
    names = {p.id: f'{p.last_name}, {p.first_name}' for p in people}
    if any(item.person_id not in names for item in items):
        raise HTTPException(409, 'Contact access changed; reload the selector')
    return Page(items=[OwnerChoice(**_to_out(item).model_dump(), display_name=names[item.person_id]) for item in items], next_cursor=cursor)


@router.post("", response_model=OwnerProfileOut, status_code=201)
async def create_owner_profile(
    body: OwnerProfileCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    await require_person_prepare(db, auth.organization_id, uuid.UUID(body.person_id))
    repo = OwnerProfileRepository(db, auth.organization_id)
    profile = OwnerProfile(
        person_id=uuid.UUID(body.person_id),
        notes=body.notes,
    )
    profile = await repo.create(profile)
    return _to_out(profile)


def _to_out(p: OwnerProfile) -> OwnerProfileOut:
    return OwnerProfileOut(
        id=str(p.id),
        person_id=str(p.person_id),
        notes=p.notes,
    )
