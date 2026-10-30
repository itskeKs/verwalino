"""Browser setup API boundaries."""
import pytest
from sqlalchemy import text
from tests.setup_support import BODY, fresh_setup  # noqa: F401


@pytest.mark.asyncio
async def test_setup_creates_an_authenticated_owner_and_then_disappears(fresh_setup):
    client, _ = fresh_setup
    assert (await client.get("/api/setup")).json() == {"required": True}
    response = await client.post("/api/setup", json=BODY)
    assert response.status_code == 201, response.text
    assert response.json()["role"] == "OWNER_ADMIN"
    session = await client.get("/api/auth/session")
    assert session.status_code == 200, session.text
    assert session.json() == response.json()
    assert (await client.get("/api/setup")).json() == {"required": False}
    unknown = await client.get("/api/not-a-route")
    second = await client.post("/api/setup", json=BODY)
    assert (second.status_code, second.json()) == (404, unknown.json())


@pytest.mark.asyncio
async def test_closed_setup_hides_even_malformed_json(fresh_setup):
    client, _ = fresh_setup
    assert (await client.post("/api/setup", json=BODY)).status_code == 201
    for content in ("{", "null", "{}", "[]"):
        unknown = await client.get("/api/not-a-route")
        closed = await client.post("/api/setup", content=content, headers={"content-type": "application/json"})
        assert (closed.status_code, closed.json()) == (404, unknown.json())


@pytest.mark.asyncio
@pytest.mark.parametrize("spa", [False, True])
async def test_closed_setup_matches_unknown_routes_for_every_write_method(fresh_setup, monkeypatch, tmp_path, spa):
    import importlib
    import httpx
    import app.main as main

    client, _ = fresh_setup
    assert (await client.post("/api/setup", json=BODY)).status_code == 201
    (tmp_path / "index.html").write_text("<html>test frontend</html>")
    original_app, original_dist = main.app, main._frontend_dist
    try:
        with monkeypatch.context() as environment:
            if spa:
                environment.setenv("HV_FRONTEND_DIST", str(tmp_path))
            else:
                environment.delenv("HV_FRONTEND_DIST", raising=False)
            importlib.reload(main)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as production:
                unknown = await production.get("/api/not-a-route")
                for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"):
                    for content in ("{}", "{"):
                        closed = await production.request(method, "/api/setup", content=content, headers={"content-type": "application/json"})
                        assert closed.status_code == 404, (method, closed.text)
                        if method == "HEAD":
                            assert closed.content == b""
                            assert closed.headers["content-length"] == unknown.headers["content-length"]
                        else:
                            assert closed.json() == unknown.json()
    finally:
        main.app, main._frontend_dist = original_app, original_dist


@pytest.mark.asyncio
async def test_used_token_stays_consumed_even_if_a_migrator_removes_the_organization(fresh_setup):
    client, connection = fresh_setup
    assert (await client.post("/api/setup", json=BODY)).status_code == 201
    await connection.execute(text("RESET ROLE"))
    assert await connection.scalar(text("SELECT used_at IS NOT NULL FROM setup_token"))
    await connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await connection.execute(text("TRUNCATE organization CASCADE"))
    await connection.execute(text("SET LOCAL ROLE app_rw"))
    response = await client.post("/api/setup", json=BODY)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_setup_errors_and_validation_never_echo_secrets(fresh_setup, caplog):
    from app.api.setup import SetupRequest
    client, _ = fresh_setup
    assert BODY["token"] not in repr(SetupRequest(**BODY))
    assert BODY["password"] not in repr(SetupRequest(**BODY))
    for changes in ({"password": "short"}, {"token": {"secret": BODY["token"]}}, {"token": "wrong"}):
        response = await client.post("/api/setup", json=BODY | changes)
        assert response.status_code in (403, 422)
        assert BODY["token"] not in response.text + caplog.text
        assert BODY["password"] not in response.text + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("token", ["00" * 32, "", "12" * 31, "ä" * 64])
async def test_wrong_tokens_get_one_generic_refusal_without_creating_rows(fresh_setup, token):
    client, connection = fresh_setup
    response = await client.post("/api/setup", json=BODY | {"token": token})
    assert (response.status_code, response.json()) == (403, {"detail": "Einrichtung nicht möglich. Bitte prüfen Sie den Einrichtungs-Token."})
    assert await connection.scalar(text("SELECT count(*) FROM public.organization")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["expired", "used", "missing"])
async def test_unusable_tokens_have_the_same_refusal(fresh_setup, state):
    client, connection = fresh_setup
    await connection.execute(text("RESET ROLE"))
    query = {"expired": "UPDATE setup_token SET created_at=now()-interval '24 hours'",
             "used": "UPDATE setup_token SET used_at=now()", "missing": "DELETE FROM setup_token"}[state]
    await connection.execute(text(query))
    await connection.execute(text("SET LOCAL ROLE app_rw"))
    answer = await client.post("/api/setup", json=BODY)
    assert (answer.status_code, answer.json()) == (403, {"detail": "Einrichtung nicht möglich. Bitte prüfen Sie den Einrichtungs-Token."})


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"organization_name": " "}, {"organization_name": "a" * 256},
    {"admin_name": " "}, {"admin_name": "a" * 256},
    {"admin_email": "bad-address"}, {"admin_email": "a" * 321},
    {"password": "short"}, {"password": "a" * 129},
    *({field: "before" + control + "after"}
      for field in ("token", "organization_name", "admin_name")
      for control in ("\x00", "\x1f", "\x7f", "\x85")),
])
async def test_setup_rejects_invalid_input_without_consuming_the_token(fresh_setup, changes):
    client, connection = fresh_setup
    response = await client.post("/api/setup", json=BODY | changes)
    assert response.status_code == 422, response.text
    assert await connection.scalar(text("SELECT count(*) FROM organization")) == 0
    assert (await client.post("/api/setup", json=BODY)).status_code == 201


@pytest.mark.asyncio
async def test_setup_strips_pasted_token_and_names_in_the_request_model(fresh_setup):
    from app.api.setup import SetupRequest

    client, connection = fresh_setup
    padded = BODY | {field: "  " + BODY[field] + "  " for field in ("token", "organization_name", "admin_name")}
    body = SetupRequest(**padded)
    assert body.token.get_secret_value() == BODY["token"]
    assert body.organization_name == BODY["organization_name"]
    assert body.admin_name == BODY["admin_name"]
    response = await client.post("/api/setup", json=padded)
    assert response.status_code == 201, response.text
    await connection.execute(text("RESET ROLE"))
    assert await connection.scalar(text("SELECT name FROM organization")) == BODY["organization_name"]
    assert await connection.scalar(text('SELECT display_name FROM "user" WHERE email=\'setup@example.test\'')) == BODY["admin_name"]


@pytest.mark.asyncio
async def test_setup_database_errors_hide_only_setup_parameters(fresh_setup):
    from fastapi import HTTPException, Response
    from app.api.setup import SetupRequest, complete_setup

    with pytest.raises(HTTPException) as error:
        await complete_setup(SetupRequest(**(BODY | {"token": "private-wrong-token"})), Response())
    assert error.value.status_code == 403
    database_error = error.value.__context__
    assert database_error.hide_parameters is True
    assert "private-wrong-token" not in str(database_error)
