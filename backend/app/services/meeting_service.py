"""Owners-meeting service -- create/update/mark-held meetings, TOPs,
resolutions, and register entries (Beschluss-Sammlung) for M8.

Boundary discipline (SDD section 16): the service loads ORM rows,
applies business rules, and persists. No pure engine needed here --
M8 is primarily CRUD + an append-only register with sequencing.

Lifecycle:
  Meeting: PLANNED -> HELD (one-way; a HELD meeting cannot revert).
  Register entry: immutable from INSERT (ADR-0011 trigger).

[section 18/fachlich] Ladungsfristen, Stimmrechtsausschlüsse, and automatic
majority enforcement are NOT implemented. The result (ANGENOMMEN/ABGELEHNT)
is the Verwalter's determination.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agenda_item import AgendaItem
from app.models.audit_event import AuditEvent
from app.models.owners_meeting import OwnersMeeting
from app.models.resolution import Resolution
from app.models.resolution_register_entry import ResolutionRegisterEntry

from app.repositories.agenda_item_repo import AgendaItemRepository
from app.repositories.audit_repo import AuditRepository
from app.repositories.owners_meeting_repo import OwnersMeetingRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.register_entry_repo import RegisterEntryRepository
from app.repositories.resolution_repo import ResolutionRepository


class MeetingService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID):
        self._session = session
        self._org_id = org_id
        self._user_id = user_id
        self._meetings = OwnersMeetingRepository(session, org_id)
        self._agenda = AgendaItemRepository(session, org_id)
        self._resolutions = ResolutionRepository(session, org_id)
        self._register = RegisterEntryRepository(session, org_id)
        self._properties = PropertyRepository(session, org_id)
        self._persons = PersonRepository(session, org_id)
        self._audit = AuditRepository(session, org_id)

    # ------------------------------------------------------------------
    # Meeting CRUD
    # ------------------------------------------------------------------
    async def create_meeting(
        self,
        property_id: uuid.UUID,
        meeting_date: date,
        meeting_type: str,
        location: str = "",
        invitation_text: str | None = None,
    ) -> OwnersMeeting:
        prop = await self._properties.get_by_id(property_id)
        if prop is None:
            raise HTTPException(status_code=404, detail="Property not found")

        meeting = OwnersMeeting(
            organization_id=self._org_id,
            property_id=property_id,
            meeting_date=meeting_date,
            meeting_type=meeting_type,
            location=location,
            status="PLANNED",
            invitation_text=invitation_text,
        )
        meeting = await self._meetings.create(meeting)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="owners_meeting",
            entity_id=meeting.id,
            action="owners_meeting.created",
            payload_diff={
                "property_id": str(property_id),
                "meeting_date": str(meeting_date),
                "meeting_type": meeting_type,
                "status": "PLANNED",
            },
        ))
        return meeting

    async def update_meeting(
        self,
        meeting_id: uuid.UUID,
        *,
        meeting_date: date | None = None,
        location: str | None = None,
        meeting_type: str | None = None,
        invitation_text: str | None = ...,  # type: ignore[assignment]
        protocol_text: str | None = ...,  # type: ignore[assignment]
    ) -> OwnersMeeting:
        meeting = await self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # HELD meetings: only protocol_text can still be updated
        changes: dict = {}
        if meeting.status == "HELD":
            if any(v is not None for v in [meeting_date, location, meeting_type]):
                raise HTTPException(
                    status_code=409,
                    detail="Cannot change date/location/type of a HELD meeting",
                )
            if protocol_text is not ...:
                meeting.protocol_text = protocol_text
                changes["protocol_text"] = "(updated)"
        else:
            if meeting_date is not None:
                meeting.meeting_date = meeting_date
                changes["meeting_date"] = str(meeting_date)
            if location is not None:
                meeting.location = location
                changes["location"] = location
            if meeting_type is not None:
                meeting.meeting_type = meeting_type
                changes["meeting_type"] = meeting_type
            if invitation_text is not ...:
                meeting.invitation_text = invitation_text
                changes["invitation_text"] = "(updated)"
            if protocol_text is not ...:
                meeting.protocol_text = protocol_text
                changes["protocol_text"] = "(updated)"

        meeting = await self._meetings.update(meeting)

        if changes:
            await self._audit.create(AuditEvent(
                actor_user_id=self._user_id,
                entity_type="owners_meeting",
                entity_id=meeting.id,
                action="owners_meeting.updated",
                payload_diff=changes,
            ))
        return meeting

    async def mark_held(self, meeting_id: uuid.UUID) -> OwnersMeeting:
        """Transition a PLANNED meeting to HELD (one-way)."""
        meeting = await self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if meeting.status == "HELD":
            raise HTTPException(status_code=409, detail="Meeting is already HELD")

        meeting.status = "HELD"
        meeting = await self._meetings.update(meeting)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="owners_meeting",
            entity_id=meeting.id,
            action="owners_meeting.held",
            payload_diff={"status": "HELD"},
        ))
        return meeting

    async def delete_meeting(self, meeting_id: uuid.UUID) -> None:
        """Delete a PLANNED meeting. HELD meetings cannot be deleted."""
        meeting = await self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")
        if meeting.status == "HELD":
            raise HTTPException(status_code=409, detail="Cannot delete a HELD meeting")
        await self._meetings.delete(meeting)
        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="owners_meeting",
            entity_id=meeting_id,
            action="owners_meeting.deleted",
            payload_diff={"status": "PLANNED"},
        ))

    # ------------------------------------------------------------------
    # Agenda items (TOPs)
    # ------------------------------------------------------------------
    async def create_agenda_item(
        self,
        meeting_id: uuid.UUID,
        title: str,
        description: str | None = None,
    ) -> AgendaItem:
        meeting = await self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        item_number = await self._agenda.next_item_number(meeting_id)
        item = AgendaItem(
            organization_id=self._org_id,
            meeting_id=meeting_id,
            item_number=item_number,
            title=title,
            description=description,
        )
        item = await self._agenda.create(item)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="agenda_item",
            entity_id=item.id,
            action="agenda_item.created",
            payload_diff={
                "meeting_id": str(meeting_id),
                "item_number": item_number,
                "title": title,
            },
        ))
        return item

    async def update_agenda_item(
        self,
        item_id: uuid.UUID,
        *,
        title: str | None = None,
        description: str | None = ...,  # type: ignore[assignment]
    ) -> AgendaItem:
        item = await self._agenda.get_by_id(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")

        changes: dict = {}
        if title is not None:
            item.title = title
            changes["title"] = title
        if description is not ...:
            item.description = description
            changes["description"] = "(updated)"

        item = await self._agenda.update(item)

        if changes:
            await self._audit.create(AuditEvent(
                actor_user_id=self._user_id,
                entity_type="agenda_item",
                entity_id=item.id,
                action="agenda_item.updated",
                payload_diff=changes,
            ))
        return item

    async def delete_agenda_item(self, item_id: uuid.UUID) -> None:
        item = await self._agenda.get_by_id(item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Agenda item not found")
        await self._agenda.delete(item)
        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="agenda_item",
            entity_id=item_id,
            action="agenda_item.deleted",
            payload_diff={"item_number": item.item_number},
        ))

    # ------------------------------------------------------------------
    # Resolutions (Beschlüsse)
    # ------------------------------------------------------------------
    async def create_resolution(
        self,
        meeting_id: uuid.UUID,
        wording: str,
        votes_yes: int,
        votes_no: int,
        votes_abstain: int,
        result: str,
        *,
        agenda_item_id: uuid.UUID | None = None,
        votes_yes_mea: str | None = None,
        votes_no_mea: str | None = None,
        votes_abstain_mea: str | None = None,
        majority_type: str | None = None,
    ) -> Resolution:
        meeting = await self._meetings.get_by_id(meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        if agenda_item_id is not None:
            ai = await self._agenda.get_by_id(agenda_item_id)
            if ai is None:
                raise HTTPException(status_code=404, detail="Agenda item not found")
            if ai.meeting_id != meeting_id:
                raise HTTPException(
                    status_code=422,
                    detail="Agenda item does not belong to this meeting",
                )

        from decimal import Decimal
        resolution = Resolution(
            organization_id=self._org_id,
            meeting_id=meeting_id,
            agenda_item_id=agenda_item_id,
            wording=wording,
            votes_yes=votes_yes,
            votes_no=votes_no,
            votes_abstain=votes_abstain,
            votes_yes_mea=Decimal(votes_yes_mea) if votes_yes_mea else None,
            votes_no_mea=Decimal(votes_no_mea) if votes_no_mea else None,
            votes_abstain_mea=Decimal(votes_abstain_mea) if votes_abstain_mea else None,
            result=result,
            majority_type=majority_type,
        )
        resolution = await self._resolutions.create(resolution)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="resolution",
            entity_id=resolution.id,
            action="resolution.created",
            payload_diff={
                "meeting_id": str(meeting_id),
                "result": result,
                "votes_yes": votes_yes,
                "votes_no": votes_no,
                "votes_abstain": votes_abstain,
            },
        ))
        return resolution

    async def delete_resolution(self, resolution_id: uuid.UUID) -> None:
        """Delete a resolution. Fails if it has a register entry."""
        res = await self._resolutions.get_by_id(resolution_id)
        if res is None:
            raise HTTPException(status_code=404, detail="Resolution not found")

        # Check if already in register
        existing = await self._register.get_by_resolution_id(resolution_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a resolution that has been entered into the Beschluss-Sammlung",
            )

        await self._resolutions.delete(res)
        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="resolution",
            entity_id=resolution_id,
            action="resolution.deleted",
            payload_diff={"result": res.result},
        ))

    # ------------------------------------------------------------------
    # Register entries (Beschluss-Sammlung)
    # ------------------------------------------------------------------
    async def enter_into_register(
        self,
        resolution_id: uuid.UUID,
    ) -> ResolutionRegisterEntry:
        """Enter a resolution into the Beschluss-Sammlung.

        Assigns the next sequential number for the property. The UNIQUE
        constraint prevents duplicates; on collision the caller must retry.
        """
        resolution = await self._resolutions.get_by_id(resolution_id)
        if resolution is None:
            raise HTTPException(status_code=404, detail="Resolution not found")

        # Check for duplicate entry
        existing = await self._register.get_by_resolution_id(resolution_id)
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="Resolution is already in the Beschluss-Sammlung",
            )

        # Resolve meeting for property_id and location
        meeting = await self._meetings.get_by_id(resolution.meeting_id)
        if meeting is None:
            raise HTTPException(status_code=404, detail="Meeting not found")

        # Next sequence number (within the same transaction)
        seq = await self._register.next_sequence_number(meeting.property_id)

        entry = ResolutionRegisterEntry(
            organization_id=self._org_id,
            property_id=meeting.property_id,
            sequence_number=seq,
            entry_date=date.today(),
            location=meeting.location,
            wording=resolution.wording,  # COPY, immutable
            resolution_id=resolution.id,
        )
        entry = await self._register.create(entry)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="resolution_register_entry",
            entity_id=entry.id,
            action="register_entry.created",
            payload_diff={
                "property_id": str(meeting.property_id),
                "sequence_number": seq,
                "resolution_id": str(resolution.id),
            },
        ))
        return entry

    async def annotate_challenge(
        self,
        entry_id: uuid.UUID,
        annotation: str | None,
    ) -> ResolutionRegisterEntry:
        """Set or update the Anfechtungs-/Aufhebungsvermerk.

        This is the ONLY allowed mutation on a register entry (ADR-0011).
        """
        entry = await self._register.get_by_id(entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Register entry not found")

        old_annotation = entry.challenge_annotation
        entry.challenge_annotation = annotation
        entry = await self._register.update(entry)

        await self._audit.create(AuditEvent(
            actor_user_id=self._user_id,
            entity_type="resolution_register_entry",
            entity_id=entry.id,
            action="register_entry.challenge_annotated",
            payload_diff={
                "old_annotation": old_annotation,
                "new_annotation": annotation,
            },
        ))
        return entry

    # ------------------------------------------------------------------
    # Name resolution for PDF
    # ------------------------------------------------------------------
    async def resolve_property_display(self, property_id: uuid.UUID) -> tuple[str, str]:
        """Return (name, address) for a property."""
        prop = await self._properties.get_by_id(property_id)
        if prop is None:
            return ("---", "")
        name = prop.name or "---"
        parts = []
        if prop.street:
            parts.append(prop.street)
        if prop.zip_code or prop.city:
            parts.append(f"{prop.zip_code} {prop.city}".strip())
        return (name, ", ".join(parts))
