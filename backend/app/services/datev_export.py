"""Read-only, explicitly configured DATEV-format 700 / booking batch 13.

Primary specification: developer.datev.de/de/file-format/details/datev-format.
A full current-state file, NOT an incremental ledger or proof of a DATEV import.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import text

from app.core.rbac import ScopeCapability
from app.core.tenant import AuthContext
from app.repositories.datev_export_repo import DatevExportRepository
from app.services.authorization_service import AuthorizationService
from app.services.report_service import ReportService

# Exact headings from the official version-13 example; positions are not inferred.
HEADINGS = ('Umsatz (ohne Soll/Haben-Kz)', 'Soll/Haben-Kennzeichen', 'WKZ Umsatz', 'Kurs', 'Basis-Umsatz', 'WKZ Basis-Umsatz', 'Konto', 'Gegenkonto (ohne BU-Schlüssel)', 'BU-Schlüssel', 'Belegdatum', 'Belegfeld 1', 'Belegfeld 2', 'Skonto', 'Buchungstext', 'Postensperre', 'Diverse Adressnummer', 'Geschäftspartnerbank', 'Sachverhalt', 'Zinssperre', 'Beleglink', 'Beleginfo - Art 1', 'Beleginfo - Inhalt 1', 'Beleginfo - Art 2', 'Beleginfo - Inhalt 2', 'Beleginfo - Art 3', 'Beleginfo - Inhalt 3', 'Beleginfo - Art 4', 'Beleginfo - Inhalt 4', 'Beleginfo - Art 5', 'Beleginfo - Inhalt 5', 'Beleginfo - Art 6', 'Beleginfo - Inhalt 6', 'Beleginfo - Art 7', 'Beleginfo - Inhalt 7', 'Beleginfo - Art 8', 'Beleginfo - Inhalt 8', 'KOST1 - Kostenstelle', 'KOST2 - Kostenstelle', 'Kost-Menge', 'EU-Land u. UStID (Bestimmung)', 'EU-Steuersatz (Bestimmung)', 'Abw. Versteuerungsart', 'Sachverhalt L+L', 'Funktionsergänzung L+L', 'BU 49 Hauptfunktionstyp', 'BU 49 Hauptfunktionsnummer', 'BU 49 Funktionsergänzung', 'Zusatzinformation - Art 1', 'Zusatzinformation- Inhalt 1', 'Zusatzinformation - Art 2', 'Zusatzinformation- Inhalt 2', 'Zusatzinformation - Art 3', 'Zusatzinformation- Inhalt 3', 'Zusatzinformation - Art 4', 'Zusatzinformation- Inhalt 4', 'Zusatzinformation - Art 5', 'Zusatzinformation- Inhalt 5', 'Zusatzinformation - Art 6', 'Zusatzinformation- Inhalt 6', 'Zusatzinformation - Art 7', 'Zusatzinformation- Inhalt 7', 'Zusatzinformation - Art 8', 'Zusatzinformation- Inhalt 8', 'Zusatzinformation - Art 9', 'Zusatzinformation- Inhalt 9', 'Zusatzinformation - Art 10', 'Zusatzinformation- Inhalt 10', 'Zusatzinformation - Art 11', 'Zusatzinformation- Inhalt 11', 'Zusatzinformation - Art 12', 'Zusatzinformation- Inhalt 12', 'Zusatzinformation - Art 13', 'Zusatzinformation- Inhalt 13', 'Zusatzinformation - Art 14', 'Zusatzinformation- Inhalt 14', 'Zusatzinformation - Art 15', 'Zusatzinformation- Inhalt 15', 'Zusatzinformation - Art 16', 'Zusatzinformation- Inhalt 16', 'Zusatzinformation - Art 17', 'Zusatzinformation- Inhalt 17', 'Zusatzinformation - Art 18', 'Zusatzinformation- Inhalt 18', 'Zusatzinformation - Art 19', 'Zusatzinformation- Inhalt 19', 'Zusatzinformation - Art 20', 'Zusatzinformation- Inhalt 20', 'Stück', 'Gewicht', 'Zahlweise', 'Forderungsart', 'Veranlagungsjahr', 'Zugeordnete Fälligkeit', 'Skontotyp', 'Auftragsnummer', 'Buchungstyp', 'USt-Schlüssel (Anzahlungen)', 'EU-Land (Anzahlungen)', 'Sachverhalt L+L (Anzahlungen)', 'EU-Steuersatz (Anzahlungen)', 'Erlöskonto (Anzahlungen)', 'Herkunft-Kz', 'Buchungs GUID', 'KOST-Datum', 'SEPA-Mandatsreferenz', 'Skontosperre', 'Gesellschaftername', 'Beteiligtennummer', 'Identifikationsnummer', 'Zeichnernummer', 'Postensperre bis', 'Bezeichnung SoBil-Sachverhalt', 'Kennzeichen SoBil-Buchung', 'Festschreibung', 'Leistungsdatum', 'Datum Zuord. Steuerperiode', 'Fälligkeit', 'Generalumkehr (GU)', 'Steuersatz', 'Land', 'Abrechnungsreferenz', 'BVV-Position', 'EU-Land u. UStID (Ursprung)', 'EU-Steuersatz (Ursprung)', 'Abw. Skontokonto')


class CategoryAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str
    counter_account: str


class DatevRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    property_id: UUID
    accounting_scope_id: UUID | None = None
    period_start: date
    period_end: date
    fiscal_year_start: date
    chart: Literal['SKR03', 'SKR04']
    advisor_number: Annotated[str, Field(pattern=r'^[1-9][0-9]{3,6}$')]
    client_number: Annotated[str, Field(pattern=r'^[1-9][0-9]{0,4}$')]
    account_length: Annotated[int, Field(strict=True, ge=4, le=8)]
    bank_accounts: dict[UUID, str]
    payment_accounts: dict[str, str]
    category_accounts: dict[UUID, CategoryAccount]
    gross_accounts_reviewed: StrictBool
    expected_source_digest: Annotated[str | None, Field(pattern=r'^[a-f0-9]{64}$')] = None

    @model_validator(mode='after')
    def validate_configuration(self):
        if not 1001 <= int(self.advisor_number) <= 9999999:
            raise ValueError('Advisor number must be in the DATEV range 1001–9999999')
        if not self.gross_accounts_reviewed:
            raise ValueError('Explicitly confirm reviewed gross accounts without tax automation')
        if not (2000 <= self.fiscal_year_start.year <= 2099 and
                self.fiscal_year_start <= self.period_start <= self.period_end and
                self.period_end.year <= 2099):
            raise ValueError('Invalid DATEV fiscal year or batch period')
        next_year = self.fiscal_year_start.replace(year=self.fiscal_year_start.year + 1,
            day=min(self.fiscal_year_start.day, 28)) if self.fiscal_year_start.month == 2 and self.fiscal_year_start.day == 29 else self.fiscal_year_start.replace(year=self.fiscal_year_start.year + 1)
        if self.period_end >= next_year:
            raise ValueError('Batch must be contained in one fiscal year')
        accounts = [*self.bank_accounts.values(), *self.payment_accounts.values()]
        for pair in self.category_accounts.values():
            accounts.extend([pair.account, pair.counter_account])
            if pair.account == pair.counter_account:
                raise ValueError('Cost account and counter account must differ')
        if any(re.fullmatch(r'[1-9][0-9]{0,8}', account) is None or
               len(account) > self.account_length + 1 for account in accounts):
            raise ValueError('Invalid ledger account or incompatible account length')
        return self


@dataclass(frozen=True)
class DatevPosting:
    source_type: str
    source_id: UUID
    day: date
    amount: Decimal
    side: str
    account: str
    counter_account: str
    text: str

    def payload(self):
        return dict(source_type=self.source_type, source_id=str(self.source_id),
                    day=self.day.isoformat(), amount=format(self.amount, '.2f'), side=self.side,
                    account=self.account, counter_account=self.counter_account, text=self.text)


@dataclass(frozen=True)
class _Numeric:
    value: str


def _csv_line(values):
    # DATEV quotes text fields but not numbers; csv.QUOTE_NONNUMERIC would
    # convert Decimal to float on reading. Build the typed fields explicitly.
    def field(value):
        if isinstance(value, _Numeric):
            return value.value
        if isinstance(value, str):
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise HTTPException(422, 'Control characters are not permitted in DATEV text')
            return '"' + value.replace('"', '""') + '"'
        return '' if value is None else str(value)
    return ';'.join(field(v) for v in values) + '\r\n'


def validate_postings(rows: list[DatevPosting]) -> None:
    for posting in rows:
        if not isinstance(posting.amount, Decimal) or not posting.amount.is_finite() or not Decimal('0') < posting.amount <= Decimal('9999999999.99'):
            raise HTTPException(422, 'DATEV amount exceeds the supported positive ten-digit range')
        if posting.account == posting.counter_account:
            raise HTTPException(422, 'Account and counter account must differ')


def render_datev(config: DatevRequest, rows: list[DatevPosting], *, generated_at=None) -> bytes:
    validate_postings(rows)
    generated_at = generated_at or datetime.now(timezone.utc)
    timestamp = int(generated_at.strftime('%Y%m%d%H%M%S') + f'{generated_at.microsecond // 1000:03d}')
    header = ['EXTF', 700, 21, 'Buchungsstapel', 13, timestamp, None, '', 'Hausverwaltung', '',
              int(config.advisor_number), int(config.client_number), int(config.fiscal_year_start.strftime('%Y%m%d')),
              config.account_length, int(config.period_start.strftime('%Y%m%d')), int(config.period_end.strftime('%Y%m%d')),
              'Hausverwaltung Bestandsausgabe', '', 1, 0, 1, 'EUR', None, '', None, None,
              '03' if config.chart == 'SKR03' else '04', None, None, '', '']
    content = _csv_line(header) + _csv_line(HEADINGS)
    for posting in rows:
        line = [None] * len(HEADINGS)
        line[0] = _Numeric(format(posting.amount, '.2f').replace('.', ','))
        line[1:3] = [posting.side, 'EUR']
        line[6:8] = [int(posting.account), int(posting.counter_account)]
        line[9] = _Numeric(posting.day.strftime('%d%m'))
        line[10] = posting.source_id.hex
        line[13] = posting.text[:60]
        content += _csv_line(line)
    return content.encode('utf-8-sig')


class DatevExportService:
    def __init__(self, session, auth: AuthContext):
        self.session, self.auth = session, auth

    async def require_export(self, property_id, scope_id):
        if scope_id is not None:
            await AuthorizationService(self.session, self.auth).require(scope_id, ScopeCapability.EXPORT)
        for sql, params in [
            ("SELECT property_financial_disclosure_access(:org,:property,'export')", {'property':property_id}),
            ("SELECT report_scope_source_access(:org,:scope,'export')", {'scope':scope_id}),
        ]:
            if not await self.session.scalar(text(sql), {'org':self.auth.organization_id, **params}):
                raise HTTPException(404, 'Complete financial export sources not accessible')

    async def load(self, property_id, start, end, scope_id):
        prop, sphere = await ReportService(self.session, self.auth.organization_id, self.auth.user_id).property_period(
            property_id, start, end, scope_id)
        selected = sphere.id if sphere else None
        await self.require_export(prop.id, selected)
        payment_rows, cost_rows, banks, categories = await DatevExportRepository(
            self.session, self.auth.organization_id).rows(property_id, start, end)
        return prop, selected, payment_rows, cost_rows, banks, categories

    @staticmethod
    def totals(payments, costs):
        with localcontext() as context:
            context.prec = 40
            return dict(payment_total=format(sum((p.amount if p.direction == 'IN' else -p.amount for p in payments), Decimal('0.00')), '.2f'),
                        cost_total=format(sum((c.gross_amount for c in costs), Decimal('0.00')), '.2f'))

    async def sources(self, property_id, start, end, scope_id):
        prop, selected, payments, costs, banks, categories = await self.load(property_id, start, end, scope_id)
        await self.require_export(prop.id, selected)
        return dict(banks=[dict(id=str(r.id), label=r.label) for r in banks],
            categories=[dict(id=str(r.id), code=r.code, label=r.label) for r in categories],
            payment_types=sorted({p.payment_type for p in payments}), **self.totals(payments, costs))

    async def preview(self, config: DatevRequest):
        prop, scope, payments, costs, banks, categories = await self.load(config.property_id,
            config.period_start, config.period_end, config.accounting_scope_id)
        if set(config.bank_accounts) != {r.id for r in banks} or set(config.payment_accounts) != {p.payment_type for p in payments} or set(config.category_accounts) != {r.id for r in categories}:
            raise HTTPException(422, 'Provide exactly one explicit account mapping for every selected bank, payment type and category')
        rows = [DatevPosting('payment', p.id, p.value_date, p.amount,
                    'S' if p.direction == 'IN' else 'H', config.bank_accounts[p.bank_account_id],
                    config.payment_accounts[p.payment_type], p.payment_type) for p in payments]
        for c in costs:
            if c.gross_amount == 0:
                continue  # DATEV explicitly forbids zero-value postings.
            pair = config.category_accounts[c.cost_category_id]
            rows.append(DatevPosting('cost', c.id, c.invoice_date, c.gross_amount.copy_abs(),
                'S' if c.gross_amount > 0 else 'H', pair.account, pair.counter_account,
                next(r.code for r in categories if r.id == c.cost_category_id)))
        rows.sort(key=lambda r: (r.day, r.source_type, r.source_id))
        validate_postings(rows)
        # Include configuration as well as all source postings: changing accounts
        # after preview cannot silently download a different batch.
        payload = dict(configuration=config.model_dump(mode='json', exclude={'expected_source_digest'}), rows=[r.payload() for r in rows])
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        await self.require_export(prop.id, scope)
        return dict(rows=[r.payload() for r in rows], source_digest=digest, **self.totals(payments, costs)), rows, scope
