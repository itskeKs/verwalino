"""Bind features/dunning_owner_weg_level.feature: one owner with units in two WEGs."""
from pathlib import Path
from uuid import uuid4

from pytest_bdd import given, parsers, scenarios, then, when

from tests.bdd.steps.browser_setup import create
from tests.settlement_period_support import period_data_cleanup as period_data_cleanup

scenarios(str(Path(__file__).resolve().parents[3] / "features" / "dunning_owner_weg_level.feature"))

GARTENSTRASSE = "Gartenstraße 5"


def weg(world, name):
    """Property, accounting scope and adopted resolution of one of the two WEGs of the feature."""
    if name == GARTENSTRASSE:
        return {"property": world.data["property"], "scope": world.data["scope"],
                "resolution": world.data["levy_resolution"]}
    return world.data["lindenweg"]


@given("Anna also owns the only unit of Lindenweg 2, a second WEG with an adopted roof repair resolution")
def second_weg(world, seed_data):
    user = str(seed_data["user_a_admin_id"])
    prop = create(world, "/api/properties", {
        "name": "Lindenweg 2", "management_type": "WEG",
        "data_audience": {"label": f"Second WEG {uuid4()}", "reason": "Synthetic second WEG",
            "grants": [{"user_id": user, "capabilities": ["read", "prepare", "approve", "export"],
                        "reason": "Synthetic second WEG"}]},
    }, keyed=True)
    answer = world.act("POST", f"/api/properties/{prop['id']}/mea-scale/confirm", json={
        "denominator": 1000, "expected_scale_status": "LEGACY_UNCONFIRMED",
        "confirm_existing_values_as_numerators": True})
    assert answer.status_code == 200, world.describe_response()
    scope = create(world, "/api/accounting-scopes", {
        "property_id": prop["id"], "scope_type": "WEG", "label": "WEG Lindenweg 2",
        "valid_from": "2026-01-01", "whole_property": True,
    }, keyed=True)
    create(world, f"/api/accounting-scopes/{scope['id']}/grants", {
        "user_id": user, "capabilities": ["read", "prepare", "approve", "confirm", "export"],
        "reason": "Synthetic second WEG",
    }, keyed=True)
    unit = create(world, "/api/units", {
        "property_id": prop["id"], "number": "1", "usage_type": "WOHNUNG", "mea": "1000", "area": "70"})
    create(world, "/api/ownership", {
        "unit_id": unit["id"], "owner_profile_id": world.data["levy_owners"][0]["id"], "valid_from": "2026-01-01"})
    meeting = create(world, "/api/meetings", {
        "property_id": prop["id"], "meeting_date": "2026-05-15", "meeting_type": "ORDENTLICH",
        "location": "Gemeinschaftsraum Lindenweg 2"})
    agenda = create(world, f"/api/meetings/{meeting['id']}/agenda", {
        "meeting_id": meeting["id"], "title": "Dachreparatur"})
    assert world.act("POST", f"/api/meetings/{meeting['id']}/hold").status_code == 200
    resolution = create(world, "/api/meetings/resolutions", {
        "meeting_id": meeting["id"], "agenda_item_id": agenda["id"],
        "wording": "Dachreparatur durch Sonderumlage von 4.000,00 € finanzieren.",
        "votes_yes": 1, "votes_no": 0, "votes_abstain": 0, "result": "ANGENOMMEN"})
    create(world, f"/api/meetings/resolutions/{resolution['id']}/register", {})
    world.data["lindenweg"] = {"property": prop, "scope": scope, "resolution": resolution}


@given(parsers.parse("a roof repair levy of {amount:d} euros falls due on 15 June 2026 in {name}"))
def june_levy(world, period_data_cleanup, amount, name):
    # An organization with special levies answers no unscoped statement read, so the records of
    # this scenario are released at its end for the features that run after it.
    where = weg(world, name)
    create(world, "/api/special-levies", {
        "property_id": where["property"]["id"], "accounting_scope_id": where["scope"]["id"],
        "amount": f"{amount}.00", "due_date": "2026-06-15", "resolution_id": where["resolution"]["id"]})


def create_notice(world, name, reference_date):
    response = world.act("POST", "/api/dunning/owner", json={
        "owner_profile_id": world.data["levy_owners"][0]["id"], "period_start": "2026-06-01",
        "period_end": "2026-06-30", "reference_date": reference_date,
        "accounting_scope_id": weg(world, name)["scope"]["id"]})
    assert response.status_code == 201, world.describe_response()
    return response.json()


@given(parsers.parse("Anna was reminded about her June levy in {name} on {day:d} June 2026"))
def reminded(world, name, day):
    notice = create_notice(world, name, f"2026-06-{day:02d}")
    issued = world.act("POST", f"/api/dunning/{notice['id']}/issue")
    assert issued.status_code == 200, world.describe_response()


@when(parsers.parse("the manager creates a notice for Anna's June levy arrears in {name} as of {day:d} June 2026"))
def create_june_notice(world, name, day):
    world.data["notice"] = create_notice(world, name, f"2026-06-{day:02d}")


@then(parsers.parse("the notice is a level {level:d} {kind} with {fee} fee and {total} total"))
def notice_level_fee_total(world, level, kind, fee, total):
    notice = world.data["notice"]
    assert (notice["level"], notice["fee_amount"], notice["total_amount"]) == (level, fee, total), notice
