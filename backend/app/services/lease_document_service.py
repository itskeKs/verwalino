"""Lease documents reuse editable letter templates and retained letter originals."""
import json
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import text

from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.person_repo import PersonRepository
from app.repositories.property_repo import PropertyRepository
from app.repositories.tenant_profile_repo import TenantProfileRepository
from app.repositories.unit_repo import UnitRepository
from app.repositories.meter_repo import MeterRepository
from app.services.letter_service import LETTER_PAYLOAD_FORMAT, LetterService, document_out, render_letter, replace_tokens, saved_payload
from app.services.meter_service import MeterReadingService, ReadingCreate
from app.services.property_master_service import require_property_master

LEGAL_CAVEAT = 'Vorlage; rechtliche Prüfung erforderlich'
CONTRACT_TEMPLATE = LEGAL_CAVEAT + """

Vermieter:
{{vermieter.name}}
{{vermieter.adresse}}

Mieter:
{{mieter.name}}
{{mieter.adresse}}

Mietobjekt: {{objekt.name}}, {{objekt.adresse}}
Einheit {{einheit.nummer}} · {{einheit.lage}}
Fläche: {{einheit.flaeche}}

Mietbeginn: {{vertrag.beginn}}
Mietende: {{vertrag.ende}}
Monatliche Kaltmiete (Originalvertrag): {{vertrag.kaltmiete}}
Monatliche Vorauszahlung (Originalvertrag): {{vertrag.vorauszahlung}}
Kaution: {{vertrag.kaution}}
Übergabedatum: {{vertrag.uebergabe}}

Freitext: Weitere Vereinbarungen
{{vertrag.vereinbarungen}}

Freitext: Befristungsgrund, Nutzung und sonstige Vertragsbedingungen
[Bitte ergänzen und rechtlich prüfen lassen.]

Ort, Datum: ____________________
Unterschrift Vermieter: ____________________
Unterschrift Mieter: ____________________"""


