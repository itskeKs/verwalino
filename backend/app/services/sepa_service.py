"""Authorized SEPA configuration and read-only rent projection, not execution."""
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.rbac import ScopeCapability
from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.mietkonto import LeaseChargeInput, RentChangeInput, tenant_expected_charges
from app.models.bank_account import BankAccount
from app.models.person import Person
from app.models.sepa import SepaCreditor, SepaMandate
from app.models.tenant_profile import TenantProfile
from app.repositories.lease_agreement_repo import LeaseAgreementRepository
from app.repositories.lease_rent_change_repo import LeaseRentChangeRepository
from app.services.datev_export import DatevExportService
from app.services.financial_scope_service import FinancialScopeService
from app.services.report_service import ReportService
from app.services.sepa_export import validate_iban, validate_bic, render_sepa


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()


def _name(person):
    return f'{person.first_name} {person.last_name}'.strip()


def _person_hash(person):
    return _hash([_name(person),validate_iban(person.iban)])


def _bank_hash(bank):
    return _hash([validate_iban(bank.iban),validate_bic(bank.bic)])


def _processing(person):
    return person.anonymized_at is None and not person.processing_restricted and person.deletion_requested_at is None


def creditor_out(row):
    if row is None:
        return None
    return {key:getattr(row,key) for key in ('creditor_id','name','country','town','bank_authorized')}


def mandate_out(row, person):
    try:
        current = _processing(person) and row.source_fingerprint == _person_hash(person)
    except ValueError:
        current = False
    return {key:getattr(row,key) for key in ('id','tenant_profile_id','reference','signed_on','sequence_type',
        'last_used_on','debtor_country','debtor_bic','original_reference','revoked_at')} | {'source_current':current}


