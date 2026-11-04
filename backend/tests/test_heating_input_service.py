"""Measured heating sources retain Decimal allocation without an estimated split."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from app.engine.heating_settlement import distribute_heating_position
from app.engine.types import CostPositionInput
from app.services import heating_input_service as service


@pytest.fixture
def measured_heating(monkeypatch):
    start, end = date(2026, 1, 1), date(2026, 12, 31)
    units = [SimpleNamespace(id=UUID(int=i), number=str(i), area=Decimal(70)) for i in (1, 2, 3)]
    leases = [SimpleNamespace(id=UUID(int=i + 10), unit_id=u.id,
        tenant_profile_id=UUID(int=i + 20), valid_from=start, valid_to=None)
        for i, u in enumerate(units)]
    meters = [SimpleNamespace(id=UUID(int=i + 30), unit_id=u.id, meter_type='HEIZUNG')
        for i, u in enumerate(units)]
    readings = {m.id: [SimpleNamespace(id=UUID(int=i * 2 + j + 40), reading_date=day,
        value=Decimal(value)) for j, (day, value) in enumerate([(start, 100), (end, 100 + quantity)])]
        for i, (m, quantity) in enumerate(zip(meters, (7, 13, 49)))}

    async def boundary_rows(self, meter_ids, *args, **kwargs):
        return readings[meter_ids[0]]

    monkeypatch.setattr(service, 'property_period_run', AsyncMock(side_effect=lambda *args: args[-1]))
    monkeypatch.setattr(service.MeterRepository, 'list_by_property', AsyncMock(return_value=meters))
    monkeypatch.setattr(service.MeterReadingRepository, 'list_boundary_readings', boundary_rows)
    runs = SimpleNamespace(leases_for_property=AsyncMock(return_value=leases),
        ownership_for_property=AsyncMock(return_value=[]))
    run = SimpleNamespace(property_id=UUID(int=90), period_start=start, period_end=end)
    entry = SimpleNamespace(id=UUID(int=91), service_period_from=start, service_period_to=end,
        tenant_apportionable='UMLAGEFAEHIG')
    config = service.HeatingConfig(entry.id, 'HEATING', Decimal('0.50'), 'AREA', True, False, 'HEIZUNG')
    return service.HeatingInputService(None, UUID(int=92), runs), run, entry, units, config, readings, meters


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['opening', 'closing', 'exact'])
async def test_unchanged_tenants_keep_decimal_cents_and_measured_provenance(measured_heating, boundary):
    resolver, run, entry, units, config, readings, meters = measured_heating
    if boundary == 'opening':
        readings[meters[0].id][0].reading_date = date(2025, 12, 31)
    elif boundary == 'closing':
        readings[meters[0].id][1].reading_date = date(2027, 1, 1)
    heating, sources = await resolver.resolve(run, entry, SimpleNamespace(code='HEIZUNG_ZENTRAL'), units, config)
    position = CostPositionInput(entry.id, None, 'HEIZUNG_ZENTRAL', 'Heizung', Decimal('4600.00'),
        run.period_start, run.period_end, 'EXTERNE_ABRECHNUNG', 'UMLAGEFAEHIG', heating=heating)
    _, allocation = distribute_heating_position(position)
    slices = {s.id: s.unit_id for s in heating.slices}
    amounts = {slices[row.unit_id]: row.consumption_amount for row in allocation.pool.rows}
    assert [amounts[u.id] for u in units] == [Decimal('233.33'), Decimal('433.34'), Decimal('1633.33')]
    assert all(s.consumption_split_method is None and s.consumption_weight is None for s in heating.slices)
    assert all('split' not in source and source['reason'] is None for source in sources.values())


@pytest.mark.asyncio
async def test_exact_interim_segment_with_nearest_annual_reading_has_no_fallback_metadata(measured_heating):
    resolver, run, entry, units, config, readings, meters = measured_heating
    readings[meters[0].id][0].reading_date = date(2025, 12, 31)
    readings[meters[0].id].append(SimpleNamespace(id=UUID(int=93), reading_date=date(2026, 6, 30), value=Decimal(103)))
    leases = resolver.runs.leases_for_property.return_value
    leases[0].valid_to = date(2026, 7, 1)
    leases.append(SimpleNamespace(id=UUID(int=94), unit_id=units[0].id, tenant_profile_id=UUID(int=95),
        valid_from=date(2026, 7, 1), valid_to=None))
    heating, sources = await resolver.resolve(run, entry, SimpleNamespace(code='HEIZUNG_ZENTRAL'), units, config)
    own = sorted((s for s in heating.slices if s.unit_id == units[0].id), key=lambda s:s.period_from)
    assert [s.consumption for s in own] == [Decimal(3), Decimal(4)]
    assert all(s.consumption_split_method is None and s.consumption_weight is None for s in own)
    assert all('split' not in source and source['reason'] is None for source in sources.values())