class LeaseDocumentService:
    def __init__(self, db, auth):
        self.db, self.auth, self.org = db, auth, auth.organization_id
        self.letters = LetterService(db, auth)

    async def context(self, lease_id, *, write=False):
        await self.letters._lock()
        lease = await LeaseAgreementRepository(self.db, self.org).get_by_id(lease_id)
        if lease is None:
            raise HTTPException(404, 'Mietvertrag nicht zugänglich')
        unit = await UnitRepository(self.db, self.org).get_by_id(lease.unit_id)
        tenant = await TenantProfileRepository(self.db, self.org).get_by_id(lease.tenant_profile_id)
        if unit is None or tenant is None:
            raise HTTPException(404, 'Vertragsquellen nicht zugänglich')
        allowed = await self.db.scalar(text('SELECT letter_source_access(:org,:property,:person,:tenant,:lease)'),
            {'org': self.org, 'property': unit.property_id, 'person': tenant.person_id, 'tenant': tenant.id, 'lease': lease.id})
        if not allowed:
            raise HTTPException(404, 'Vertragsquellen nicht zugänglich')
        if write:
            await require_property_master(self.db, self.org, unit.property_id, 'prepare')
        person = await PersonRepository(self.db, self.org).get_by_id(tenant.person_id)
        prop = await PropertyRepository(self.db, self.org).get_by_id(unit.property_id)
        if person is None or prop is None:
            raise HTTPException(404, 'Vertragsquellen nicht zugänglich')
        await self.letters._person_processing(person.id)
        return lease, unit, person, prop

    async def list_documents(self, lease_id):
        await self.context(lease_id)
        rows = await self.letters._documents('lease_agreement_id=:lease', {'lease': lease_id})
        for row in rows:
            await self.letters.check_document(row)
        return [document_out(row) for row in rows]

    async def replay(self, lease, body, kind):
        await self.db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                              {'key': f'letter:{self.org}:{body.request_id}'})
        if not await self.db.scalar(text('SELECT letter_request_access(:org,:request)'),
                                    {'org': self.org, 'request': body.request_id}):
            raise HTTPException(404, 'Ursprüngliche Vertragsquellen nicht zugänglich')
        rows = await self.letters._documents('request_id=:request', {'request': body.request_id})
        if not rows:
            return None
        row = rows[0]
        payload = saved_payload(row['body'])
        if len(rows) != 1 or row['lease_agreement_id'] != lease.id or payload.get('document_type') != kind or payload.get('input') != body.model_dump(mode='json'):
            raise HTTPException(409, 'Anfrage-ID gehört zu einem anderen Dokument')
        await self.letters.check_document(row)
        return document_out(row)

    async def template(self, name, subject, body):
        await self.db.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:key,0))'),
                              {'key': f'lease-template:{self.org}:{name}'})
        params = {'org': self.org, 'name': name}
        template = (await self.db.execute(text('''SELECT * FROM letter_template
            WHERE organization_id=:org AND name=:name ORDER BY created_at,id LIMIT 1 FOR SHARE'''), params)).mappings().first()
        if template is None:
            template = (await self.db.execute(text('''INSERT INTO letter_template(organization_id,name,subject,body)
                VALUES (:org,:name,:subject,:body) RETURNING *'''), {**params, 'subject': subject, 'body': body})).mappings().one()
        return template

    async def contract(self, lease_id, body):
        lease, unit, person, prop = await self.context(lease_id, write=True)
        existing = await self.replay(lease, body, 'lease_contract')
        if existing:
            return existing
        template = await self.template('Mietvertrag', 'Mietvertrag · Einheit {{einheit.nummer}}', CONTRACT_TEMPLATE)
        values, sources = await self.letters.variables(lease, person, prop, body.as_of, landlords=True,
            handover_date=body.handover_date, agreements=body.agreements)
        subject = replace_tokens(template['subject'], values)
        rendered = replace_tokens(template['body'], values)
        if not rendered.startswith(LEGAL_CAVEAT):
            rendered = LEGAL_CAVEAT + '\n\n' + rendered
        payload = {'format': LETTER_PAYLOAD_FORMAT, 'document_type': 'lease_contract', 'input': body.model_dump(mode='json'),
                   'landlord_sources': sources, 'variables': values, 'text': rendered}
        pdf = render_letter(values['mieter.name'], values['mieter.adresse'], subject, rendered, body.as_of)
        return await self.letters.save_original(template_id=template['id'], lease=lease, person=person, prop=prop,
            request_id=body.request_id, as_of=body.as_of, subject=subject, body=json.dumps(payload, ensure_ascii=False), pdf=pdf)

    async def handover(self, lease_id, body):
        lease, unit, person, prop = await self.context(lease_id, write=True)
        existing = await self.replay(lease, body, 'handover')
        if existing:
            return existing
        repo = MeterRepository(self.db, self.org)
        # Resolve submitted IDs before set validation, keeping foreign IDs indistinguishable from missing IDs.
        submitted = {}
        for reading in body.readings:
            meter = await repo.get_by_id(reading.meter_id)
            if meter is None or meter.unit_id != unit.id or meter.property_id != prop.id:
                raise HTTPException(404, 'Zähler der Einheit nicht zugänglich')
            submitted[meter.id] = meter
        meters = await repo.list_by_unit(unit.id)
        if len(submitted) != len(body.readings) or set(submitted) != {meter.id for meter in meters}:
            raise HTTPException(422, 'Bitte jeden Zähler der Einheit genau einmal ablesen.')
        values = {reading.meter_id: reading.value for reading in body.readings}
        rows = []
        service = MeterReadingService(self.db, self.auth)
        for meter in sorted(meters, key=lambda m: m.id):
            reading = await service.record(meter.id, ReadingCreate(reading_date=body.date,
                value=values[meter.id], period=f'Übergabeprotokoll {body.date.isoformat()}'))
            rows.append({'meter_id': str(meter.id), 'reading_id': reading['id'], 'number': meter.meter_number,
                         'type': meter.meter_type, 'value': reading['value']})
        template = await self.template('Übergabeprotokoll', 'Übergabeprotokoll', 'Übergabeprotokoll zur Einheit {{einheit.nummer}}')
        subject = f'Übergabeprotokoll · {body.type} · Einheit {unit.number}'
        rendered = (f'{body.type} am {body.date.strftime("%d.%m.%Y")}\n'
            f'{prop.name}\n{prop.street}\n{prop.zip_code} {prop.city}\nEinheit {unit.number}\n\n'
            f'Anwesende Parteien:\n{body.parties}\n\nSchlüssel:\n' +
            ('\n'.join(f'{key.type}: {key.count}' for key in body.keys) or 'Keine Schlüssel erfasst') +
            f'\n\nMängel / Notizen:\n{body.notes or "Keine Angaben"}\n\n'
            f'Unterschriften (Namen; keine elektronische Signatur):\n{body.signatures}')
        meter_rows = [(r['number'], r['type'], f"{Decimal(r['value']):,.4f}".replace(',', '_').replace('.', ',').replace('_', '.')) for r in rows]
        pdf = render_letter(person.company_name or f'{person.first_name} {person.last_name}'.strip(),
            f'{person.street}\n{person.zip_code} {person.city}'.strip(), subject, rendered, body.date, meter_rows)
        payload = {'format': LETTER_PAYLOAD_FORMAT, 'document_type': 'handover', 'input': body.model_dump(mode='json'), 'meters': rows, 'text': rendered}
        return await self.letters.save_original(template_id=template['id'], lease=lease, person=person, prop=prop,
            request_id=body.request_id, as_of=body.date, subject=subject, body=json.dumps(payload, ensure_ascii=False), pdf=pdf)
