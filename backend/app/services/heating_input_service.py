"""Resolve explicit BK heating intent within an already-authorized financial run.

No money calculation here. Exact boundary readings avoid using the same annual
quantity for successive tenants. Missing interim readings split known period consumption by days within the unit;
missing period consumption still requires explicit manual input.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException

from app.core.temporal import db_exclusive_to_engine_inclusive
from app.engine.meter_consumption import MeterConsumptionError, MeterReadingInput, boundary_reading, derive_consumption, split_consumption
from app.engine.periods import build_unit_cover, period_for_date, property_period_windows
from app.engine.types import HeatingPositionInput, HeatingSliceInput, LeasePeriodInput, OwnerPeriodInput
from app.repositories.meter_repo import MeterRepository, MeterReadingRepository


async def property_period_run(session, org_id, run):
    """The allocation context is the configured property year, never a lease window."""
    from app.repositories.property_repo import PropertyRepository

    prop = await PropertyRepository(session, org_id).get_by_id(run.property_id)
    if prop is None:
        raise HTTPException(404, "Property not found")
    start = period_for_date(prop.fiscal_year_start_month, run.period_start)[0]
    end = period_for_date(prop.fiscal_year_start_month, run.period_end)[1]
    return SimpleNamespace(property_id=run.property_id, period_start=start, period_end=end)


@dataclass
class HeatingSliceOverride:
    unit_id: uuid.UUID
    period_from: date
    period_to: date
    consumption: Decimal | None = None
    base_quantity: Decimal | None = None


@dataclass
class HeatingConfig:
    cost_entry_id: uuid.UUID
    kind: str
    consumption_fraction: Decimal
    base_key: str
    consumption_recorded: bool
    mandatory_seventy_percent: bool
    meter_type: str
    slices: list[HeatingSliceOverride] = field(default_factory=list)


class HeatingInputService:
    def __init__(self, session, org_id, runs):
        self.session, self.org_id, self.runs = session, org_id, runs

    async def candidates(self, run, entry, units, meter_type, base_key):
        if base_key not in {'AREA','HEATED_AREA','VOLUME'} or not meter_type.strip():
            raise HTTPException(422, 'Heizkosten: Grundbasis und explizite Messart erforderlich')
        context = await property_period_run(self.session, self.org_id, run)
        windows = property_period_windows(run.period_start, run.period_end, context.period_start.month)
        if len(windows) > 1:
            rows = []
            for start, end in windows:
                year_start, year_end = period_for_date(context.period_start.month, start)
                if entry.service_period_from > year_end or entry.service_period_to < year_start:
                    continue
                part = SimpleNamespace(property_id=run.property_id, period_start=start, period_end=end)
                rows.extend(await self.candidates(part, entry, units, meter_type, base_key))
            return rows
        leases = [LeasePeriodInput(l.id,l.unit_id,l.tenant_profile_id,l.valid_from,
                    db_exclusive_to_engine_inclusive(l.valid_to))
                  for l in await self.runs.leases_for_property(run.property_id)]
        owners = [OwnerPeriodInput(o.unit_id,o.owner_profile_id,o.valid_from,
                    db_exclusive_to_engine_inclusive(o.valid_to),o.mea_share)
                  for o in await self.runs.ownership_for_property(run.property_id)]
        start, end = context.period_start, context.period_end
        meters = await MeterRepository(self.session,self.org_id).list_by_property(run.property_id)
        repository = MeterReadingRepository(self.session,self.org_id)
        rows = []
        for unit in sorted(units,key=lambda u:str(u.id)):
            matches = [m for m in meters if m.unit_id==unit.id and m.meter_type.strip().upper()==meter_type.strip().upper()]
            covers = build_unit_cover(unit.id,start,end,leases,owners)
            period_readings = await repository.list_boundary_readings(
                [matches[0].id], start, end, include_interim=True) if len(matches)==1 else []
            period_readings = [MeterReadingInput(r.id,r.reading_date,r.value) for r in period_readings]
            split_error = None
            try:
                split = split_consumption(period_readings, start, end, covers)
                quantities = split.periods
            except MeterConsumptionError as exc:
                split_error = str(exc)
            def reading(r):
                return {'id':str(r.id),'reading_date':r.reading_date.isoformat(),'value':f'{r.value:.4f}'}

            for index, cover in enumerate(covers):
                physical = unit.area if base_key=='AREA' else unit.heated_area if base_key=='HEATED_AREA' else None
                item = {'unit_id':str(unit.id),'unit_label':unit.number,
                    'period_from':cover.start.isoformat(),'period_to':cover.end.isoformat(),
                    'lease_agreement_id':str(cover.lease_agreement_id) if cover.lease_agreement_id else None,
                    'owner_profile_id':str(cover.owner_profile_id) if cover.owner_profile_id else None,
                    'base_quantity':str(physical) if physical is not None else None,
                    'consumption':None,'meter_id':None,'start_reading':None,'end_reading':None,'reason':None}
                if len(matches)!=1:
                    item['reason']='Handeingabe erforderlich: kein eindeutig passender Einheitszähler.'
                else:
                    try:
                        # Both dates at an occupant boundary denote one counter
                        # value; period endpoints never compete as interim rows.
                        opening_dates = {start} if cover.start == start else {cover.start-timedelta(days=1), cover.start} - {start, end}
                        closing_dates = {end} if cover.end == end else {cover.end, cover.end+timedelta(days=1)} - {start, end}
                        first = boundary_reading([r for r in period_readings if r.reading_date in opening_dates])
                        last = boundary_reading([r for r in period_readings if r.reading_date in closing_dates])
                        measured = derive_consumption([first, last], first.reading_date, last.reading_date)
                    except MeterConsumptionError:
                        if split_error is None:
                            first, last, days = split.bases[index]
                            annual = split.measured
                            label = 'Periodenbasis' if (first, last) == (annual.start_reading, annual.end_reading) else 'Abschnittsbasis'
                            item.update(consumption=str(quantities[index][2]),
                                meter_id=str(matches[0].id), start_reading=reading(first),
                                end_reading=reading(last),
                                reason=f'{split.reason or "Aufteilung nach Tagen."} {label}: {first.reading_date} → {last.reading_date}; Tagesanteil {cover.days}/{days}.')
                        else:
                            item['reason']=split_error
                    else:
                        item.update(consumption=f'{measured.consumption:.4f}',meter_id=str(matches[0].id),
                            start_reading=reading(measured.start_reading),end_reading=reading(measured.end_reading))
                rows.append(item)
        return rows

    async def resolve(self, run, entry, category, units, config):
        expected = {'HEIZUNG_ZENTRAL':'HEATING','WARMWASSER_ZENTRAL':'HOT_WATER'}
        if category is None or expected.get(category.code)!=config.kind or entry.tenant_apportionable=='NICHT_UMLAGEFAEHIG':
            raise HTTPException(422,'Heizkosten: Kostenquelle und getrennte Heiz-/Warmwasserart passen nicht zusammen')
        candidates = await self.candidates(run,entry,units,config.meter_type,config.base_key)
        identities = {(r['unit_id'],r['period_from'],r['period_to']) for r in candidates}
        overrides = {}
        for supplied in config.slices:
            key = (str(supplied.unit_id),supplied.period_from.isoformat(),supplied.period_to.isoformat())
            if key not in identities or key in overrides:
                raise HTTPException(422,'Heizkosten: unbekannter oder doppelter Nutzungsabschnitt')
            overrides[key]=supplied
        slices, sources = [], {}
        for row in candidates:
            key=(row['unit_id'],row['period_from'],row['period_to'])
            supplied=overrides.get(key)
            base = supplied.base_quantity if supplied and supplied.base_quantity is not None else Decimal(row['base_quantity']) if row['base_quantity'] is not None else None
            manual = supplied is not None and supplied.consumption is not None
            if not config.consumption_recorded and manual:
                raise HTTPException(422,'Heizkosten: ohne Verbrauchserfassung darf kein Verbrauch ignoriert werden')
            consumption = (supplied.consumption if manual else Decimal(row['consumption']) if row['consumption'] is not None else None) if config.consumption_recorded else None
            if base is None or (config.consumption_recorded and consumption is None):
                raise HTTPException(422,f"Heizkosten Einheit {row['unit_label']}: Grundbasis/Verbrauch fehlt; {row['reason'] or 'Handeingabe erforderlich'}")
            identity=uuid.uuid5(entry.id,':'.join(key))
            slices.append(HeatingSliceInput(identity,uuid.UUID(row['unit_id']),date.fromisoformat(row['period_from']),
                date.fromisoformat(row['period_to']),uuid.UUID(row['lease_agreement_id']) if row['lease_agreement_id'] else None,
                uuid.UUID(row['owner_profile_id']) if row['owner_profile_id'] else None,base,consumption))
            sources[str(identity)] = ({'source':'MANUAL','consumption':str(consumption)} if manual
                else {'source':'METER',**row} if config.consumption_recorded else {'source':'NON_CONSUMPTION'})
            sources[str(identity)]['base_source']='MANUAL' if supplied and supplied.base_quantity is not None else config.base_key
        return HeatingPositionInput(config.kind,config.consumption_fraction,config.base_key,
            config.consumption_recorded,config.mandatory_seventy_percent,tuple(slices)), sources
