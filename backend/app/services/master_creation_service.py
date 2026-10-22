"""Canonical master-data creation shared by single-record and CSV APIs."""
from __future__ import annotations
import hashlib
import json
import uuid
import re
from decimal import Decimal, InvalidOperation
from fastapi import HTTPException
from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.declarations.review_schemas import NewPropertyAudience
from app.services.property_data_domain_service import PropertyDataDomainService
from app.services.property_master_service import require_property_master
from app.services.person_source_access import require_contact_creation
from app.repositories.property_creation_repo import PropertyCreationRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.person_repo import PersonRepository
from app.models.property import Property
from app.models.unit import Unit
from app.models.person import Person

class PropertyFacts(BaseModel):
    construction_year: StrictInt | None = Field(default=None, ge=1, le=9999)
    total_area: StrictStr | None = None

    @field_validator("total_area")
    @classmethod
    def validate_total_area(cls, value):
        if value is None:
            return None
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", value):
            raise ValueError("total_area must be a plain nonnegative decimal with at most two fractional digits")
        if Decimal(value) >= Decimal("1000000000000"):
            raise ValueError("total_area exceeds NUMERIC(14,2)")
        return value


class PropertyCreate(PropertyFacts):
    name: str
    street: str = ""
    zip_code: str = ""
    city: str = ""
    management_type: str
    fiscal_year_start_month: int = 1
    mea_denominator: int | None = Field(default=None, ge=1, le=1_000_000_000_000)
    data_audience: NewPropertyAudience | None = None


class UnitCreate(BaseModel):
    property_id: str
    number: str
    location: str = ""
    area: str | None = None
    heated_area: str | None = None
    usage_type: str
    mea: str | None = None
    building_part: str = ""


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


async def create_property(db, auth, body: PropertyCreate, idempotency_key=None):
    _validate_management_type(body.management_type)
    repo = PropertyRepository(db, auth.organization_id)
    mode = await db.scalar(text('SELECT property_source_security_mode(:org)'), {'org':auth.organization_id})
    if mode == 'PAUSED':
        raise HTTPException(404, 'Property creation is paused')
    if mode != 'PREPARATION' and body.data_audience is None:
        raise HTTPException(422, 'Explicit property data audience required')
    if body.data_audience is not None and idempotency_key is None:
        raise HTTPException(422, 'Idempotency-Key required for audience creation')
    # Keep existing receipt digests stable when callers omit the new facts.
    payload = body.model_dump(mode='json', exclude={
        field for field in ("construction_year", "total_area") if field not in body.model_fields_set
    })
    if body.data_audience is not None:
        for grant in payload['data_audience']['grants']:
            grant['capabilities'] = sorted(grant['capabilities'])
        payload['data_audience']['grants'].sort(key=lambda grant: grant['user_id'])
    receipts = PropertyCreationRepository(db, auth.organization_id, auth.user_id)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if idempotency_key is not None:
        receipt = await receipts.get(idempotency_key)
        if receipt and receipt['request_digest'] != digest:
            raise HTTPException(409, 'Idempotency-Key already used with a different request')
        prior = receipt['property_id'] if receipt else None
        if prior:
            await require_property_master(db, auth.organization_id, prior)
            saved = await repo.get_by_id(prior)
            if saved is None:
                raise HTTPException(404, 'Property not accessible')
            return saved
    property_id = uuid.uuid4()
    if body.data_audience is not None:
        await PropertyDataDomainService(db, auth).prepare_new_audience(property_id, body.data_audience)
    prop = Property(
        id=property_id,
        name=body.name,
        street=body.street,
        zip_code=body.zip_code,
        city=body.city,
        management_type=body.management_type,
        fiscal_year_start_month=body.fiscal_year_start_month,
        construction_year=body.construction_year,
        total_area=Decimal(body.total_area) if body.total_area is not None else None,
        mea_denominator=body.mea_denominator,
        mea_scale_status=("CONFIRMED" if body.mea_denominator is not None else "LEGACY_UNCONFIRMED"),
    )
    prop = await repo.create(prop)
    if idempotency_key is not None:
        await receipts.record(idempotency_key, digest, prop.id)
    return prop


async def create_unit(db, auth, body: UnitCreate):
    await require_property_master(db, auth.organization_id, uuid.UUID(body.property_id))
    _validate_usage_type(body.usage_type)
    repo = UnitRepository(db, auth.organization_id)
    mea = _parse_mea_numerator(body.mea) if body.mea is not None else None
    if mea is not None:
        prop = await _require_confirmed_mea_scale(
            db, auth.organization_id, uuid.UUID(body.property_id)
        )
        units = await repo.list_by_property_for_update(prop.id)
        _validate_mea_total(mea, prop, units)
    unit = Unit(
        property_id=uuid.UUID(body.property_id),
        number=body.number,
        location=body.location,
        area=Decimal(body.area) if body.area else None,
        heated_area=Decimal(body.heated_area) if body.heated_area else None,
        usage_type=body.usage_type,
        mea=mea,
        building_part=body.building_part,
    )
    unit = await repo.create(unit)
    return unit


async def create_person(db, auth, body: PersonCreate):
    repo = PersonRepository(db, auth.organization_id)
    await require_contact_creation(db, auth.organization_id)
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
    return person


def _validate_management_type(mt: str) -> None:
    if mt not in ("MIET", "WEG", "SEV", "MISCH"):
        raise HTTPException(status_code=422, detail="Invalid management_type")


def _validate_usage_type(ut: str) -> None:
    if ut not in ("WOHNUNG", "GEWERBE", "STELLPLATZ", "SONSTIGE"):
        raise HTTPException(status_code=422, detail="Invalid usage_type")


def _parse_mea_numerator(value: str) -> Decimal:
    try:
        mea = Decimal(value)
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_INTEGER") from None
    if not mea.is_finite() or mea != mea.to_integral_value():
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_INTEGER")
    if mea < 0:
        raise HTTPException(status_code=422, detail="MEA_MUST_BE_NONNEGATIVE")
    return mea


async def _require_confirmed_mea_scale(
    db: AsyncSession,
    org_id: uuid.UUID,
    property_id: uuid.UUID,
) -> Property:
    property_repo = PropertyRepository(db, org_id)
    prop = await property_repo.get_by_id_for_update(property_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="Property not found")
    if prop.mea_scale_status != "CONFIRMED" or prop.mea_denominator is None:
        raise HTTPException(
            status_code=409,
            detail="Property MEA scale must be confirmed before writing MEA values",
        )
    return prop


def _validate_mea_total(
    proposed: Decimal,
    prop: Property,
    units: list[Unit] | tuple[Unit, ...],
    *,
    replacing_unit: Unit | None = None,
) -> None:
    denominator = Decimal(prop.mea_denominator)
    if proposed > denominator:
        raise HTTPException(status_code=422, detail="MEA_EXCEEDS_DENOMINATOR")
    total = sum((unit.mea or Decimal(0) for unit in units), Decimal(0))
    if replacing_unit is not None and replacing_unit.mea is not None:
        total -= replacing_unit.mea
    if total + proposed > denominator:
        raise HTTPException(status_code=409, detail="MEA_TOTAL_EXCEEDS_DENOMINATOR")
