"""Safe plain-text placeholders and retained, live-authorized circular letters."""
from __future__ import annotations

import html
import re
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import text

from app.core.pdf import render_pdf
from app.core.storage import compute_sha256, get_store, make_storage_key
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.property_repo import PropertyRepository
from app.services.financial_scope_service import FinancialScopeService
from app.services.property_master_service import require_property_master

TOKENS = {"mieter.name", "mieter.adresse", "objekt.name", "objekt.adresse", "datum"}
PLACEHOLDER = re.compile(r"{{\s*([a-z.]+)\s*}}")


def validate_template(value: str) -> str:
    tokens = PLACEHOLDER.findall(value)
    remainder = PLACEHOLDER.sub("", value)
    if any(token not in TOKENS for token in tokens) or any(marker in remainder for marker in ("{{", "}}", "{%", "%}")):
        raise ValueError("Nur die angegebenen Textplatzhalter sind zulässig")
    if not value.strip():
        raise ValueError("Text darf nicht leer sein")
    return value


def address(entity):
    return f"{entity.street}\n{entity.zip_code} {entity.city}".strip()


def replace_tokens(template, values):
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], template)


def render_letter(name, postal_address, subject, body, issued_on):
    safe = lambda value: html.escape(value).replace("\n", "<br>")
    return render_pdf(f'''<!doctype html><html lang="de"><meta charset="utf-8">
    <style>@page {{size:A4;margin:25mm}} body {{font:11pt sans-serif;line-height:1.5}}
    h1 {{font-size:15pt}} .text {{overflow-wrap:anywhere}}</style>
    <p>{safe(name)}<br>{safe(postal_address)}</p><p>{issued_on.strftime('%d.%m.%Y')}</p>
    <h1>{safe(subject)}</h1><div class="text">{safe(body)}</div></html>''')


def document_out(row):
    return {key: row[key] for key in ("id", "request_id", "template_id", "property_id", "person_id", "recipient_name", "subject", "as_of", "created_at")} | {"pdf_url": f"/api/letters/documents/{row['id']}/pdf"}


