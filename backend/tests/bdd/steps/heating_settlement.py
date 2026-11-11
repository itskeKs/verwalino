"""Heating tenant-change checks through the public API and finalized PDFs."""

import subprocess

from pytest_bdd import given, when, then, parsers

from tests.financial_coverage_helpers import attest_fixture
from tests.settlement_period_support import create, period_property, statement
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup


@given("a unit with a tenant change on 1 July 2026 and annual heating and hot-water readings")
def heating_tenant_change(world, period_data_cleanup):
    _heating_tenant_change(world, "4000.00")


@given("a unit with a midyear tenant change and annual heating costs of 23 euros")
def small_heating_change(world, period_data_cleanup):
    _heating_tenant_change(world, "23.00")


def _heating_tenant_change(world, heating_total):
    data = world.run(period_property(world.actor, month=1))
    response = world.act("PATCH", f"/api/leases/{data['lease']['id']}", json={"valid_to": "2026-06-30"})
    assert response.status_code == 200, world.describe_response()
    incoming = world.run(create(world.actor, "/api/leases", {
        "unit_id": data["unit"]["id"], "tenant_profile_id": data["tenant"]["id"],
        "valid_from": "2026-07-01", "base_rent": "700.00", "advance_payment": "0.00",
    }))
    categories = world.act("GET", "/api/cost-categories")
    assert categories.status_code == 200, world.describe_response()
    inputs, meters = [], []
    # 50% consumption: EUR 2,000 heating and EUR 400 hot water. Basic pools
    # have the same amounts, so their unchanged daily shares are observable.
    for code, kind, meter_type, amount in (
        ("HEIZUNG_ZENTRAL", "HEATING", "HEIZUNG", heating_total),
        ("WARMWASSER_ZENTRAL", "HOT_WATER", "WARMWASSER", "800.00"),
    ):
        category = next(c for c in categories.json() if c["code"] == code)
        entry = world.run(create(world.actor, "/api/cost-entries", {
            "property_id": data["property"]["id"], "cost_category_id": category["id"],
            "invoice_date": "2026-12-31", "service_period_from": "2026-01-01",
            "service_period_to": "2026-12-31", "gross_amount": amount,
            "tenant_distribution_key": "EXTERNE_ABRECHNUNG",
            "tenant_apportionable": "UMLAGEFAEHIG_EXTERN_PRUEFEN",
            "apportionable_tenant_amount": amount,
        }))
        response = world.act("POST", f"/api/cost-entries/{entry['id']}/confirm")
        assert response.status_code == 200, world.describe_response()
        meter = world.run(create(world.actor, "/api/meters", {
            "property_id": data["property"]["id"], "unit_id": data["unit"]["id"],
            "meter_number": meter_type, "meter_type": meter_type,
        }))
        meters.append(meter)
        for day, value in (("2026-01-01", "100"), ("2026-12-31", "1100")):
            world.run(create(world.actor, f"/api/meters/{meter['id']}/readings",
                {"reading_date": day, "value": value}))
        inputs.append({"cost_entry_id": entry["id"], "kind": kind,
            "consumption_fraction": "0.50", "base_key": "AREA", "consumption_recorded": True,
            "mandatory_seventy_percent": False, "meter_type": meter_type, "slices": []})
    world.data["heating_change"] = {**data, "leases": [data["lease"], incoming],
        "inputs": {"heating_inputs": inputs}, "meters": meters}


@given("an exact heat reading at the tenant change records 40 percent of consumption")
def exact_heat_reading(world):
    meter = world.data["heating_change"]["meters"][0]
    world.run(create(world.actor, f"/api/meters/{meter['id']}/readings",
        {"reading_date": "2026-06-30", "value": "500"}))


@when("the manager finalizes both tenants' internal heating statements")
def finalize_heating_change(world):
    data = world.data["heating_change"]
    results, pdfs = [], []
    for lease in data["leases"]:
        run = world.run(create(world.actor, "/api/settlements", {
            **statement({**data, "lease": lease}, "2026-01-01", "2026-12-31"),
            "advance_payments_total": "0.00", "heating_advance_payments_total": "0.00",
        }))
        world.run(attest_fixture(world.actor, "settlement", run["id"], {
            "advance_payments_total": "0.00", "advance_payments_source": "MANUAL",
            "heating_advance_payments_total": "0.00", "heating_advance_payments_source": "MANUAL",
        }))
        response = world.act("POST", f"/api/settlements/{run['id']}/finalize", json=data["inputs"])
        assert response.status_code == 200, world.describe_response()
        results.append(response.json())
        response = world.act("GET", f"/api/settlements/{run['id']}/pdf")
        assert response.status_code == 200, world.describe_response()
        pdfs.append(subprocess.run(["pdftotext", "-", "-"], input=response.content,
            capture_output=True, check=True).stdout.decode())
    world.data.update(heating_results=results, heating_pdfs=pdfs)


@then(parsers.parse("heating consumption costs are {outgoing} and {incoming} euros"))
def heating_amounts(world, outgoing, incoming):
    # These totals include the separately checked day-based basic costs.
    from decimal import Decimal

    for result, consumption, base in zip(world.data["heating_results"],
                                        (outgoing, incoming), ("991.78", "1008.22")):
        line = next(r for r in result["lines"] if r["cost_category_code"] == "HEIZUNG_ZENTRAL")
        assert Decimal(line["amount"]) == Decimal(consumption) + Decimal(base)
    for pdf, consumption, base in zip(world.data["heating_pdfs"],
                                     (outgoing, incoming), ("991.78", "1008.22")):
        assert consumption.replace(".", ",") in pdf.replace(".", "")
        assert base.replace(".", ",") in pdf.replace(".", "")


@then("hot-water consumption costs are 198.36 and 201.64 euros")
def hot_water_amounts(world):
    for result, total in zip(world.data["heating_results"], ("396.72", "403.28")):
        line = next(r for r in result["lines"] if r["cost_category_code"] == "WARMWASSER_ZENTRAL")
        assert line["amount"] == total
    for pdf, consumption in zip(world.data["heating_pdfs"], ("198,36", "201,64")):
        assert consumption in pdf


@then("both PDFs explain the degree-day heating split and the daily hot-water split")
def heating_fallback_notes(world):
    for pdf in world.data["heating_pdfs"]:
        compact = " ".join(pdf.split())
        assert "Aufteilung nach Gradtagszahlen (§ 9b HeizkostenV)" in compact, compact
        assert "Gradtagsanteil" in compact
        assert "Aufteilung des Einheitsverbrauchs nach Tagen." in compact


@then("both PDFs retain the interim reading without a degree-day fallback")
def heating_interim_notes(world):
    for pdf in world.data["heating_pdfs"]:
        assert "2026-06-30: 500.0000" in " ".join(pdf.split())
        assert "Gradtagszahlen" not in pdf


@then("heating consumption costs are 6.71 and 4.79 euros with the exact tied cent assigned to the larger share")
def small_heating_money_tie(world):
    for result, pdf, amount, consumption, base in zip(world.data['heating_results'],
        world.data['heating_pdfs'], ('12.41', '10.59'), ('6,71', '4,79'), ('5,70', '5,80')):
        line = next(r for r in result['lines'] if r['cost_category_code'] == 'HEIZUNG_ZENTRAL')
        assert line['amount'] == amount
        assert consumption in pdf and base in pdf
