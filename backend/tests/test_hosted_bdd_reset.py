"""Hosted BDD features start from the seeded organizations, not from earlier features' records."""

from tests.bdd.conftest import reset_shared_hosted_records, shared_property_counts


async def test_reset_removes_scenario_properties_and_keeps_the_seeded_identities(
    client_a_admin, client_b_admin, seed_data
):
    for client in (client_a_admin, client_b_admin):
        for index in range(3):
            created = await client.post("/api/properties", json={"name": f"Haus {index}", "management_type": "MIET"})
            assert created.status_code == 201, created.text
    assert min(shared_property_counts(seed_data).values()) >= 3

    reset_shared_hosted_records()

    assert shared_property_counts(seed_data) == {"Verwaltung Alpha": 0, "Verwaltung Beta": 0}
    for client in (client_a_admin, client_b_admin):
        listed = await client.get("/api/properties")
        assert listed.status_code == 200, listed.text
        assert listed.json() == []
