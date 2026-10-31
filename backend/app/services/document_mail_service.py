"""Source-authorized document mail with durable append-only submission attempts."""
from __future__ import annotations

import asyncio
import smtplib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_tenant_session
from app.core.live_principal import require_live_principal
from app.core.rbac import ScopeCapability
from app.core.storage import compute_sha256, get_store, put_document, retain_document
from app.models.audit_event import AuditEvent
from app.repositories.audit_repo import AuditRepository
from app.repositories.dunning_notice_repo import DunningNoticeRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.owner_profile_repo import OwnerProfileRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.owners_meeting_repo import OwnersMeetingRepository
from app.repositories.ownership_history_repo import OwnershipHistoryRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.agenda_item_repo import AgendaItemRepository
from app.repositories.settlement_repo import SettlementRunRepository, SettlementLineRepository
from app.services.authorization_service import AuthorizationService
from app.services.dunning_service import DunningService
from app.services.dunning_scope_service import DunningScopeService
from app.services.dunning_pdf import render_dunning_pdf
from app.services.financial_scope_service import FinancialScopeService
from app.services.render_names import settlement_render_names
from app.services.settlement_pdf import render_settlement_pdf
from app.services.meeting_service import MeetingService, invitation_short_notice
from app.services.meeting_pdf import MeetingDisplayNames, render_invitation_pdf
from app.services.mail_service import send_pdf, validate_configuration, validate_address, MailConfigurationError


ENTITY = {"dunning": "dunning_notice", "settlement": "settlement_run", "invitation": "owners_meeting"}
SUBJECT = {"dunning": "Mahnung", "settlement": "Betriebskostenabrechnung", "invitation": "Einladung zur Eigentümerversammlung"}


@dataclass
class MailDocument:
    source: object
    recipients: list


