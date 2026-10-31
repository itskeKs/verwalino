"""Safe plain-text placeholders and retained, live-authorized circular letters."""
from __future__ import annotations

import html
import json
import re
import uuid
from datetime import date

from fastapi import HTTPException
from sqlalchemy import text

from app.core.pdf import render_pdf
from app.core.storage import compute_sha256, get_store, make_storage_key
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.unit_repo import UnitRepository
from app.core.temporal import db_exclusive_to_api_inclusive
from app.services.financial_scope_service import FinancialScopeService
from app.services.property_master_service import require_property_master

TOKENS = {"mieter.name", "mieter.adresse", "objekt.name", "objekt.adresse", "datum",
          "vermieter.name", "vermieter.adresse", "einheit.nummer", "einheit.lage", "einheit.flaeche",
          "vertrag.beginn", "vertrag.ende", "vertrag.kaltmiete", "vertrag.vorauszahlung",
          "vertrag.kaution", "vertrag.uebergabe", "vertrag.vereinbarungen"}
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


def german_decimal(value):
    return f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def saved_payload(body):
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) and payload.get('document_type') in ('lease_contract', 'handover', 'letter') else {}


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

    async def check_document(self, row):
        await self._person_processing(row['person_id'])
        for source in saved_payload(row['body']).get('landlord_sources', []):
            allowed = await self.db.scalar(text("""SELECT
                profile_source_access(:org,'owner_profile',:profile,'export')
                AND property_history_row_access(:org,'ownership_history',CAST(:context AS jsonb),'export')"""),
                {'org': self.org, 'profile': source['owner_profile_id'], 'context': json.dumps(source)})
            if not allowed:
                raise HTTPException(404, 'Vermieterkontakt nicht zugänglich')
            await self._person_processing(source['person_id'])

    async def variables(self, lease, person, prop, as_of, *, landlords=False, handover_date=None, agreements=''):
        unit = await UnitRepository(self.db, self.org).get_by_id(lease.unit_id)
        if unit is None:
            raise HTTPException(404, 'Einheit nicht zugänglich')
        owners = []
        if landlords:
            owners = (await self.db.execute(text("""SELECT h.id,h.organization_id,h.unit_id,h.owner_profile_id,
                p.id person_id,p.first_name,p.last_name,p.company_name,p.street,p.zip_code,p.city,
                profile_source_access(:org,'owner_profile',o.id,'export') allowed
                FROM ownership_history h JOIN owner_profile o ON o.id=h.owner_profile_id AND o.organization_id=h.organization_id
                JOIN person p ON p.id=o.person_id AND p.organization_id=o.organization_id
                WHERE h.organization_id=:org AND h.unit_id=:unit AND h.valid_from<=:day
                  AND (h.valid_to IS NULL OR h.valid_to>:day) ORDER BY h.id"""),
                {'org': self.org, 'unit': unit.id, 'day': lease.valid_from})).mappings().all()
            for owner in owners:
                if not owner['allowed']:
                    raise HTTPException(404, 'Vermieterkontakt nicht zugänglich')
                await self._person_processing(owner['person_id'])
        owner_names = [owner['company_name'] or f"{owner['first_name']} {owner['last_name']}".strip() for owner in owners]
        owner_addresses = [f"{owner['street']}\n{owner['zip_code']} {owner['city']}".strip() for owner in owners]
        sources = [{key: str(owner[key]) for key in ('id', 'organization_id', 'unit_id', 'owner_profile_id', 'person_id')} for owner in owners]
        end = db_exclusive_to_api_inclusive(lease.valid_to)
        values = {'mieter.name': person.company_name or f'{person.first_name} {person.last_name}'.strip(),
                  'mieter.adresse': address(person), 'objekt.name': prop.name, 'objekt.adresse': address(prop),
                  'datum': as_of.strftime('%d.%m.%Y'), 'einheit.nummer': unit.number, 'einheit.lage': unit.location,
                  'einheit.flaeche': german_decimal(unit.area) + ' m²' if unit.area is not None else 'Nicht erfasst',
                  'vermieter.name': '\n'.join(owner_names) or '[Freitext: Vermieter ergänzen]',
                  'vermieter.adresse': '\n'.join(owner_addresses) or '[Freitext: Vermieteranschrift ergänzen]',
                  'vertrag.beginn': lease.valid_from.strftime('%d.%m.%Y'),
                  'vertrag.ende': end.strftime('%d.%m.%Y') if end else 'Unbefristet',
                  'vertrag.kaltmiete': german_decimal(lease.base_rent) + ' €',
                  'vertrag.vorauszahlung': german_decimal(lease.advance_payment) + ' €',
                  'vertrag.kaution': german_decimal(lease.deposit) + ' €' if lease.deposit is not None else 'Nicht erfasst',
                  'vertrag.uebergabe': handover_date.strftime('%d.%m.%Y') if handover_date else '[Freitext: Übergabedatum ergänzen]',
                  'vertrag.vereinbarungen': agreements or '[Freitext: weitere Vereinbarungen ergänzen und rechtlich prüfen]'}
        return values, sources

    async def save_original(self, *, template_id, lease, person, prop, request_id, as_of, subject, body, pdf):
        digest = compute_sha256(pdf)
        stored = get_store().put(make_storage_key(str(self.org), digest), pdf, content_type='application/pdf')
        row = (await self.db.execute(text("""INSERT INTO letter_document
            (organization_id,template_id,property_id,person_id,tenant_profile_id,lease_agreement_id,
             request_id,as_of,recipient_name,subject,body,storage_uri,content_sha256)
            VALUES (:org,:template,:property,:person,:tenant,:lease,:request,:day,:name,:subject,:body,:uri,:digest)
            RETURNING *"""),
            {'org': self.org, 'template': template_id, 'property': prop.id, 'person': person.id,
             'tenant': lease.tenant_profile_id, 'lease': lease.id, 'request': request_id, 'day': as_of,
             'name': person.company_name or f'{person.first_name} {person.last_name}'.strip(),
             'subject': subject, 'body': body, 'uri': stored.uri, 'digest': digest})).mappings().one()
        await self.check_document(row)
        return document_out(row)

    async def _documents(self, where, params):
        return (await self.db.execute(text(f"""SELECT * FROM letter_document
            WHERE organization_id=:org AND {where} ORDER BY person_id,id"""),
            {"org": self.org, **params})).mappings().all()

    async def list_documents(self, property_id):
        await self._lock()
        await require_property_master(self.db, self.org, property_id, 'export')
        rows = await self._documents("property_id=:property", {"property": property_id})
        for row in rows:
            await self.check_document(row)
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
                await self.check_document(row)
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
            values, sources = await self.variables(lease, person, prop, body.as_of,
                landlords=any(token.startswith('vermieter.') for token in PLACEHOLDER.findall(template['subject'] + template['body'])))
            subject = replace_tokens(template["subject"], values)
            rendered_body = replace_tokens(template["body"], values)
            pdf = render_letter(name, address(person), subject, rendered_body, body.as_of)
            saved_body = json.dumps({'document_type': 'letter', 'landlord_sources': sources, 'text': rendered_body}) if sources else rendered_body
            await self.save_original(template_id=body.template_id, lease=lease, person=person, prop=prop,
                request_id=body.request_id, as_of=body.as_of, subject=subject, body=saved_body, pdf=pdf)
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
        await self.check_document(row)
        try:
            content = get_store().get(row["storage_uri"])
        except FileNotFoundError as exc:
            raise HTTPException(503, "Gespeichertes Brief-PDF fehlt") from exc
        if compute_sha256(content) != row["content_sha256"]:
            raise HTTPException(503, "Integritätsprüfung des Brief-PDF fehlgeschlagen")
        return content