class SepaService:
    def __init__(self, db, auth):
        self.db,self.auth,self.org=db,auth,auth.organization_id
        self.context=FinancialScopeService(db,self.org,auth.user_id)

    async def _bank(self, bank_id, capability=ScopeCapability.READ):
        await self.context.lock_inputs()
        bank=await self.db.scalar(select(BankAccount).where(BankAccount.organization_id==self.org,
            BankAccount.id==bank_id).with_for_update(read=True))
        if bank is None:
            raise HTTPException(404,'Bank account not accessible')
        await self.context.enter(bank,capability=capability)
        if capability == ScopeCapability.PREPARE and not bank.is_active:
            raise HTTPException(409, 'Bank account is inactive; no new SEPA configuration permitted')
        if bank.account_type!='LAUFEND':
            raise HTTPException(422,'Monthly rent collections require an operating account')
        if not await self.db.scalar(text('SELECT sepa_bank_access(:org,:bank,:cap)'),
            {'org':self.org,'bank':bank_id,'cap':capability.value}):
            raise HTTPException(404,'Bank configuration not accessible')
        if not await self.db.scalar(text("SELECT property_financial_disclosure_access(:org,:property,'read')"),
            {'org':self.org,'property':bank.property_id}):
            raise HTTPException(404,'Complete property contacts not accessible')
        scope=await self.db.scalar(text("SELECT nullif(current_setting('app.current_scope_id',true),'')::uuid"))
        if not await self.db.scalar(text("SELECT report_scope_source_access(:org,:scope,'read')"),{'org':self.org,'scope':scope}):
            raise HTTPException(404,'Complete rent sources not accessible')
        return bank

    async def _creditor(self, bank_id):
        # Callers hold the financial-input lock. FOR SHARE would additionally
        # apply UPDATE RLS and hide configuration from authorized read-only exports.
        return await self.db.scalar(select(SepaCreditor).where(SepaCreditor.organization_id==self.org,
            SepaCreditor.bank_account_id==bank_id))

    async def _mandates(self, bank_id):
        return list((await self.db.scalars(select(SepaMandate).where(SepaMandate.organization_id==self.org,
            SepaMandate.bank_account_id==bank_id).order_by(SepaMandate.created_at,SepaMandate.id))).all())

    async def _person(self, tenant_id, *, processing=True):
        person=await self.db.scalar(select(Person).join(TenantProfile,
            (TenantProfile.person_id==Person.id)&(TenantProfile.organization_id==self.org)).where(
                Person.organization_id==self.org,TenantProfile.id==tenant_id).with_for_update(read=True,of=Person))
        if person is None:
            raise HTTPException(404,'Tenant contact not accessible')
        await self.db.refresh(person)
        if processing and not _processing(person):
            raise HTTPException(409,'Tenant processing is restricted; no direct debit permitted')
        return person

    async def _flush(self):
        try:
            await self.db.flush()
        except IntegrityError as error:
            raise HTTPException(409,'SEPA identity is already registered, historically referenced or cannot be rewritten') from error

    async def account(self, bank_id):
        bank=await self._bank(bank_id)
        targets=await LeaseAgreementRepository(self.db,self.org).matching_targets(bank.property_id,date.min,date.max)
        tenants={}
        for lease,person in targets:
            allowed=_processing(person)
            tenants[str(lease.tenant_profile_id)]={'tenant_profile_id':lease.tenant_profile_id,'person_id':person.id,
                'name':_name(person),'iban':person.iban if allowed else None,'city':person.city}
        mandates=[]
        for m in await self._mandates(bank_id):
            person=await self._person(m.tenant_profile_id,processing=False)
            mandates.append(mandate_out(m,person))
        return {'bank':{'id':bank.id,'label':bank.label,'iban':bank.iban,'bic':bank.bic,'property_id':bank.property_id},
            'creditor':creditor_out(await self._creditor(bank_id)),
            'tenants':list(tenants.values()),'mandates':mandates}

    async def save_creditor(self, bank_id, body):
        bank=await self._bank(bank_id,ScopeCapability.PREPARE)
        try:
            fingerprint=_bank_hash(bank)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        row=await self._creditor(bank_id)
        values=body.model_dump()|{'bank_fingerprint':fingerprint}
        if row is None:
            row=SepaCreditor(organization_id=self.org,bank_account_id=bank_id,**values)
            self.db.add(row)
        else:
            if any(getattr(row,key)!=value for key,value in values.items()) and any(
                    m.revoked_at is None for m in await self._mandates(bank_id)):
                raise HTTPException(409,'Revoke all active mandates before changing creditor configuration')
            for key,value in values.items():
                setattr(row,key,value)
        await self._flush()
        return creditor_out(row)

    async def create_mandate(self, bank_id, body):
        await self._bank(bank_id,ScopeCapability.PREPARE)
        if not await self.db.scalar(text("SELECT sepa_mandate_access(:org,:bank,:tenant,'prepare')"),
            {'org':self.org,'bank':bank_id,'tenant':body.tenant_profile_id}):
            raise HTTPException(404,'Tenant and bank sources not accessible')
        person=await self._person(body.tenant_profile_id)
        try:
            if body.confirmed_iban!=validate_iban(person.iban):
                raise ValueError('Explicitly confirmed mandate IBAN differs from the current tenant contact')
            fingerprint=_person_hash(person)
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        creditor=await self._creditor(bank_id)
        if creditor is None:
            raise HTTPException(422,'Configure the actual creditor first')
        if body.signed_on>date.today() or (body.last_used_on is not None and body.last_used_on>date.today()):
            raise HTTPException(422,'Signature and actual previous usage cannot be in the future')
        values=body.model_dump(exclude={'confirmed_iban','mandate_confirmed'})
        row=SepaMandate(organization_id=self.org,bank_account_id=bank_id,
            creditor_id=creditor.creditor_id,source_fingerprint=fingerprint,**values)
        self.db.add(row)
        await self._flush()
        return mandate_out(row,person)

    async def change_mandate(self, mandate_id, usage=None):
        await self.context.lock_inputs()
        row=await self.db.scalar(select(SepaMandate).where(SepaMandate.organization_id==self.org,
            SepaMandate.id==mandate_id).with_for_update())
        if row is None:
            raise HTTPException(404,'Mandate not accessible')
        await self._bank(row.bank_account_id,ScopeCapability.PREPARE)
        if not await self.db.scalar(text("SELECT sepa_mandate_access(:org,:bank,:tenant,'prepare')"),
            {'org':self.org,'bank':row.bank_account_id,'tenant':row.tenant_profile_id}):
            raise HTTPException(404,'Mandate preparation not accessible')
        if row.revoked_at is not None:
            raise HTTPException(409,'Revoked mandates cannot be rewritten')
        if usage is None:
            row.revoked_at=datetime.now(timezone.utc)
        else:
            if usage.last_used_on is not None and not row.signed_on<=usage.last_used_on<=date.today():
                raise HTTPException(422,'Actual prior use must be between signature and today')
            if row.last_used_on is not None and (usage.last_used_on is None or usage.last_used_on<row.last_used_on):
                raise HTTPException(409,'Recorded prior usage cannot be moved backwards')
            row.sequence_type,row.last_used_on=usage.sequence_type,usage.last_used_on
        await self._flush()
        return mandate_out(row,await self._person(row.tenant_profile_id,processing=False))

    async def require_export(self, bank, scope_id):
        await self.context.require_export(bank)
        await DatevExportService(self.db,self.auth).require_export(bank.property_id,scope_id)

    async def prepare(self, body):
        bank=await self._bank(body.bank_account_id)
        if not bank.is_active:
            raise HTTPException(409, 'Bank account is inactive; no new SEPA collection permitted')
        prop,scope=await ReportService(self.db,self.org,self.auth.user_id).property_period(
            bank.property_id,body.period_start,body.period_end,body.accounting_scope_id)
        if prop.management_type not in ('MIET','MISCH') or (scope is not None and scope.scope_type!='MIET'):
            raise HTTPException(422,'Monthly rent collections require a complete rental financial scope')
        scope_id=scope.id if scope else None
        await self.require_export(bank,scope_id)
        creditor=await self._creditor(bank.id)
        if creditor is None:
            raise HTTPException(422,'Creditor configuration is missing')
        try:
            if creditor.bank_fingerprint!=_bank_hash(bank):
                raise ValueError('Creditor bank details changed; explicitly renew configuration and mandates')
        except ValueError as error:
            raise HTTPException(422,str(error)) from error
        targets=await LeaseAgreementRepository(self.db,self.org).matching_targets(bank.property_id,body.period_start,body.period_end)
        totals={}
        with localcontext() as context:
            context.prec=40
            for lease,_ in targets:
                changes=await LeaseRentChangeRepository(self.db,self.org).list_for_lease(lease.id)
                charge=LeaseChargeInput(lease.id,lease.valid_from,db_exclusive_to_engine_inclusive(lease.valid_to),
                    lease.base_rent,lease.advance_payment,tuple(RentChangeInput(c.valid_from,c.base_rent,c.advance_payment,c.heating_advance_payment) for c in changes),
                    heating_advance_payment=lease.heating_advance_payment)
                values=tenant_expected_charges(charge,body.period_start,body.period_end)
                entry=totals.setdefault(lease.tenant_profile_id,{'amount':Decimal('0.00'),'lease_ids':[]})
                entry['amount']+=sum((v.amount for v in values),Decimal('0.00'))
                entry['lease_ids'].append(str(lease.id))
        active={m.tenant_profile_id:m for m in await self._mandates(bank.id) if m.revoked_at is None}
        rows=[]
        for tenant_id,entry in sorted(totals.items()):
            if entry['amount']==0:
                continue
            person=await self._person(tenant_id)
            m=active.get(tenant_id)
            if m is None:
                raise HTTPException(422,'Every positive monthly rent requires an explicit active mandate; no tenant is silently skipped')
            try:
                if m.source_fingerprint!=_person_hash(person) or m.creditor_id!=creditor.creditor_id:
                    raise ValueError('Mandate identity changed; revoke and register renewed consent')
                last=m.last_used_on or m.signed_on
                elapsed=(body.collection_date.year-last.year)*12+body.collection_date.month-last.month
                if elapsed>36 or (elapsed==36 and body.collection_date.day>=last.day):
                    raise ValueError('Mandate has not been used for 36 months; obtain new consent')
                if m.signed_on>body.collection_date or (m.last_used_on is not None and m.last_used_on>=body.collection_date):
                    raise ValueError('Signature/prior usage must precede the requested collection')
                iban=validate_iban(person.iban)
            except ValueError as error:
                raise HTTPException(422,str(error)) from error
            rows.append({'tenant_profile_id':str(tenant_id),'person_id':str(person.id),'name':_name(person),
                'mandate_id':str(m.id),'reference':m.reference,'signed_on':m.signed_on.isoformat(),
                'sequence_type':m.sequence_type,'last_used_on':str(m.last_used_on) if m.last_used_on else None,
                'amount':format(entry['amount'],'.2f'),'lease_ids':entry['lease_ids'],
                'iban':iban,'town':person.city,'country':m.debtor_country,'street':person.street,
                'postal_code':person.zip_code,'debtor_bic':m.debtor_bic})
        if len(rows)>500:
            raise HTTPException(422,'Split rent universes larger than 500 tenants explicitly')
        bank_values={'iban':bank.iban,'bic':bank.bic}
        creditor_values=creditor_out(creditor)
        # Validate the actual frozen file before exposing a successful preview.
        render_sepa(body,creditor_values,bank_values,rows)
        digest=_hash({'configuration':body.model_dump(mode='json',exclude={'expected_source_digest'}),
            'creditor':creditor_values,'bank':bank_values,'rows':rows})
        with localcontext() as context:
            context.prec=40
            total=format(sum((Decimal(r['amount']) for r in rows),Decimal('0.00')),'.2f')
        await self.require_export(bank,scope_id)
        preview={'rows':[{k:r[k] for k in ('tenant_profile_id','name','mandate_id','reference','amount','sequence_type','lease_ids')} for r in rows],
            'total':total,'source_digest':digest}
        return preview,creditor_values,bank_values,rows,bank,scope_id
