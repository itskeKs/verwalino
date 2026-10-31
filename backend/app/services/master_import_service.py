"""Bounded, read-only CSV preview and atomic canonical master-data creation."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
import uuid
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr
from sqlalchemy import select, text

from app.declarations.review_schemas import NewPropertyAudience
from app.models.master_import_batch import MasterImportBatch
from app.repositories.person_repo import PersonRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.services.master_creation_service import (
    PersonCreate, PropertyCreate, UnitCreate, create_person, create_property, create_unit,
    _validate_management_type, _validate_usage_type,
)
from app.services.person_source_access import require_contact_creation, require_person_prepare
from app.services.property_data_domain_service import PropertyDataDomainService
from app.services.property_master_service import require_property_master
from app.services.quota_service import require_counts

FIELDS = ('kind', 'key', 'property_key', 'name', 'street', 'zip_code', 'city',
          'management_type', 'number', 'area', 'heated_area', 'usage_type',
          'first_name', 'last_name', 'email', 'phone', 'company_name')
MAX_CSV_BYTES = 256 * 1024
MAX_ROWS = 500
KIND_COUNTS = {'property': 'properties', 'unit': 'units', 'person': 'persons'}
KIND_FIELDS = {
    'property': {'name', 'street', 'zip_code', 'city', 'management_type'},
    'unit': {'property_key', 'number', 'area', 'heated_area', 'usage_type'},
    'person': {'first_name', 'last_name', 'street', 'zip_code', 'city', 'email', 'phone', 'company_name'},
}
LIMITS = {field: 255 for field in FIELDS}
LIMITS.update(kind=8, zip_code=10, management_type=5, number=50, usage_type=12,
              email=320, phone=50, area=32, heated_area=32)


class MasterImportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    csv_text: StrictStr
    data_audience: NewPropertyAudience | None  # Explicit null is not an implicit default.


class MasterImportApplyRequest(MasterImportRequest):
    expected_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    request_id: uuid.UUID


def template_csv() -> bytes:
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS, delimiter=';', lineterminator='\r\n')
    writer.writeheader()
    writer.writerows([
        dict(kind='property', key='P1', name='Beispielobjekt', street='Musterweg 1',
             zip_code='12345', city='Musterstadt', management_type='MIET'),
        dict(kind='unit', key='U1', property_key='P1', number='1', area='60,25',
             heated_area='55.00', usage_type='WOHNUNG'),
        dict(kind='person', key='C1', first_name='Ada', last_name='Beispiel',
             email='ada@example.invalid'),
    ])
    return output.getvalue().encode('utf-8-sig')


def _check_quotes(source: str) -> None:
    # csv.reader(strict=True) still admits quotes inside unquoted fields.
    state = 'start'
    for char in source:
        if state == 'quoted':
            if char == '"':
                state = 'closed'
        elif state == 'closed':
            if char == '"':
                state = 'quoted'
            elif char in ';\r\n':
                state = 'start'
            else:
                raise ValueError('Unexpected character after closing quote')
        elif char == '"':
            if state != 'start':
                raise ValueError('Quote must begin a field')
            state = 'quoted'
        elif char in ';\r\n':
            state = 'start'
        else:
            state = 'plain'
    if state == 'quoted':
        raise ValueError('Unterminated quoted field')


def parse_csv(source: str) -> dict:
    errors, rows = [], []
    counts = dict.fromkeys(KIND_COUNTS.values(), 0)

    def error(line, field, message):
        errors.append(dict(line=line, field=field, message=message))

    try:
        size = len(source.encode('utf-8', errors='strict'))
    except UnicodeEncodeError:
        error(1, 'csv_text', 'CSV must be valid UTF-8 text')
        return dict(rows=rows, errors=errors, counts=counts)
    if size > MAX_CSV_BYTES:
        raise HTTPException(413, 'CSV exceeds 256 KiB')
    source = source.removeprefix('\ufeff')
    try:
        _check_quotes(source)
    except ValueError as exc:
        error(1, 'csv_text', str(exc))
        return dict(rows=rows, errors=errors, counts=counts)
    reader = csv.reader(io.StringIO(source, newline=''), delimiter=';', strict=True)
    try:
        header = next(reader, [])
        if tuple(header) != FIELDS:
            error(1, 'header', 'Expected the exact, unique template header')
            return dict(rows=rows, errors=errors, counts=counts)
        keys = set()
        while True:
            line = reader.line_num + 1
            record = next(reader, None)
            if record is None:
                break
            if len(rows) >= MAX_ROWS:
                error(line, 'csv_text', 'CSV exceeds 500 rows')
                break
            if len(record) != len(FIELDS):
                error(line, 'row', 'Row must have exactly the template column count')
                # Count malformed records toward the bounded row budget too.
                rows.append(dict(line=line, kind='', key='', values={}))
                continue
            values = dict(zip(FIELDS, record))
            for field, value in values.items():
                if any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} for char in value):
                    error(line, field, 'Control characters are not allowed')
                values[field] = value.strip()
                if len(values[field]) > LIMITS[field]:
                    error(line, field, f'Maximum length is {LIMITS[field]}')
            kind, key = values['kind'], values['key']
            rows.append(dict(line=line, kind=kind, key=key, values=values))
            if not key:
                error(line, 'key', 'A record key is required')
            elif key in keys:
                error(line, 'key', 'Record keys must be unique')
            keys.add(key)
            if kind not in KIND_FIELDS:
                error(line, 'kind', 'Expected property, unit or person')
                continue
            counts[KIND_COUNTS[kind]] += 1
            for field, value in values.items():
                if field not in KIND_FIELDS[kind] | {'kind', 'key'} and value:
                    error(line, field, 'Field is not applicable to this record kind')
            required = {'property': ('name', 'management_type'),
                        'unit': ('property_key', 'number', 'usage_type'),
                        'person': ('last_name',)}[kind]
            for field in required:
                if not values[field]:
                    error(line, field, 'A value is required')
            if kind == 'property':
                try:
                    _validate_management_type(values['management_type'])
                except HTTPException:
                    error(line, 'management_type', 'Invalid management_type')
            elif kind == 'unit':
                try:
                    _validate_usage_type(values['usage_type'])
                except HTTPException:
                    error(line, 'usage_type', 'Invalid usage_type')
                for field in ('area', 'heated_area'):
                    value = values[field]
                    if not value:
                        continue
                    if not re.fullmatch(r'[0-9]+(?:[.,][0-9]{1,2})?', value):
                        error(line, field, 'Expected a nonnegative plain decimal with at most two decimals')
                    else:
                        amount = Decimal(value.replace(',', '.'))
                        if amount > Decimal('99999999.99'):
                            error(line, field, 'Area exceeds NUMERIC(10,2)')
                        else:
                            values[field] = format(amount, '.2f')
        if not rows:
            error(2, 'row', 'At least one data row is required')
    except csv.Error:
        error(reader.line_num or 1, 'csv_text', 'Malformed CSV quoting')
    return dict(rows=rows, errors=errors, counts=counts)


def _digest(command, mode):
    audience = command.data_audience.model_dump(mode='json') if command.data_audience is not None else None
    if audience is not None:
        for grant in audience['grants']:
            grant['capabilities'] = sorted(grant['capabilities'])
        audience['grants'].sort(key=lambda grant: grant['user_id'])
    payload = dict(csv_text=command.csv_text, data_audience=audience, source_mode=mode)
    # ensure_ascii also fingerprints malformed Unicode without encoding it as CSV.
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode('ascii')).hexdigest()


class MasterImportService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    async def preview(self, command: MasterImportRequest):
        result = parse_csv(command.csv_text)
        mode = await self.db.scalar(text('SELECT property_source_security_mode(:org)'),
                                    {'org': self.auth.organization_id})
        if mode == 'PAUSED':
            raise HTTPException(404, 'Source effects are paused')
        result['source_digest'] = _digest(command, mode)
        if result['counts']['properties']:
            if mode != 'PREPARATION' and command.data_audience is None:
                result['errors'].append(dict(line=1, field='data_audience', message='Explicit property data audience required'))
            if command.data_audience is not None:
                await PropertyDataDomainService(self.db, self.auth).validate_new_audience(command.data_audience)
        if result['counts']['persons']:
            await require_contact_creation(self.db, self.auth.organization_id)
        property_keys = {row['key'] for row in result['rows'] if row['kind'] == 'property'}
        for row in result['rows']:
            if row['kind'] != 'unit':
                continue
            reference = row['values']['property_key']
            if reference in property_keys:
                continue
            try:
                property_id = uuid.UUID(reference)
            except ValueError:
                result['errors'].append(dict(line=row['line'], field='property_key', message='Unknown property key'))
                continue
            await require_property_master(self.db, self.auth.organization_id, property_id)
            if await PropertyRepository(self.db, self.auth.organization_id).get_by_id(property_id) is None:
                raise HTTPException(404, 'Property data not accessible')
        return result

    async def _recheck_result(self, result):
        # Never return even a partial replay after source/custody revocation.
        for row in result['rows']:
            entity_id = uuid.UUID(row['id'])
            if row['kind'] == 'property':
                await require_property_master(self.db, self.auth.organization_id, entity_id)
                entity = await PropertyRepository(self.db, self.auth.organization_id).get_by_id(entity_id)
            elif row['kind'] == 'unit':
                entity = await UnitRepository(self.db, self.auth.organization_id).get_by_id(entity_id)
                if entity is not None:
                    await require_property_master(self.db, self.auth.organization_id, entity.property_id)
            else:
                await require_person_prepare(self.db, self.auth.organization_id, entity_id)
                entity = await PersonRepository(self.db, self.auth.organization_id).get_by_id(entity_id)
            if entity is None:
                raise HTTPException(404, 'Imported data not accessible')

    async def apply(self, command: MasterImportApplyRequest):
        # get_live_publication_db already holds the exclusive organization ACL.
        # Batch RLS intentionally does not depend on source access, so we can find
        # an old result and recheck every canonical identity after revocation.
        batch = await self.db.scalar(select(MasterImportBatch).where(
            MasterImportBatch.organization_id == self.auth.organization_id,
            MasterImportBatch.actor_user_id == self.auth.user_id,
            MasterImportBatch.request_key == command.request_id))
        if batch is not None:
            await self._recheck_result(batch.result)
        projected = await self.preview(command)
        if command.expected_digest != projected['source_digest']:
            raise HTTPException(409, 'CSV or audience configuration changed; preview again')
        if batch is not None and batch.request_digest != projected['source_digest']:
            raise HTTPException(409, 'Request ID already used with a different import')
        if projected['errors']:
            raise HTTPException(422, detail=projected['errors'])
        if batch is not None:
            return batch.result
        await require_counts(self.db, self.auth.organization_id,
                             properties=projected['counts']['properties'],
                             units=projected['counts']['units'])
        identities, properties = {}, {}
        for row in projected['rows']:
            if row['kind'] != 'property':
                continue
            values = row['values']
            body = PropertyCreate(**{key: values[key] for key in KIND_FIELDS['property']},
                                  data_audience=command.data_audience)
            # Each canonical property gets its own deterministic retry identity.
            entity = await create_property(self.db, self.auth, body,
                uuid.uuid5(command.request_id, f"property:{row['key']}"))
            properties[row['key']] = entity.id
            identities[row['line']] = entity.id
        for row in projected['rows']:
            values = row['values']
            if row['kind'] == 'unit':
                reference = values['property_key']
                property_id = properties.get(reference)
                if property_id is None:
                    property_id = uuid.UUID(reference)
                body = UnitCreate(property_id=str(property_id), number=values['number'],
                    area=values['area'] or None, heated_area=values['heated_area'] or None,
                    usage_type=values['usage_type'])
                entity = await create_unit(self.db, self.auth, body)
            elif row['kind'] == 'person':
                fields = {key: values[key] for key in KIND_FIELDS['person']}
                for key in ('company_name', 'email', 'phone'):
                    fields[key] = fields[key] or None
                entity = await create_person(self.db, self.auth, PersonCreate(**fields))
            else:
                continue
            identities[row['line']] = entity.id
        result = dict(rows=[dict(line=row['line'], kind=row['kind'], key=row['key'],
                                 id=str(identities[row['line']])) for row in projected['rows']],
                      counts=projected['counts'])
        await self._recheck_result(result)
        self.db.add(MasterImportBatch(organization_id=self.auth.organization_id,
            actor_user_id=self.auth.user_id, request_key=command.request_id,
            request_digest=projected['source_digest'], result=result))
        await self.db.flush()
        return result