class DocumentMailService:
    def __init__(self, db, auth):
        self.db, self.auth = db, auth
        self.org = auth.organization_id

    async def _person(self, profile_kind, profile_id):
        allowed = await self.db.scalar(text("SELECT profile_source_access(:org,:kind,:id,'export')"),
            {"org": self.org, "kind": profile_kind, "id": profile_id})
        repo = TenantProfileRepository if profile_kind == "tenant_profile" else OwnerProfileRepository
        profile = await repo(self.db, self.org).get_by_id(profile_id) if allowed else None
        person = await PersonRepository(self.db, self.org).get_by_id(profile.person_id) if profile else None
        if person is None:
            raise HTTPException(404, "Empfänger nicht zugänglich")
        await self.db.execute(text('SELECT id FROM person WHERE organization_id=:org AND id=:id FOR SHARE'),
                              {"org": self.org, "id": person.id})
        await self.db.refresh(person)
        if person.anonymized_at or person.processing_restricted or person.deletion_requested_at:
            raise HTTPException(409, "Kontaktverarbeitung ist eingeschränkt")
        return person

    async def resolve(self, kind, document_id):
        if kind not in ENTITY:
            raise HTTPException(404, "Dokumentart nicht gefunden")
        # Same lock order as source mutations: live ACL, financial inputs, source row.
        await require_live_principal(self.db, self.auth, writer=False)
        await FinancialScopeService(self.db, self.org, self.auth.user_id).lock_inputs()
        await self.db.execute(text(f'SELECT id FROM {ENTITY[kind]} WHERE organization_id=:org AND id=:id FOR SHARE'),
                              {"org": self.org, "id": document_id})
        if kind == "dunning":
            source = await DunningNoticeRepository(self.db, self.org).get_by_id(document_id)
            if source is None:
                raise HTTPException(404, "Mahnung nicht gefunden")
            await DunningScopeService(self.db, self.org, self.auth.user_id).enter(source, ScopeCapability.EXPORT)
            if source.status != "issued" or source.cancelled_at:
                raise HTTPException(409, "Nur ausgestellte Mahnungen können versendet werden")
            person = await self._person("tenant_profile" if source.tenant_profile_id else "owner_profile",
                                        source.tenant_profile_id or source.owner_profile_id)
            people = [person]
        elif kind == "settlement":
            source = await SettlementRunRepository(self.db, self.org).get_by_id(document_id)
            if source is None:
                raise HTTPException(404, "Abrechnung nicht gefunden")
            await FinancialScopeService(self.db, self.org, self.auth.user_id).require_export(source)
            if source.status != "finalized":
                raise HTTPException(409, "Nur finalisierte Abrechnungen können versendet werden")
            if not source.tenant_profile_id:
                raise HTTPException(422, "Abrechnung hat keinen Mieterempfänger")
            people = [await self._person("tenant_profile", source.tenant_profile_id)]
        else:
            svc = MeetingService(self.db, self.org, self.auth.user_id)
            await svc.require_source_access("owners_meeting", document_id, "export")
            source = await OwnersMeetingRepository(self.db, self.org).get_by_id(document_id)
            if source is None:
                raise HTTPException(404, "Versammlung nicht gefunden")
            await svc.invitation_recipients(source)
            owner_ids = set()
            for unit in await UnitRepository(self.db, self.org).list_by_property(source.property_id):
                for history in await OwnershipHistoryRepository(self.db, self.org).owners_at_date(unit.id, source.meeting_date):
                    owner_ids.add(history.owner_profile_id)
            people = [await self._person("owner_profile", owner) for owner in sorted(owner_ids)]
        return MailDocument(source, list({p.id: p for p in people}.values()))

    @staticmethod
    def recipient(person):
        return {"person_id": str(person.id), "name": person.company_name or " ".join(filter(None, [person.first_name, person.last_name])),
                "email": person.email}

    async def require_retained_contact(self, payload):
        allowed = await self.db.scalar(text("SELECT person_source_access(:org,:person,'export')"),
            {"org": self.org, "person": uuid.UUID(payload["person_id"])})
        if not allowed:
            raise HTTPException(404, "Ursprünglicher Versandkontakt nicht zugänglich")

    async def attempts(self, kind, document_id):
        events = await AuditRepository(self.db, self.org).list_for_entity(ENTITY[kind], document_id)
        attempts = {}
        for event in events:
            if event.action not in {"mail_prepared", "mail_completed"}:
                continue
            payload = event.payload_diff or {}
            if event.action == "mail_prepared":
                await self.require_retained_contact(payload)
                attempts[payload["id"]] = {key: payload[key] for key in (
                    "id", "person_id", "email", "status", "created_at", "completed_at")}
            elif payload.get("id") in attempts:
                attempts[payload["id"]].update(status=payload["status"], completed_at=payload["completed_at"])
        return list(attempts.values())

    async def info(self, kind, document_id):
        document = await self.resolve(kind, document_id)
        return {"recipients": [self.recipient(p) for p in document.recipients],
                "attempts": await self.attempts(kind, document_id)}

    async def render(self, kind, document, person):
        source = document.source
        if kind == "dunning":
            names = await DunningService(self.db, self.org, self.auth.user_id).render_display_names(source)
            return render_dunning_pdf(source, names)
        if kind == "settlement":
            lines = await SettlementLineRepository(self.db, self.org).list_for_run(source.id)
            names = await settlement_render_names(self.db, self.org, source)
            return render_settlement_pdf(source, list(lines), names)
        svc = MeetingService(self.db, self.org, self.auth.user_id)
        agenda = await AgendaItemRepository(self.db, self.org).list_for_meeting(source.id)
        name, address = await svc.resolve_property_display(source.property_id)
        recipient = (self.recipient(person)["name"], person.street, f"{person.zip_code} {person.city}")
        try:
            return render_invitation_pdf(source, list(agenda), [recipient], MeetingDisplayNames(name, address),
                                         invitation_short_notice(source.meeting_date))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    async def retained_pdf(self, kind, document_id, attempt_id):
        await self.resolve(kind, document_id)
        events = await AuditRepository(self.db, self.org).list_for_entity(ENTITY[kind], document_id)
        event = next((e for e in events if e.action == "mail_prepared"
                      and (e.payload_diff or {}).get("id") == str(attempt_id)), None)
        if event is None:
            raise HTTPException(404, "Versandversuch nicht gefunden")
        payload = event.payload_diff
        await self.require_retained_contact(payload)
        try:
            pdf = get_store().get(payload["storage_uri"])
        except FileNotFoundError as exc:
            raise HTTPException(409, "Aufbewahrtes Versand-PDF fehlt") from exc
        if compute_sha256(pdf) != payload["content_sha256"]:
            raise HTTPException(409, "Integritätsprüfung des Versand-PDF fehlgeschlagen")
        return pdf

    async def record(self, kind, document_id, action, payload):
        # Commit before SMTP. Request rollback/crash must not erase a possible submission.
        async with get_tenant_session(self.org) as session:
            await session.execute(text("SELECT set_config('app.current_user_id',:actor,true)"), {"actor": str(self.auth.user_id)})
            await AuditRepository(session, self.org).create(AuditEvent(
                actor_user_id=self.auth.user_id, entity_type=ENTITY[kind], entity_id=document_id,
                action=action, payload_diff=payload))

    async def send(self, kind, document_id, person_id, request_id):
        await require_live_principal(self.db, self.auth, writer=True)
        document = await self.resolve(kind, document_id)
        if kind == "dunning":
            context = await DunningScopeService(self.db, self.org, self.auth.user_id).enter(document.source, ScopeCapability.EXPORT)
            if context is not None:
                await AuthorizationService(self.db, self.auth).require(context.scope_id, ScopeCapability.SEND)
        elif kind == "settlement":
            await FinancialScopeService(self.db, self.org, self.auth.user_id).enter(document.source, capability=ScopeCapability.SEND)
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                              {"key": f"mail:{self.org}:{request_id}"})
        # Scope the key to this source/recipient; a replay can never send a second message.
        for attempt in await self.attempts(kind, document_id):
            if attempt["id"] == str(request_id):
                if attempt["person_id"] != str(person_id):
                    raise HTTPException(409, "Wiederholungsschlüssel gehört zu einem anderen Empfänger")
                return attempt
        person = next((p for p in document.recipients if p.id == person_id), None)
        if person is None:
            raise HTTPException(404, "Empfänger gehört nicht zum Dokument")
        try:
            validate_configuration(settings)
        except MailConfigurationError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            email = validate_address(person.email)
        except MailConfigurationError as exc:
            raise HTTPException(422, str(exc)) from exc
        pdf = await self.render(kind, document, person)
        # Recheck after potentially expensive rendering while source/ACL locks remain held.
        await require_live_principal(self.db, self.auth, writer=True)
        await self.resolve(kind, document_id)
        digest = compute_sha256(pdf)
        stored = await put_document(self.db, self.org, pdf, content_type="application/pdf")
        created = datetime.now(timezone.utc).isoformat()
        message_id = f"<{request_id}@{settings.smtp_from.rsplit('@', 1)[1]}>"
        payload = {"id": str(request_id), "person_id": str(person_id), "email": email,
                   "status": "prepared", "created_at": created, "completed_at": None,
                   "content_sha256": digest, "storage_uri": stored.uri, "bytes": stored.byte_size, "message_id": message_id}
        # The receipt commits independently and can survive this request.
        # Preserve its original even if that commit's outcome is uncertain.
        retain_document(self.db, stored)
        await self.record(kind, document_id, "mail_prepared", payload)
        try:
            await asyncio.to_thread(send_pdf, settings, recipient=email, subject=SUBJECT[kind],
                body=f"Guten Tag,\n\nanbei erhalten Sie: {SUBJECT[kind]}.\n", pdf=pdf,
                filename=f"{kind}-{document_id}.pdf", message_id=message_id)
            status = "accepted"
        except (MailConfigurationError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError,
                smtplib.SMTPAuthenticationError, smtplib.SMTPConnectError, ConnectionRefusedError):
            status = "failed"
        except (smtplib.SMTPException, OSError):
            # A timeout/disconnection may occur after remote DATA acceptance.
            status = "unknown"
        completed = datetime.now(timezone.utc).isoformat()
        await self.record(kind, document_id, "mail_completed", {"id": str(request_id), "status": status, "completed_at": completed})
        return {key: (status if key == "status" else completed if key == "completed_at" else payload[key])
                for key in ("id", "person_id", "email", "status", "created_at", "completed_at")}
