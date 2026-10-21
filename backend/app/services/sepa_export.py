"""Offline CORE pain.008.001.08 rendering; never executes a collection.

Primary profiles: ISO pain.008.001.08 and EPC130-08 SDD Core C2PSP 2025 V1.0
(public EPC implementation-guideline page, original schema packaged locally).
A valid schema/confirmed configuration is not proof of bank or payer consent.
"""
from calendar import monthrange
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from functools import lru_cache
import hashlib
from pathlib import Path
import re
from typing import Annotated, Literal
from uuid import UUID
from xml.etree import ElementTree as ET

from fastapi import HTTPException
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

NAMESPACE = 'urn:iso:std:iso:20022:tech:xsd:pain.008.001.08'
ET.register_namespace('', NAMESPACE)
EPC_SCHEMA = Path(__file__).resolve().parent.parent / 'resources' / 'sepa' / 'EPC130-08_2025_V1.0_pain.008.001.08.xsd'
# EPC geographical scope; an IBAN checksum does not establish account existence.
SEPA_COUNTRIES = frozenset('AD AL AT BE BG CH CY CZ DE DK EE ES FI FR GB GI GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM VA'.split())
EEA_COUNTRIES = frozenset('AT BE BG CY CZ DE DK EE ES FI FR GR HR HU IE IS IT LI LT LU LV MT NL PL PT RO SE SI SK IS LI NO'.split())


def _compact(value):
    if not isinstance(value, str):
        raise ValueError('A bank identifier must be a string')
    return value.replace(' ', '').upper()


def _mod97(value):
    remainder = 0
    for character in value:
        digits = character if character.isascii() and character.isdigit() else str(ord(character)-55)
        for digit in digits:
            remainder = (remainder * 10 + int(digit)) % 97
    return remainder


def validate_iban(value) -> str:
    value = _compact(value)
    if re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}',value) is None or value[:2] not in SEPA_COUNTRIES:
        raise ValueError('Provide a SEPA-country IBAN of 15–34 characters')
    if value[:2] == 'DE' and (len(value) != 22 or not value[4:].isdigit()):
        raise ValueError('A German IBAN has 22 characters and a numeric BBAN')
    if _mod97(value[4:] + value[:4]) != 1:
        raise ValueError('Invalid IBAN checksum')
    return value


def validate_creditor_id(value) -> str:
    value = _compact(value)
    if re.fullmatch(r'[A-Z]{2}[0-9]{2}[A-Z0-9]{3}[A-Z0-9]{1,28}',value) is None or value[:2] not in SEPA_COUNTRIES:
        raise ValueError('Invalid SEPA creditor identifier')
    if value[:2] == 'DE' and (len(value) != 18 or not value[7:].isdigit() or value[7] != '0'):
        raise ValueError('A German creditor identifier has 18 characters and an eleven-digit national part starting with zero')
    # EPC §1.5.2 explicitly excludes the three-character business code.
    if _mod97(value[7:] + value[:4]) != 1:
        raise ValueError('Invalid creditor identifier checksum')
    return value


def identifier(value: str) -> str:
    if not isinstance(value,str) or not 1 <= len(value) <= 35 or re.fullmatch(r"[A-Za-z0-9/?:().,'+ -]+",value) is None or value.startswith('/') or value.endswith('/') or '//' in value:
        raise ValueError('Identifiers must use 1–35 SEPA characters, without leading/trailing or repeated slashes')
    return value


def _text(value, maximum):
    if not isinstance(value,str) or not value.strip() or len(value)>maximum or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError(f'Provide nonempty SEPA text of at most {maximum} characters without controls')
    return value.strip()


def validate_bic(value):
    if value is None or value == '':
        return None
    value = _compact(value)
    if re.fullmatch(r'[A-Z0-9]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?',value) is None:
        raise ValueError('Invalid BIC')
    return value


class CreditorRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    creditor_id: str
    name: str
    country: Annotated[str,Field(pattern=r'^[A-Z]{2}$')]
    town: str
    bank_authorized: StrictBool

    @model_validator(mode='after')
    def validate_configuration(self):
        self.creditor_id = validate_creditor_id(self.creditor_id)
        self.name, self.town = _text(self.name,70), _text(self.town,35)
        if not self.bank_authorized:
            raise ValueError('Confirm the actual bank agreement; creditor-ID allocation is not collection permission')
        return self


class MandateUsageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sequence_type: Literal['FRST','RCUR']
    last_used_on: date | None

    @model_validator(mode='after')
    def validate_usage(self):
        if (self.sequence_type == 'FRST') != (self.last_used_on is None):
            raise ValueError('FRST requires no previous use; RCUR requires an explicitly recorded previous collection date')
        return self


class MandateRequest(MandateUsageRequest):
    tenant_profile_id: UUID
    reference: str
    signed_on: date
    debtor_country: Annotated[str,Field(pattern=r'^[A-Z]{2}$')]
    debtor_bic: str | None = None
    confirmed_iban: str
    original_reference: str
    mandate_confirmed: StrictBool

    @model_validator(mode='after')
    def validate_mandate(self):
        self.reference = identifier(self.reference).upper()
        self.confirmed_iban = validate_iban(self.confirmed_iban)
        self.debtor_bic = validate_bic(self.debtor_bic)
        self.original_reference = _text(self.original_reference,255)
        if not self.mandate_confirmed:
            raise ValueError('An actually signed mandate and retained original are required')
        if self.last_used_on is not None and self.last_used_on < self.signed_on:
            raise ValueError('Previous collection cannot precede the signature')
        return self


class SepaRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    bank_account_id: UUID
    accounting_scope_id: UUID | None = None
    year: Annotated[int,Field(strict=True,ge=2000,le=2099)]
    month: Annotated[int,Field(strict=True,ge=1,le=12)]
    collection_date: date
    message_id: str
    pre_notification_on: date
    notice_period_days: Annotated[int,Field(strict=True,ge=1,le=14)]
    shorter_period_agreed: StrictBool
    pre_notification_confirmed: StrictBool
    bank_profile_confirmed: StrictBool
    expected_source_digest: Annotated[str | None,Field(pattern=r'^[a-f0-9]{64}$')] = None

    @property
    def period_start(self):
        return date(self.year,self.month,1)

    @property
    def period_end(self):
        return date(self.year,self.month,monthrange(self.year,self.month)[1])

    @model_validator(mode='after')
    def validate_export(self):
        self.message_id = identifier(self.message_id)
        if not self.pre_notification_confirmed or not self.bank_profile_confirmed:
            raise ValueError('Explicitly confirm actual pre-notification and the bank-supported CORE profile')
        if self.notice_period_days < 14 and not self.shorter_period_agreed:
            raise ValueError('A shorter pre-notification period requires an actual agreement')
        if (self.collection_date-self.pre_notification_on).days < self.notice_period_days:
            raise ValueError('Pre-notification does not meet the explicitly chosen notice period')
        if self.collection_date < self.period_start or self.collection_date.year>2099:
            raise ValueError('Collection cannot precede the selected rent month')
        return self


@lru_cache(maxsize=1)
def _schema():
    return etree.XMLSchema(etree.parse(str(EPC_SCHEMA),etree.XMLParser(resolve_entities=False,no_network=True)))


def _child(parent, tag, value=None, **attributes):
    node = ET.SubElement(parent, f'{{{NAMESPACE}}}{tag}', attributes)
    if value is not None:
        node.text = str(value)
    return node


def _party(parent, tag, name, town, country, street=None, postal_code=None):
    party = _child(parent,tag)
    _child(party,'Nm',_text(name,70))
    address = _child(party,'PstlAdr')
    if postal_code:
        _child(address,'PstCd',_text(postal_code,16))
    _child(address,'TwnNm',_text(town,35))
    _child(address,'Ctry',country)
    if street:
        _child(address,'AdrLine',_text(street,70))
    return party


def _agent(parent, tag, iban, bic):
    bank = _child(_child(parent,tag),'FinInstnId')
    if bic:
        _child(bank,'BICFI',validate_bic(bic))
    elif iban[:2] in EEA_COUNTRIES:
        _child(_child(bank,'Othr'),'Id','NOTPROVIDED')
    else:
        raise ValueError('A BIC is required for this non-EEA SEPA bank')


