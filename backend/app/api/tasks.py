"""CRUD for operational tasks (not retained financial documents)."""
import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import require_live_principal
from app.core.tenant import AuthContext, get_current_user, get_db, require_write_dep
from app.services.task_service import TaskService

router = APIRouter(prefix='/api/tasks', tags=['tasks'])


class TaskFields(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('title', check_fields=False)
    @classmethod
    def title_text(cls, value):
        if value is not None:
            value = value.strip()
            if not value or '\x00' in value:
                raise ValueError('Title must be nonblank and contain no NUL')
        return value

    @field_validator('note', check_fields=False)
    @classmethod
    def note_text(cls, value):
        if value is not None and '\x00' in value:
            raise ValueError('Note must contain no NUL')
        return value


class TaskCreate(TaskFields):
    title: str = Field(max_length=255)
    note: str = Field('', max_length=10000, description='Task note, at most 10000 characters; no NUL.')
    due_date: date | None = None
    completed: bool = False
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    lease_agreement_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None


class TaskPatch(TaskFields):
    title: str | None = Field(None, max_length=255)
    note: str | None = Field(None, max_length=10000, description='Task note, at most 10000 characters; no NUL.')
    due_date: date | None = None
    completed: bool | None = None
    property_id: uuid.UUID | None = None
    unit_id: uuid.UUID | None = None
    lease_agreement_id: uuid.UUID | None = None
    assigned_user_id: uuid.UUID | None = None

    @model_validator(mode='after')
    def nonnullable_fields(self):
        for key in ('title', 'note', 'completed', 'assigned_user_id'):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f'{key} cannot be null')
        return self


class TaskOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    created_by: uuid.UUID
    assigned_user_id: uuid.UUID
    property_id: uuid.UUID | None
    unit_id: uuid.UUID | None
    lease_agreement_id: uuid.UUID | None
    title: str
    note: str
    due_date: date | None
    completed: bool
    created_at: datetime
    updated_at: datetime
    property_name: str | None
    unit_number: str | None
    lease_label: str | None
    assigned_user_name: str | None


class TaskListOut(BaseModel):
    items: list[TaskOut]
    next_offset: int | None


async def task_reader_db(auth: AuthContext = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db, scope='function')):
    await require_live_principal(db, auth, writer=False)
    # The shared helper rejects missing principals; unknown live roles must not
    # become apparently successful empty task lists either.
    role = await db.scalar(text('SELECT scope_actor_role(:org)'), {'org': auth.organization_id})
    if role not in {'OWNER_ADMIN', 'CLERK', 'READ_ONLY'}:
        raise HTTPException(403, 'Organization member role required')
    return db


async def task_writer_db(auth: AuthContext = Depends(require_write_dep),
                         db: AsyncSession = Depends(get_db, scope='function')):
    await require_live_principal(db, auth, writer=True)
    return db


@router.post('', response_model=TaskOut, status_code=201)
async def create_task(body: TaskCreate, auth: AuthContext = Depends(require_write_dep),
                      db: AsyncSession = Depends(task_writer_db)):
    service = TaskService(db, auth.organization_id, auth.user_id)
    return await service.output(await service.create(body.model_dump()))


@router.get('', response_model=TaskListOut)
async def list_tasks(property_id: uuid.UUID | None = None, unit_id: uuid.UUID | None = None,
                     lease_agreement_id: uuid.UUID | None = None,
                     assigned_user_id: uuid.UUID | None = None, completed: bool | None = None,
                     due_until: date | None = None, offset: int = Query(0, ge=0),
                     limit: int = Query(50, ge=1, le=100),
                     auth: AuthContext = Depends(get_current_user),
                     db: AsyncSession = Depends(task_reader_db)):
    service = TaskService(db, auth.organization_id, auth.user_id)
    rows, next_offset = await service.list(property_id=property_id, unit_id=unit_id,
        lease_agreement_id=lease_agreement_id, assigned_user_id=assigned_user_id,
        completed=completed, due_until=due_until, offset=offset, limit=limit)
    return {'items': [await service.output(row) for row in rows], 'next_offset': next_offset}


@router.get('/{task_id}', response_model=TaskOut)
async def get_task(task_id: uuid.UUID, auth: AuthContext = Depends(get_current_user),
                   db: AsyncSession = Depends(task_reader_db)):
    service = TaskService(db, auth.organization_id, auth.user_id)
    return await service.output(await service.get(task_id))


@router.patch('/{task_id}', response_model=TaskOut)
async def patch_task(task_id: uuid.UUID, body: TaskPatch,
                     auth: AuthContext = Depends(require_write_dep),
                     db: AsyncSession = Depends(task_writer_db)):
    service = TaskService(db, auth.organization_id, auth.user_id)
    return await service.output(await service.update(task_id, body.model_dump(exclude_unset=True)))


@router.delete('/{task_id}', status_code=204)
async def delete_task(task_id: uuid.UUID, auth: AuthContext = Depends(require_write_dep),
                      db: AsyncSession = Depends(task_writer_db)):
    await TaskService(db, auth.organization_id, auth.user_id).delete(task_id)
    return Response(status_code=204)