class LetterService:
    def __init__(self, db, auth):
        self.db, self.auth, self.org = db, auth, auth.organization_id

    async def _lock(self):
        await FinancialScopeService(self.db, self.org, self.auth.user_id).lock_inputs()

    async def _person_processing(self, person_id):
        row = (await self.db.execute(text("""SELECT anonymized_at,processing_restricted,deletion_requested_at
            FROM person WHERE organization_id=:org AND id=:id FOR SHARE"""),
            {"org": self.org, "id": person_id})).mappings().first()
        if row is None:
            raise HTTPException(404, "Briefkontakt nicht zugänglich")
        if row["anonymized_at"] or row["processing_restricted"] or row["deletion_requested_at"]:
            raise HTTPException(409, "Kontaktverarbeitung ist eingeschränkt")

    async def _documents(self, where, params):
        return (await self.db.execute(text(f"""SELECT * FROM letter_document
            WHERE organization_id=:org AND {where} ORDER BY person_id,id"""),
            {"org": self.org, **params})).mappings().all()

    async def list_documents(self, property_id):
        await self._lock()
        await require_property_master(self.db, self.org, property_id, 'export')
        rows = await self._documents("property_id=:property", {"property": property_id})
        for row in rows:
            await self._person_processing(row["person_id"])
        return [document_out(row) for row in rows]

    async def issue(self, body):
        await self._lock()
        await self.db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                              {"key": f"letter:{self.org}:{body.request_id}"})
        template = (await self.db.execute(text("SELECT * FROM letter_template WHERE organization_id=:org AND id=:id FOR SHARE"),
                    {"org": self.org, "id": body.template_id})).mappings().first()
        if template is None:
            raise HTTPException(404, "Vorlage nicht zugänglich")
        if not await self.db.scalar(text("SELECT letter_request_access(:org,:request)"),
                                    {"org": self.org, "request": body.request_id}):
            raise HTTPException(404, "Ursprüngliche Briefempfänger nicht vollständig zugänglich")
        existing = await self._documents("request_id=:request", {"request": body.request_id})
        if existing:
            if any(row["template_id"] != body.template_id or row["property_id"] != body.property_id or row["as_of"] != body.as_of for row in existing):
                raise HTTPException(409, "Anfrage-ID gehört zu einem anderen Rundschreiben")
            for row in existing:
                await self._person_processing(row["person_id"])
            return [document_out(row) for row in existing]
        allowed = await self.db.scalar(text("SELECT letter_recipients_access(:org,:property,:day)"),
                    {"org": self.org, "property": body.property_id, "day": body.as_of})
        if not allowed:
            raise HTTPException(404, "Vollständige Mieterkontakte oder Objektquellen nicht zugänglich")
        await self.db.execute(text("SELECT id FROM property WHERE organization_id=:org AND id=:id FOR SHARE"),
                              {"org": self.org, "id": body.property_id})
        prop = await PropertyRepository(self.db, self.org).get_by_id(body.property_id)
        if prop is None:
            raise HTTPException(404, "Objekt nicht zugänglich")
        targets = await LeaseAgreementRepository(self.db, self.org).matching_targets(body.property_id, body.as_of, body.as_of)
        people = {}
        for lease, person in targets:
            people.setdefault(person.id, (lease, person))
        if not people:
            raise HTTPException(422, "Keine Mieter am gewählten Stichtag")
        if len(people) > 500:
            raise HTTPException(422, "Maximal 500 Empfänger pro Rundschreiben")
        for person_id in sorted(people):
            lease, person = people[person_id]
            await self._person_processing(person_id)
            await self.db.refresh(person)
            name = person.company_name or f"{person.first_name} {person.last_name}".strip()
            values = {"mieter.name": name, "mieter.adresse": address(person), "objekt.name": prop.name,
                      "objekt.adresse": address(prop), "datum": body.as_of.strftime("%d.%m.%Y")}
            subject = replace_tokens(template["subject"], values)
            rendered_body = replace_tokens(template["body"], values)
            pdf = render_letter(name, address(person), subject, rendered_body, body.as_of)
            digest = compute_sha256(pdf)
            stored = get_store().put(make_storage_key(str(self.org), digest), pdf, content_type="application/pdf")
            await self.db.execute(text("""INSERT INTO letter_document
                (organization_id,template_id,property_id,person_id,tenant_profile_id,lease_agreement_id,
                 request_id,as_of,recipient_name,subject,body,storage_uri,content_sha256)
                VALUES (:org,:template,:property,:person,:tenant,:lease,:request,:day,:name,:subject,:body,:uri,:digest)"""),
                {"org": self.org, "template": body.template_id, "property": body.property_id, "person": person_id,
                 "tenant": lease.tenant_profile_id, "lease": lease.id, "request": body.request_id, "day": body.as_of,
                 "name": name, "subject": subject, "body": rendered_body, "uri": stored.uri, "digest": digest})
        # Grant expiry is wall-clock based even while ACL/source locks are held.
        if not await self.db.scalar(text("SELECT letter_recipients_access(:org,:property,:day)"),
                {"org": self.org, "property": body.property_id, "day": body.as_of}):
            raise HTTPException(404, "Exportberechtigung ist abgelaufen")
        return [document_out(row) for row in await self._documents("request_id=:request", {"request": body.request_id})]

    async def pdf(self, document_id):
        await self._lock()
        rows = await self._documents("id=:id", {"id": document_id})
        if not rows:
            raise HTTPException(404, "Brief nicht zugänglich")
        row = rows[0]
        await self._person_processing(row["person_id"])
        try:
            content = get_store().get(row["storage_uri"])
        except FileNotFoundError as exc:
            raise HTTPException(503, "Gespeichertes Brief-PDF fehlt") from exc
        if compute_sha256(content) != row["content_sha256"]:
            raise HTTPException(503, "Integritätsprüfung des Brief-PDF fehlgeschlagen")
        return content
