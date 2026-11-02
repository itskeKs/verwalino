"""Owners-meeting (Eigentümerversammlung) & Beschluss-Sammlung API endpoints -- M8.

RBAC (like M0-M7): GET for all roles; POST/PUT/DELETE require a write role.
Foreign ID -> 404 (not 403). No hard-delete of register entries or HELD meetings.
PDF: Niederschrift (meeting protocol) and Beschluss-Sammlung (register extract).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import AuthContext, get_current_user, require_write_dep
from app.core.live_principal import get_live_member_db, get_live_writer_db
from app.repositories.agenda_item_repo import AgendaItemRepository
from app.repositories.owners_meeting_repo import OwnersMeetingRepository
from app.repositories.register_entry_repo import RegisterEntryRepository
from app.repositories.resolution_repo import ResolutionRepository
from app.services.meeting_pdf import (
    MeetingDisplayNames,
    render_niederschrift_pdf,
    render_register_pdf,
    render_invitation_pdf,
)
from app.services.meeting_service import MeetingService, invitation_short_notice

router = APIRouter(prefix="/api/meetings", tags=["meetings"])


# ===========================================================================
# Schemas
# ===========================================================================
class MeetingCreate(BaseModel):
    property_id: str
    meeting_date: date
    meeting_type: str
    location: str = ""
    invitation_text: str | None = None


class MeetingUpdate(BaseModel):
    meeting_date: date | None = None
    location: str | None = None
    meeting_type: str | None = None
    invitation_text: str | None = None
    protocol_text: str | None = None


class MeetingOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    meeting_date: date
    location: str
    meeting_type: str
    status: str
    invitation_text: str | None
    protocol_text: str | None
    invitation_short_notice: bool

    model_config = {"from_attributes": True}


class AgendaItemCreate(BaseModel):
    meeting_id: str
    title: str
    description: str | None = None


class AgendaItemUpdate(BaseModel):
    title: str | None = None
    description: str | None = None


class AgendaItemOut(BaseModel):
    id: str
    organization_id: str
    meeting_id: str
    item_number: int
    title: str
    description: str | None

    model_config = {"from_attributes": True}


class ResolutionCreate(BaseModel):
    meeting_id: str
    wording: str
    votes_yes: int
    votes_no: int
    votes_abstain: int
    result: str
    agenda_item_id: str | None = None
    votes_yes_mea: str | None = None
    votes_no_mea: str | None = None
    votes_abstain_mea: str | None = None
    majority_type: str | None = None


class ResolutionOut(BaseModel):
    id: str
    organization_id: str
    meeting_id: str
    agenda_item_id: str | None
    wording: str
    votes_yes: int
    votes_no: int
    votes_abstain: int
    votes_yes_mea: str | None
    votes_no_mea: str | None
    votes_abstain_mea: str | None
    result: str
    majority_type: str | None

    model_config = {"from_attributes": True}


class RegisterEntryOut(BaseModel):
    id: str
    organization_id: str
    property_id: str
    sequence_number: int
    entry_date: date
    location: str
    wording: str
    resolution_id: str
    result: str | None
    challenge_annotation: str | None

    model_config = {"from_attributes": True}


class ChallengeAnnotation(BaseModel):
    annotation: str | None


# ===========================================================================
# Converters
# ===========================================================================
def _meeting_out(m) -> MeetingOut:
    return MeetingOut(
        id=str(m.id),
        organization_id=str(m.organization_id),
        property_id=str(m.property_id),
        meeting_date=m.meeting_date,
        location=m.location,
        meeting_type=m.meeting_type,
        status=m.status,
        invitation_text=m.invitation_text,
        protocol_text=m.protocol_text,
        invitation_short_notice=invitation_short_notice(m.meeting_date),
    )


def _agenda_out(a) -> AgendaItemOut:
    return AgendaItemOut(
        id=str(a.id),
        organization_id=str(a.organization_id),
        meeting_id=str(a.meeting_id),
        item_number=a.item_number,
        title=a.title,
        description=a.description,
    )


def _resolution_out(r) -> ResolutionOut:
    return ResolutionOut(
        id=str(r.id),
        organization_id=str(r.organization_id),
        meeting_id=str(r.meeting_id),
        agenda_item_id=str(r.agenda_item_id) if r.agenda_item_id else None,
        wording=r.wording,
        votes_yes=r.votes_yes,
        votes_no=r.votes_no,
        votes_abstain=r.votes_abstain,
        votes_yes_mea=str(r.votes_yes_mea) if r.votes_yes_mea is not None else None,
        votes_no_mea=str(r.votes_no_mea) if r.votes_no_mea is not None else None,
        votes_abstain_mea=str(r.votes_abstain_mea) if r.votes_abstain_mea is not None else None,
        result=r.result,
        majority_type=r.majority_type,
    )


def _entry_out(e, result) -> RegisterEntryOut:
    return RegisterEntryOut(
        id=str(e.id),
        organization_id=str(e.organization_id),
        property_id=str(e.property_id),
        sequence_number=e.sequence_number,
        entry_date=e.entry_date,
        location=e.location,
        wording=e.wording,
        resolution_id=str(e.resolution_id),
        result=result,
        challenge_annotation=e.challenge_annotation,
    )


async def _entries_out(db, org_id, entries):
    results = await RegisterEntryRepository(db, org_id).resolution_results(entries)
    return [_entry_out(entry, results.get(entry.resolution_id)) for entry in entries]


# ===========================================================================
# Meeting CRUD
# ===========================================================================
@router.get("", response_model=list[MeetingOut])
async def list_meetings(
    property_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = OwnersMeetingRepository(db, auth.organization_id)
    if property_id:
        items = await repo.list_for_property(property_id)
    else:
        items = await repo.list_all_ordered()
    return [_meeting_out(m) for m in items]


@router.get("/{meeting_id}", response_model=MeetingOut)
async def get_meeting(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = OwnersMeetingRepository(db, auth.organization_id)
    m = await repo.get_by_id(meeting_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return _meeting_out(m)


@router.post("", response_model=MeetingOut, status_code=201)
async def create_meeting(
    body: MeetingCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    m = await svc.create_meeting(
        property_id=uuid.UUID(body.property_id),
        meeting_date=body.meeting_date,
        meeting_type=body.meeting_type,
        location=body.location,
        invitation_text=body.invitation_text,
    )
    return _meeting_out(m)


@router.put("/{meeting_id}", response_model=MeetingOut)
async def update_meeting(
    meeting_id: uuid.UUID,
    body: MeetingUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    # Convert None fields to sentinel for optional update
    m = await svc.update_meeting(
        meeting_id,
        meeting_date=body.meeting_date,
        location=body.location,
        meeting_type=body.meeting_type,
        invitation_text=body.invitation_text if body.invitation_text is not None else ...,
        protocol_text=body.protocol_text if body.protocol_text is not None else ...,
    )
    return _meeting_out(m)


@router.post("/{meeting_id}/hold", response_model=MeetingOut)
async def mark_meeting_held(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    m = await svc.mark_held(meeting_id)
    return _meeting_out(m)


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.delete_meeting(meeting_id)


# ===========================================================================
# Agenda items (TOPs)
# ===========================================================================
@router.get("/{meeting_id}/agenda", response_model=list[AgendaItemOut])
async def list_agenda_items(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = AgendaItemRepository(db, auth.organization_id)
    items = await repo.list_for_meeting(meeting_id)
    return [_agenda_out(a) for a in items]


@router.post("/{meeting_id}/agenda", response_model=AgendaItemOut, status_code=201)
async def create_agenda_item(
    meeting_id: uuid.UUID,
    body: AgendaItemCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    item = await svc.create_agenda_item(
        meeting_id=meeting_id,
        title=body.title,
        description=body.description,
    )
    return _agenda_out(item)


@router.put("/agenda/{item_id}", response_model=AgendaItemOut)
async def update_agenda_item(
    item_id: uuid.UUID,
    body: AgendaItemUpdate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    item = await svc.update_agenda_item(
        item_id,
        title=body.title,
        description=body.description if body.description is not None else ...,
    )
    return _agenda_out(item)


@router.delete("/agenda/{item_id}", status_code=204)
async def delete_agenda_item(
    item_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.delete_agenda_item(item_id)


# ===========================================================================
# Resolutions (Beschlüsse)
# ===========================================================================
@router.get("/{meeting_id}/resolutions", response_model=list[ResolutionOut])
async def list_resolutions(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = ResolutionRepository(db, auth.organization_id)
    items = await repo.list_for_meeting(meeting_id)
    return [_resolution_out(r) for r in items]


@router.post("/resolutions", response_model=ResolutionOut, status_code=201)
async def create_resolution(
    body: ResolutionCreate,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    r = await svc.create_resolution(
        meeting_id=uuid.UUID(body.meeting_id),
        wording=body.wording,
        votes_yes=body.votes_yes,
        votes_no=body.votes_no,
        votes_abstain=body.votes_abstain,
        result=body.result,
        agenda_item_id=uuid.UUID(body.agenda_item_id) if body.agenda_item_id else None,
        votes_yes_mea=body.votes_yes_mea,
        votes_no_mea=body.votes_no_mea,
        votes_abstain_mea=body.votes_abstain_mea,
        majority_type=body.majority_type,
    )
    return _resolution_out(r)


@router.delete("/resolutions/{resolution_id}", status_code=204)
async def delete_resolution(
    resolution_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.delete_resolution(resolution_id)


# ===========================================================================
# Register entry (Beschluss-Sammlung)
# ===========================================================================
@router.post(
    "/resolutions/{resolution_id}/register",
    response_model=RegisterEntryOut,
    status_code=201,
)
async def enter_into_register(
    resolution_id: uuid.UUID,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    entry = await svc.enter_into_register(resolution_id)
    return (await _entries_out(db, auth.organization_id, [entry]))[0]


@router.get("/register/{property_id}", response_model=list[RegisterEntryOut])
async def list_register_entries(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    repo = RegisterEntryRepository(db, auth.organization_id)
    entries = await repo.list_for_property(property_id)
    return await _entries_out(db, auth.organization_id, entries)


@router.put("/register/entry/{entry_id}/challenge", response_model=RegisterEntryOut)
async def annotate_challenge(
    entry_id: uuid.UUID,
    body: ChallengeAnnotation,
    auth: AuthContext = Depends(require_write_dep),
    db: AsyncSession = Depends(get_live_writer_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    entry = await svc.annotate_challenge(entry_id, body.annotation)
    return (await _entries_out(db, auth.organization_id, [entry]))[0]


# ===========================================================================
# PDFs
# ===========================================================================
@router.get("/{meeting_id}/invitation.pdf")
async def meeting_invitation_pdf(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.require_source_access("owners_meeting", meeting_id, "export")
    meeting = await OwnersMeetingRepository(db, auth.organization_id).get_by_id(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")
    recipients = await svc.invitation_recipients(meeting)
    agenda = list(await AgendaItemRepository(db, auth.organization_id).list_for_meeting(meeting_id))
    prop_name, prop_address = await svc.resolve_property_display(meeting.property_id)
    names = MeetingDisplayNames(property_name=prop_name, property_address=prop_address)
    await svc.require_source_access("owners_meeting", meeting_id, "export")
    try:
        pdf = render_invitation_pdf(
            meeting, agenda, recipients, names, invitation_short_notice(meeting.meeting_date),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await svc.require_source_access("owners_meeting", meeting_id, "export")
    # Contact export rights can be revoked while the PDF is being rendered too.
    await svc.invitation_recipients(meeting)
    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="einladung_{meeting_id}.pdf"',
    })


@router.get("/{meeting_id}/pdf")
async def meeting_protocol_pdf(
    meeting_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    """Render the Niederschrift (minutes) for a meeting as PDF."""
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.require_source_access('owners_meeting', meeting_id, 'export')
    meeting_repo = OwnersMeetingRepository(db, auth.organization_id)
    meeting = await meeting_repo.get_by_id(meeting_id)
    if meeting is None:
        raise HTTPException(status_code=404, detail="Meeting not found")

    agenda_repo = AgendaItemRepository(db, auth.organization_id)
    resolution_repo = ResolutionRepository(db, auth.organization_id)

    agenda_items = list(await agenda_repo.list_for_meeting(meeting_id))
    resolutions = list(await resolution_repo.list_for_meeting(meeting_id))

    prop_name, prop_addr = await svc.resolve_property_display(meeting.property_id)
    names = MeetingDisplayNames(property_name=prop_name, property_address=prop_addr)

    await svc.require_source_access('owners_meeting', meeting_id, 'export')
    pdf_bytes = render_niederschrift_pdf(meeting, agenda_items, resolutions, names)
    await svc.require_source_access('owners_meeting', meeting_id, 'export')
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="niederschrift_{meeting_id}.pdf"'
        },
    )


@router.get("/register/{property_id}/pdf")
async def register_pdf(
    property_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    """Render the Beschluss-Sammlung for a WEG property as PDF."""
    svc = MeetingService(db, auth.organization_id, auth.user_id)
    await svc.require_source_access('property', property_id, 'export')
    register_repo = RegisterEntryRepository(db, auth.organization_id)

    entries = list(await register_repo.list_for_property(property_id))
    prop_name, prop_addr = await svc.resolve_property_display(property_id)
    names = MeetingDisplayNames(property_name=prop_name, property_address=prop_addr)

    await svc.require_source_access('property', property_id, 'export')
    pdf_bytes = render_register_pdf(entries, names)
    await svc.require_source_access('property', property_id, 'export')
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="beschluss_sammlung_{property_id}.pdf"'
        },
    )
