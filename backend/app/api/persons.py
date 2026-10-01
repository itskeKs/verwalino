"""Person endpoints -- CRUD within the current organization."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import (
    AuthContext,
    get_current_user,
    get_db,
    require_write_dep,
)
from app.repositories.person_repo import PersonRepository
from app.models.person import Person

from app.repositories.pagination import Page, PageQuery, page_query, page_rows

router = APIRouter(prefix="/api/persons", tags=["persons"])


class PersonOut(BaseModel):
    id: str
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


class PersonCreate(BaseModel):
    first_name: str = ""
    last_name: str
    company_name: str | None = None
    email: str | None = None
    phone: str | None = None
    street: str = ""
    zip_code: str = ""
    city: str = ""
    iban: str | None = None
    bic: str | None = None
    bank_name: str | None = None


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
    db: AsyncSession = Depends(get_db),
):
    repo = PersonRepository(db, auth.organization_id)
    items = await repo.list_all()
    return [_to_out(p) for p in items]


@router.get("/page", response_model=Page[PersonOut])
async def page_persons(
    query: PageQuery = Depends(page_query),
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    items, cursor = await page_rows(db, auth, Person, Person.last_name, [Person.first_name, Person.last_name, Person.company_name], query)
    return Page(items=[_to_out(item) for item in items], next_cursor=cursor)


@router.get("/{person_id}", response_model=PersonOut)
async def get_person(
    person_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    repo = PersonRepository(db, auth.organization_id)
    p = await repo.get_by_id(person_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return _to_out(p)


@router.post("", response_model=PersonOut, status_code=201)
async def create_person(
    body: PersonCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = PersonRepository(db, auth.organization_id)
    person = Person(
        first_name=body.first_name,
        last_name=body.last_name,
        company_name=body.company_name,
        email=body.email,
        phone=body.phone,
        street=body.street,
        zip_code=body.zip_code,
        city=body.city,
    )
    if body.iban is not None:
        person.iban = body.iban
    if body.bic is not None:
        person.bic = body.bic
    if body.bank_name is not None:
        person.bank_name = body.bank_name

    person = await repo.create(person)
    return _to_out(person)


@router.put("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: uuid.UUID,
    body: PersonUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_db),
):
    repo = PersonRepository(db, auth.organization_id)
    person = await repo.get_by_id(person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")

    for field in (
        "first_name", "last_name", "company_name", "email", "phone",
        "street", "zip_code", "city", "iban", "bic", "bank_name",
    ):
        val = getattr(body, field)
        if val is not None:
            setattr(person, field, val)

    await db.flush()
    return _to_out(person)


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
