"""Person endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.services.person_source_access import require_person_prepare
from app.core.tenant import (
    AuthContext,
    get_current_user,
    require_write_dep,
)
from app.repositories.person_repo import PersonRepository
from app.models.person import Person

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

from app.services.master_creation_service import PersonCreate, create_person as create_person_record

router = APIRouter(prefix="/api/persons", tags=["persons"])


class PersonOut(BaseModel):
    id: str
    can_prepare: bool = False
    can_export: bool = False
    first_name: str
    last_name: str
    company_name: str | None
    email: str | None
    phone: str | None
    street: str
    zip_code: str
    city: str
    iban: str | None
    bic: str | None
    bank_name: str | None

    model_config = {"from_attributes": True}


class PersonUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    street: str | None = None
    zip_code: str | None = None
    city: str | None = None
    iban: str | None = None
    bic: str | None = None
    bank_name: str | None = None


@router.get("", response_model=list[PersonOut])
async def list_persons(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = PersonRepository(db, auth.organization_id)
    items = await repo.list_all()
    return await _outputs(db, items)


@router.get("/page", response_model=Page[PersonOut])
async def page_persons(
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    items, cursor = await page_rows(db, auth, Person, Person.last_name, [Person.first_name, Person.last_name, Person.company_name], query)
    return Page(items=await _outputs(db, items), next_cursor=cursor)


@router.get("/{person_id}", response_model=PersonOut)
async def get_person(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = PersonRepository(db, auth.organization_id)
    p = await repo.get_by_id(person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return (await _outputs(db, [p]))[0]


@router.post("", response_model=PersonOut, status_code=201)
async def create_person(
    body: PersonCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    person = await create_person_record(db, auth, body)
    return (await _outputs(db, [person]))[0]


@router.put("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: uuid.UUID,
    body: PersonUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    repo = PersonRepository(db, auth.organization_id)
    person = await repo.get_by_id(person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    await require_person_prepare(db, auth.organization_id, person_id)
    for field in (
        "first_name", "last_name", "company_name", "email", "phone",
        "street", "zip_code", "city", "iban", "bic", "bank_name",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(person, field, val)

    await db.flush()
    return (await _outputs(db, [person]))[0]


async def _outputs(db, people):
    if not people:
        return []
    # One bounded metadata query for the page; never infer edit rights from role.
    rows = (await db.execute(select(Person.id,
        (func.scope_actor_role(Person.organization_id).in_(['OWNER_ADMIN','CLERK']) &
         func.person_source_access(Person.organization_id,Person.id,'prepare')).label('prepare'),
        func.person_source_access(Person.organization_id,Person.id,'export').label('export'),
    ).where(Person.id.in_([p.id for p in people])))).all()
    rights = {row.id: (row.prepare,row.export) for row in rows}
    if any(person.id not in rights for person in people):
        raise HTTPException(409, 'Contact access changed; reload the page')
    return [PersonOut(**_to_out(p).model_dump(exclude={'can_prepare','can_export'}),
        can_prepare=rights.get(p.id,(False,False))[0],can_export=rights.get(p.id,(False,False))[1]) for p in people]


def _to_out(p: Person) -> PersonOut:
    return PersonOut(
        id=str(p.id),
        first_name=p.first_name,
        last_name=p.last_name,
        company_name=p.company_name,
        email=p.email,
        phone=p.phone,
        street=p.street,
        zip_code=p.zip_code,
        city=p.city,
        iban=p.iban,
        bic=p.bic,
        bank_name=p.bank_name,
    )