def render_sepa(config: SepaRequest, creditor: dict, bank: dict, rows: list[dict], *, generated_at=None) -> bytes:
    """Serialize admitted frozen rows; local validation never contacts a bank."""
    try:
        if not rows:
            raise ValueError('No positive monthly rent to collect')
        with localcontext() as context:
            context.prec=40
            total = sum((Decimal(row['amount']) for row in rows),Decimal('0.00'))
        for row in rows:
            amount = Decimal(row['amount'])
            if not amount.is_finite() or not Decimal('.01') <= amount <= Decimal('999999999.99'):
                raise ValueError('CORE amount must be 0.01–999999999.99 EUR')
        document = ET.Element(f'{{{NAMESPACE}}}Document')
        initiation = _child(document,'CstmrDrctDbtInitn')
        header = _child(initiation,'GrpHdr')
        _child(header,'MsgId',config.message_id)
        _child(header,'CreDtTm',(generated_at or datetime.now(timezone.utc)).isoformat(timespec='seconds'))
        _child(header,'NbOfTxs',len(rows))
        _child(header,'CtrlSum',format(total,'.2f'))
        _child(_child(header,'InitgPty'),'Nm',_text(creditor['name'],70))
        for sequence in sorted({row['sequence_type'] for row in rows}):
            group = [row for row in rows if row['sequence_type']==sequence]
            with localcontext() as context:
                context.prec=40
                subtotal=sum((Decimal(row['amount']) for row in group),Decimal('0.00'))
            payment = _child(initiation,'PmtInf')
            _child(payment,'PmtInfId',hashlib.sha256(f'{config.message_id}:{sequence}'.encode()).hexdigest()[:32])
            _child(payment,'PmtMtd','DD')
            _child(payment,'NbOfTxs',len(group))
            _child(payment,'CtrlSum',format(subtotal,'.2f'))
            info = _child(payment,'PmtTpInf')
            _child(_child(info,'SvcLvl'),'Cd','SEPA')
            _child(_child(info,'LclInstrm'),'Cd','CORE')
            _child(info,'SeqTp',sequence)
            _child(payment,'ReqdColltnDt',config.collection_date.isoformat())
            _party(payment,'Cdtr',creditor['name'],creditor['town'],creditor['country'])
            iban = validate_iban(bank['iban'])
            _child(_child(_child(payment,'CdtrAcct'),'Id'),'IBAN',iban)
            _agent(payment,'CdtrAgt',iban,bank.get('bic'))
            _child(payment,'ChrgBr','SLEV')
            other=_child(_child(_child(_child(payment,'CdtrSchmeId'),'Id'),'PrvtId'),'Othr')
            _child(other,'Id',validate_creditor_id(creditor['creditor_id']))
            _child(_child(other,'SchmeNm'),'Prtry','SEPA')
            for row in group:
                transaction = _child(payment,'DrctDbtTxInf')
                e2e = hashlib.sha256(f"{config.message_id}:{row['tenant_profile_id']}".encode()).hexdigest()[:32]
                _child(_child(transaction,'PmtId'),'EndToEndId',e2e)
                _child(transaction,'InstdAmt',row['amount'],Ccy='EUR')
                mandate = _child(_child(transaction,'DrctDbtTx'),'MndtRltdInf')
                _child(mandate,'MndtId',row['reference'])
                _child(mandate,'DtOfSgntr',row['signed_on'])
                _child(mandate,'AmdmntInd','false')
                debtor_iban=validate_iban(row['iban'])
                _agent(transaction,'DbtrAgt',debtor_iban,row.get('debtor_bic'))
                _party(transaction,'Dbtr',row['name'],row['town'],row['country'],row.get('street'),row.get('postal_code'))
                _child(_child(_child(transaction,'DbtrAcct'),'Id'),'IBAN',debtor_iban)
                _child(_child(transaction,'RmtInf'),'Ustrd',f'Miete {config.year}-{config.month:02d}')
        content=ET.tostring(document,encoding='utf-8',xml_declaration=True)
        _schema().assertValid(etree.fromstring(content,etree.XMLParser(resolve_entities=False,no_network=True)))
        return content
    except (ValueError,etree.DocumentInvalid) as error:
        raise HTTPException(422,f'SEPA file validation failed: {error if isinstance(error,ValueError) else "EPC profile schema mismatch"}') from error
