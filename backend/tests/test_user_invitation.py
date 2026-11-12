"""User invitations: single-use issuance, acceptance, refusal and revocation."""

import re
import uuid
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

INVITED_PASSWORD = "InviteePassword789!"
REFUSED = {
    "detail": "Diese Einladung ist nicht mehr gültig. Bitte wenden Sie sich an Ihre Verwaltung."
}
SUBJECT = "Verwalino: Einladung von Verwaltung Alpha"


def unique(email):
    local, domain = email.split("@")
    return f"{local}-{uuid.uuid4().hex[:8]}@{domain}"


def invitation_token(mail, organization="Verwaltung Alpha"):
    message = BytesParser(policy=policy.default).parsebytes(mail.messages[-1][0])
    assert message["Subject"] == f"Verwalino: Einladung von {organization}"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "72 Stunden" in body
    link = re.search(r"https://signup.example.test/einladung\?token=\S+", body)[0]
    return parse_qs(urlsplit(link).query)["token"][0]


async def invite(client, email, role="CLERK"):
    answer = await client.post(
        "/api/users/invitations", json={"email": email, "role": role}
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


@pytest.mark.asyncio
async def test_definer_has_fixed_metadata_and_only_runtime_execute(app_rw_session):
    row = (
        await app_rw_session.execute(
            text(
                "SELECT pg_get_userbyid(proowner),prosecdef,proconfig FROM pg_proc "
                "WHERE oid='public.accept_user_invitation(text,text)'::regprocedure"
            )
        )
    ).one()
    assert tuple(row) == (
        "app_migrator",
        True,
        ["search_path=pg_catalog, pg_temp", "row_security=off"],
    )
    grants = (
        await app_rw_session.execute(
            text("""SELECT pg_get_userbyid(a.grantee),a.privilege_type,a.is_grantable
        FROM pg_proc p CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE p.oid='public.accept_user_invitation(text,text)'::regprocedure AND a.grantee<>p.proowner""")
        )
    ).all()
    assert grants == [("app_rw", "EXECUTE", False)]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["selfhost", "hosted"])
async def test_invited_user_sets_password_and_signs_in_with_invited_role(
    client_a_admin, client_unauth, signup_mail, monkeypatch, mode
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "deployment_mode", mode)
    email = unique(f"kollegin-{mode}@example.test")
    await invite(client_a_admin, email, "CLERK")
    token = invitation_token(signup_mail)
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert answer.status_code == 204, answer.text
    assert "set-cookie" not in answer.headers
    login = await client_unauth.post(
        "/api/auth/login",
        json={"email": email, "password": INVITED_PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "CLERK"
    again = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert again.status_code == 403
    assert again.json() == REFUSED


@pytest.mark.asyncio
async def test_expired_invitation_is_refused_in_german(
    client_a_admin, client_unauth, signup_mail
):
    from tests.password_reset_support import reset_store

    invitation = await invite(client_a_admin, unique("alt@example.test"))
    async with reset_store() as session:
        await session.execute(
            text(
                "UPDATE user_invitation SET expires_at=now()-interval '1 minute' WHERE id=:id"
            ),
            {"id": invitation["id"]},
        )
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": invitation_token(signup_mail), "password": INVITED_PASSWORD},
    )
    assert answer.status_code == 403
    assert answer.json() == REFUSED


@pytest.mark.asyncio
async def test_revoked_invitation_is_refused_and_resend_rotates_the_token(
    client_a_admin, client_unauth, signup_mail
):
    invitation = await invite(client_a_admin, unique("wechsel@example.test"))
    first_token = invitation_token(signup_mail)
    answer = await client_a_admin.post(
        f"/api/users/invitations/{invitation['id']}/resend"
    )
    assert answer.status_code == 200, answer.text
    second_token = invitation_token(signup_mail)
    assert second_token != first_token
    old = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": first_token, "password": INVITED_PASSWORD},
    )
    assert (old.status_code, old.json()) == (403, REFUSED)
    fresh = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": second_token, "password": INVITED_PASSWORD},
    )
    assert fresh.status_code == 204, fresh.text
    again = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": second_token, "password": INVITED_PASSWORD},
    )
    assert (again.status_code, again.json()) == (403, REFUSED)
    other = await invite(client_a_admin, unique("widerruf@example.test"))
    third_token = invitation_token(signup_mail)
    revoked = await client_a_admin.delete(f"/api/users/invitations/{other['id']}")
    assert revoked.status_code == 204, revoked.text
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": third_token, "password": INVITED_PASSWORD},
    )
    assert (answer.status_code, answer.json()) == (403, REFUSED)
    missing = await client_a_admin.delete(f"/api/users/invitations/{other['id']}")
    assert missing.status_code == 404
    unknown = await client_a_admin.post(f"/api/users/invitations/{other['id']}/resend")
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_create_refuses_existing_addresses_and_pending_duplicates(
    client_a_admin, signup_mail
):
    taken = await client_a_admin.post(
        "/api/users/invitations", json={"email": "ADMIN@alpha.de", "role": "CLERK"}
    )
    assert taken.status_code == 409
    assert taken.json() == {"detail": "Diese E-Mail-Adresse wird bereits verwendet."}
    doubled = unique("doppelt@example.test")
    await invite(client_a_admin, doubled)
    duplicate = await client_a_admin.post(
        "/api/users/invitations",
        json={"email": doubled, "role": "CLERK"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": "Für diese Adresse besteht bereits eine offene Einladung."
    }


@pytest.mark.asyncio
async def test_create_refuses_addresses_of_any_organization(
    client_a_admin, client_b_admin, signup_mail
):
    foreign = await client_a_admin.post(
        "/api/users/invitations",
        json={"email": "admin@beta.de", "role": "CLERK"},
    )
    assert foreign.status_code == 409
    assert foreign.json() == {"detail": "Diese E-Mail-Adresse wird bereits verwendet."}
    own = await client_a_admin.post(
        "/api/users/invitations",
        json={"email": "admin@alpha.de", "role": "CLERK"},
    )
    assert (own.status_code, own.json()) == (foreign.status_code, foreign.json())
    listing = await client_a_admin.get("/api/users/invitations")
    assert "admin@beta.de" not in [item["email"] for item in listing.json()]
    assert signup_mail.messages == []


@pytest.mark.asyncio
async def test_accept_refuses_when_the_address_was_created_as_a_user(
    client_a_admin, client_unauth, signup_mail
):
    from tests.password_reset_support import reset_store

    taken = unique("vergeben@example.test")
    await invite(client_a_admin, taken)
    token = invitation_token(signup_mail)
    created = await client_a_admin.post(
        "/api/users",
        json={
            "email": taken,
            "password": "StartPasswort123!",
            "role": "CLERK",
        },
    )
    assert created.status_code == 201, created.text
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert (answer.status_code, answer.json()) == (403, REFUSED)
    async with reset_store() as session:
        leftovers = await session.scalar(
            text('SELECT count(*) FROM "user" WHERE lower(btrim(email))=lower(:email)'),
            {"email": taken},
        )
    assert leftovers == 1  # only the account created above; no second user


@pytest.mark.asyncio
async def test_clerk_cannot_invite_and_sees_no_pending_invitations(client_a_clerk):
    answer = await client_a_clerk.post(
        "/api/users/invitations",
        json={"email": unique("heheim@example.test"), "role": "CLERK"},
    )
    assert answer.status_code == 403
    listing = await client_a_clerk.get("/api/users/invitations")
    assert listing.status_code == 403


@pytest.mark.asyncio
async def test_pending_list_is_scoped_to_the_organization(
    client_a_admin, client_b_admin, signup_mail
):
    ours_email = unique("nur-alpha@example.test")
    await invite(client_a_admin, ours_email)
    ours = await client_a_admin.get("/api/users/invitations")
    assert ours.status_code == 200
    entry = next(item for item in ours.json() if item["email"] == ours_email)
    assert entry["role"] == "CLERK"
    foreign = await client_b_admin.get("/api/users/invitations")
    assert foreign.status_code == 200
    assert foreign.json() == []


@pytest.mark.asyncio
async def test_invitation_requires_mail_configuration(client_a_admin, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "smtp_host", None)
    answer = await client_a_admin.post(
        "/api/users/invitations",
        json={"email": unique("ohne-mail@example.test"), "role": "CLERK"},
    )
    assert answer.status_code == 409


@pytest.mark.asyncio
async def test_recipient_window_refuses_the_fourth_send(client_a_admin, signup_mail):
    invitation = await invite(client_a_admin, unique("haeufig@example.test"))
    for _ in range(2):
        resent = await client_a_admin.post(
            f"/api/users/invitations/{invitation['id']}/resend"
        )
        assert resent.status_code == 200, resent.text
    refused = await client_a_admin.post(
        f"/api/users/invitations/{invitation['id']}/resend"
    )
    assert refused.status_code == 429


@pytest.mark.asyncio
async def test_invite_revoke_and_resend_are_audit_logged(
    client_a_admin, signup_mail, migrator_session, seed_data
):
    invitation = await invite(client_a_admin, unique("audit@example.test"))
    await client_a_admin.post(f"/api/users/invitations/{invitation['id']}/resend")
    await client_a_admin.delete(f"/api/users/invitations/{invitation['id']}")
    rows = (
        await migrator_session.execute(
            text(
                "SELECT action FROM audit_event WHERE entity_type='user_invitation' "
                "AND entity_id=:id ORDER BY occurred_at, action"
            ),
            {"id": invitation["id"]},
        )
    ).all()
    assert [row.action for row in rows] == ["INVITE", "RESEND", "REVOKE"]
    foreign = (
        await migrator_session.execute(
            text(
                "SELECT count(*) FROM audit_event WHERE entity_type='user_invitation' "
                "AND organization_id=:org"
            ),
            {"org": seed_data["org_b_id"]},
        )
    ).scalar()
    assert foreign == 0


@pytest.mark.asyncio
async def test_audit_rows_of_the_invitation_flow_never_contain_the_invitee_address(
    client_a_admin, client_unauth, signup_mail, seed_data
):
    from tests.password_reset_support import reset_store

    # Three issued mails per address are allowed; each address uses two.
    resent = unique("audit-erneut@example.test")
    revoked = unique("audit-widerrufen@example.test")
    expired = unique("audit-abgelaufen@example.test")
    accepted = unique("audit-angenommen@example.test")
    first = await invite(client_a_admin, resent)
    assert (
        await client_a_admin.post(f"/api/users/invitations/{first['id']}/resend")
    ).status_code == 200
    second = await invite(client_a_admin, revoked)
    assert (
        await client_a_admin.delete(f"/api/users/invitations/{second['id']}")
    ).status_code == 204
    assert (await invite(client_a_admin, revoked))["id"] != second["id"]
    third = await invite(client_a_admin, expired)
    async with reset_store() as session:
        await session.execute(
            text(
                "UPDATE user_invitation SET expires_at=now()-interval '1 minute' WHERE id=:id"
            ),
            {"id": third["id"]},
        )
    # Re-inviting an expired pending invitation rotates it and logs RESEND.
    assert (await invite(client_a_admin, expired))["id"] == third["id"]
    await invite(client_a_admin, accepted)
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": invitation_token(signup_mail), "password": INVITED_PASSWORD},
    )
    assert answer.status_code == 204, answer.text
    org = str(seed_data["org_a_id"])
    async with reset_store() as session:
        actions = (
            await session.scalars(
                text(
                    "SELECT action FROM audit_event WHERE organization_id=:org "
                    "AND entity_type='user_invitation' ORDER BY occurred_at, id"
                ),
                {"org": org},
            )
        ).all()
        # Every column of every audit row of the organization, as text.
        leaks = [
            (address, row.entity_type, row.action)
            for address in (resent, revoked, expired, accepted)
            for row in (
                await session.execute(
                    text(
                        "SELECT a.entity_type, a.action FROM audit_event a "
                        "WHERE a.organization_id=:org AND position(:needle IN "
                        "lower(row_to_json(a)::text))>0"
                    ),
                    {"org": org, "needle": address.split("@")[0].lower()},
                )
            ).all()
        ]
    assert actions.count("INVITE") >= 5
    assert actions.count("RESEND") >= 2
    assert actions.count("REVOKE") >= 1
    assert leaks == []


@pytest.mark.asyncio
async def test_tenant_context_refuses_the_accept_definer_without_changing_the_invitation(
    client_a_admin, client_unauth, signup_mail, app_rw_session, seed_data
):
    invitation = await invite(client_a_admin, unique("kontext@example.test"))
    token = invitation_token(signup_mail)
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(seed_data["org_a_id"])},
    )
    with pytest.raises(DBAPIError, match="Invitation acceptance refused"):
        async with app_rw_session.begin_nested():
            await app_rw_session.execute(
                text("SELECT * FROM public.accept_user_invitation(:token, :hash)"),
                {"token": token, "hash": "$argon2id$v=19$m=65536,t=3,p=4$c2FsdA$hash"},
            )
    await app_rw_session.execute(
        text("SELECT set_config('app.current_org_id', '', false)")
    )
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert answer.status_code == 204, answer.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"token": "0" * 64, "password": INVITED_PASSWORD},
        {"token": "0" * 63, "password": INVITED_PASSWORD},
        {"token": "0" * 64, "password": "short"},
        {"email": "a@b.de", "role": "CLERK", "website": ""},
    ],
)
@pytest.mark.parametrize(
    "path", ["/api/auth/invitation/accept", "/api/users/invitations"]
)
async def test_malformed_bodies_are_refused_without_echoing_secrets(
    client_unauth, body, path
):
    answer = await client_unauth.post(path, json=body)
    assert answer.status_code in (401, 403, 404, 409, 422)
    assert INVITED_PASSWORD not in answer.text


@pytest.mark.asyncio
async def test_import_admission_ignores_invitation_rows():
    import json

    import psycopg2

    from tests.conftest import APP_RW_URL, MIGRATOR_SYNC_URL

    org, user = uuid.uuid4(), uuid.uuid4()
    with (
        psycopg2.connect(MIGRATOR_SYNC_URL) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO organization(id,name) VALUES (%s,'Import admission invitation')",
            (str(org),),
        )
        cursor.execute(
            'INSERT INTO "user"(id,organization_id,email,password_hash) '
            "VALUES (%s,%s,'import-admin@example.test','synthetic-not-a-login')",
            (str(user), str(org)),
        )
        cursor.execute(
            "INSERT INTO membership(organization_id,user_id,role) "
            "VALUES (%s,%s,'OWNER_ADMIN')",
            (str(org), str(user)),
        )
        cursor.execute(
            "INSERT INTO user_invitation(organization_id,email,role,invited_by,token_hash,expires_at) "
            "VALUES (%s,'import-warte@example.test','CLERK',%s,sha256('import'::bytea),now()+interval '72 hours')",
            (str(org), str(user)),
        )
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(APP_RW_URL)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_org_id', :org, false)"),
                {"org": str(org)},
            )
            await session.execute(
                text("SELECT set_config('app.current_user_id', :actor, false)"),
                {"actor": str(user)},
            )
            result = await session.scalar(
                text(
                    "SELECT public.organization_import_admission(:org, '{}'::jsonb)::text"
                ),
                {"org": str(org)},
            )
        assert json.loads(result)["empty"] is True
    finally:
        await engine.dispose()
        with (
            psycopg2.connect(MIGRATOR_SYNC_URL) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM user_invitation WHERE organization_id=%s", (str(org),)
            )
            cursor.execute(
                "DELETE FROM membership WHERE organization_id=%s", (str(org),)
            )
            cursor.execute('DELETE FROM "user" WHERE organization_id=%s', (str(org),))
            cursor.execute("DELETE FROM organization WHERE id=%s", (str(org),))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["deactivated", "demoted"])
async def test_acceptance_refuses_when_the_inviter_lost_their_rights(
    client_a_admin, client_unauth, signup_mail, mode
):
    b_email = unique("zweitadmin@example.test")
    created = await client_a_admin.post(
        "/api/users",
        json={
            "email": b_email,
            "password": "StartPasswort123!",
            "role": "OWNER_ADMIN",
        },
    )
    assert created.status_code == 201, created.text
    b_id = created.json()["id"]
    login = await client_unauth.post(
        "/api/auth/login", json={"email": b_email, "password": "StartPasswort123!"}
    )
    assert login.status_code == 200, login.text
    invited = unique("aussen@example.test")
    invitation = await invite(client_unauth, invited, "OWNER_ADMIN")
    token = invitation_token(signup_mail)
    patch = {"is_active": False} if mode == "deactivated" else {"role": "CLERK"}
    try:
        changed = await client_a_admin.patch(f"/api/users/{b_id}", json=patch)
        assert changed.status_code == 200, changed.text
        answer = await client_unauth.post(
            "/api/auth/invitation/accept",
            json={"token": token, "password": INVITED_PASSWORD},
        )
        assert (answer.status_code, answer.json()) == (403, REFUSED)
        from tests.password_reset_support import reset_store

        async with reset_store() as session:
            leftovers = await session.scalar(
                text(
                    'SELECT count(*) FROM "user" WHERE lower(btrim(email))=lower(:email)'
                ),
                {"email": invited},
            )
        assert leftovers == 0
    finally:
        # Alpha keeps exactly one active OWNER_ADMIN for later scenarios.
        restored = await client_a_admin.patch(
            f"/api/users/{b_id}", json={"role": "CLERK", "is_active": True}
        )
        assert restored.status_code == 200, restored.text


@pytest.mark.asyncio
async def test_concurrent_accepts_consume_once(
    client_a_admin, client_unauth, signup_mail
):
    import asyncio

    await invite(client_a_admin, unique("gleichzeitig@example.test"))
    token = invitation_token(signup_mail)
    replies = await asyncio.gather(
        *(
            client_unauth.post(
                "/api/auth/invitation/accept",
                json={"token": token, "password": INVITED_PASSWORD},
            )
            for _ in range(2)
        )
    )
    assert sorted(reply.status_code for reply in replies) == [204, 403]


@pytest.mark.asyncio
async def test_accept_rate_limit_throttles_without_consuming_the_token(
    client_a_admin, client_unauth, signup_mail
):
    from app.core.db import get_session_factory
    from app.core.deployment_policy import get_deployment_policy

    await invite(client_a_admin, unique("drossel@example.test"))
    token = invitation_token(signup_mail)
    rules = get_deployment_policy().rate_limit_rules
    statuses = [
        (
            await client_unauth.post(
                "/api/auth/invitation/accept",
                json={"token": "wrong-token", "password": INVITED_PASSWORD},
            )
        ).status_code
        for _ in range(rules.ip_failures + 2)
    ]
    assert 429 in statuses
    async with get_session_factory()() as session, session.begin():
        await session.execute(text("DELETE FROM auth_throttle"))
    accepted = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert accepted.status_code == 204, accepted.text


@pytest.mark.asyncio
async def test_expires_at_is_seventy_two_hours_after_creation(
    client_a_admin, signup_mail
):
    from datetime import timedelta

    from tests.password_reset_support import reset_store

    invitation = await invite(client_a_admin, unique("laufzeit@example.test"))
    async with reset_store() as session:
        delta = await session.scalar(
            text("SELECT expires_at-created_at FROM user_invitation WHERE id=:id"),
            {"id": invitation["id"]},
        )
    assert delta <= timedelta(hours=72)
    assert delta > timedelta(hours=71, minutes=59)


@pytest.mark.asyncio
async def test_acceptance_during_erasure_refuses_in_german(
    client_a_admin, client_unauth, signup_mail, seed_data
):
    from tests.password_reset_support import reset_store

    invited = unique("erasure@example.test")
    await invite(client_a_admin, invited)
    token = invitation_token(signup_mail)
    org = str(seed_data["org_a_id"])
    async with reset_store() as session:
        await session.execute(
            text(
                "INSERT INTO organization_deletion(organization_id,status,execute_after) "
                "VALUES (:org,'cooling_off',now()+interval '7 days')"
            ),
            {"org": org},
        )
    try:
        answer = await client_unauth.post(
            "/api/auth/invitation/accept",
            json={"token": token, "password": INVITED_PASSWORD},
        )
        assert (answer.status_code, answer.json()) == (403, REFUSED)
        async with reset_store() as session:
            leftovers = await session.scalar(
                text(
                    'SELECT count(*) FROM "user" WHERE lower(btrim(email))=lower(:email)'
                ),
                {"email": invited},
            )
        assert leftovers == 0
    finally:
        async with reset_store() as session:
            await session.execute(
                text("DELETE FROM organization_deletion WHERE organization_id=:org"),
                {"org": org},
            )


@pytest.mark.asyncio
async def test_malformed_invitation_id_is_a_validation_error(client_a_admin):
    answer = await client_a_admin.delete("/api/users/invitations/not-a-uuid")
    assert answer.status_code == 422
    answer = await client_a_admin.post("/api/users/invitations/not-a-uuid/resend")
    assert answer.status_code == 422


@pytest.mark.asyncio
async def test_expired_invitation_leaves_the_open_list_and_can_be_reinvited(
    client_a_admin, client_unauth, signup_mail, seed_data
):
    from tests.password_reset_support import reset_store

    email = unique("erneut@example.test")
    invitation = await invite(client_a_admin, email)
    first_token = invitation_token(signup_mail)
    async with reset_store() as session:
        await session.execute(
            text(
                "UPDATE user_invitation SET expires_at=now()-interval '1 minute' WHERE id=:id"
            ),
            {"id": invitation["id"]},
        )
    listing = await client_a_admin.get("/api/users/invitations")
    assert listing.status_code == 200
    assert email not in [item["email"] for item in listing.json()]
    replacement = await invite(client_a_admin, email)
    assert replacement["email"] == email
    dead = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": first_token, "password": INVITED_PASSWORD},
    )
    assert (dead.status_code, dead.json()) == (403, REFUSED)
    async with reset_store() as session:
        open_rows = await session.scalar(
            text(
                "SELECT count(*) FROM user_invitation WHERE organization_id=:org "
                "AND lower(btrim(email))=lower(btrim(:email)) "
                "AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"org": str(seed_data["org_a_id"]), "email": email},
        )
    assert open_rows == 1


@pytest.mark.asyncio
async def test_accept_refuses_when_the_address_was_registered_elsewhere(
    client_a_admin, client_b_admin, client_unauth, signup_mail
):
    from tests.password_reset_support import reset_store

    invited = unique("anderswo@example.test")
    await invite(client_a_admin, invited)
    token = invitation_token(signup_mail)
    elsewhere = await client_b_admin.post(
        "/api/users",
        json={
            "email": invited,
            "password": "StartPasswort123!",
            "role": "CLERK",
        },
    )
    assert elsewhere.status_code == 201, elsewhere.text
    answer = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": token, "password": INVITED_PASSWORD},
    )
    assert (answer.status_code, answer.json()) == (403, REFUSED)
    async with reset_store() as session:
        users = await session.scalar(
            text('SELECT count(*) FROM "user" WHERE lower(btrim(email))=lower(:email)'),
            {"email": invited},
        )
    assert users == 1  # only the account of the other organization


@pytest.mark.asyncio
async def test_cleanup_removes_finished_invitations_after_seven_days(
    client_a_admin, client_unauth, signup_mail
):
    from tests.password_reset_support import reset_store

    accepted_old = await invite(client_a_admin, unique("aufgeraeumt@example.test"))
    accepted = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={
            "token": invitation_token(signup_mail),
            "password": INVITED_PASSWORD,
        },
    )
    assert accepted.status_code == 204, accepted.text
    revoked_old = await invite(client_a_admin, unique("widerrufen-alt@example.test"))
    invitation_token(signup_mail)
    assert (
        await client_a_admin.delete(f"/api/users/invitations/{revoked_old['id']}")
    ).status_code == 204
    expired_old = await invite(client_a_admin, unique("abgelaufen-alt@example.test"))
    invitation_token(signup_mail)
    young = await invite(client_a_admin, unique("jung@example.test"))
    young_accept = await client_unauth.post(
        "/api/auth/invitation/accept",
        json={"token": invitation_token(signup_mail), "password": INVITED_PASSWORD},
    )
    assert young_accept.status_code == 204, young_accept.text
    open_invitation = await invite(client_a_admin, unique("offen@example.test"))
    invitation_token(signup_mail)
    async with reset_store() as session:
        await session.execute(
            text(
                "UPDATE user_invitation SET accepted_at=now()-interval '8 days' "
                "WHERE id=:id"
            ),
            {"id": accepted_old["id"]},
        )
        await session.execute(
            text(
                "UPDATE user_invitation SET revoked_at=now()-interval '8 days' "
                "WHERE id=:id"
            ),
            {"id": revoked_old["id"]},
        )
        await session.execute(
            text(
                "UPDATE user_invitation SET expires_at=now()-interval '8 days' "
                "WHERE id=:id"
            ),
            {"id": expired_old["id"]},
        )
        await session.scalar(text("SELECT public.cleanup_password_reset_requests()"))
        finished = set(
            (
                await session.scalars(
                    text(
                        "SELECT id FROM user_invitation WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [accepted_old["id"], revoked_old["id"], expired_old["id"]]},
                )
            ).all()
        )
        survivors = set(
            (
                await session.scalars(
                    text(
                        "SELECT id FROM user_invitation WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {"ids": [young["id"], open_invitation["id"]]},
                )
            ).all()
        )
        audits = await session.scalar(
            text(
                "SELECT count(*) FROM audit_event WHERE entity_type='user_invitation' "
                "AND entity_id = ANY(CAST(:ids AS uuid[]))"
            ),
            {
                "ids": [
                    accepted_old["id"],
                    revoked_old["id"],
                    expired_old["id"],
                    young["id"],
                    open_invitation["id"],
                ]
            },
        )
    assert finished == set()
    assert {str(row) for row in survivors} == {young["id"], open_invitation["id"]}
    assert audits >= 5


@pytest.mark.asyncio
async def test_runtime_cleanup_deletes_finished_rows_beside_an_organization_in_erasure(
    client_b_admin, signup_mail, seed_data
):
    from tests.password_reset_support import reset_store, runtime_store

    erasing = (str(seed_data["org_b_id"]), str(seed_data["user_b_admin_id"]))
    live = (str(seed_data["org_a_id"]), str(seed_data["user_a_admin_id"]))
    ids = {}

    async def add_invitation(session, name, scope, **times):
        columns = "".join(f",{column}" for column in times)
        values = "".join(f",now()-interval '{days} days'" for days in times.values())
        ids[name] = str(
            await session.scalar(
                text(
                    f"INSERT INTO user_invitation(organization_id,email,role,invited_by,"
                    f"token_hash,expires_at{columns}) VALUES (:org,:email,'CLERK',:user,"
                    f"sha256(convert_to(:email,'UTF8')),now()+interval '72 hours'{values}) "
                    f"RETURNING id"
                ),
                {"org": scope[0], "user": scope[1], "email": unique(f"{name}@example.test")},
            )
        )

    async def add_reset(session, name, scope, days):
        ids[name] = str(
            await session.scalar(
                text(
                    "INSERT INTO password_reset_request(user_id,organization_id,token_hash,"
                    f"expires_at) VALUES (:user,:org,sha256(convert_to(:name,'UTF8')),"
                    f"now()-interval '{days} days') RETURNING id"
                ),
                {"org": scope[0], "user": scope[1], "name": unique(f"{name}@x.test")},
            )
        )

    async with reset_store() as session:
        # The erasing organization holds the oldest rows, so the batch reaches them first.
        await add_invitation(session, "erasing_old_invitation", erasing, revoked_at=30)
        await add_invitation(session, "erasing_young_invitation", erasing, revoked_at=1)
        await add_reset(session, "erasing_old_reset", erasing, 30)
        await add_invitation(session, "live_old_invitation", live, accepted_at=8)
        await add_reset(session, "live_old_reset", live, 8)
        await add_invitation(session, "live_open_invitation", live)
        await session.execute(
            text(
                "INSERT INTO organization_deletion(organization_id,status,execute_after) "
                "VALUES (:org,'cooling_off',now()+interval '7 days')"
            ),
            {"org": erasing[0]},
        )
    try:
        # The guard still refuses new invitations of an organization in erasure.
        refused = await client_b_admin.post(
            "/api/users/invitations",
            json={"email": unique("gesperrt@example.test"), "role": "CLERK"},
        )
        assert refused.status_code == 409, refused.text
        removed = 0
        for _ in range(20):  # bounded batches; earlier tests may leave old rows
            async with runtime_store() as session:
                batch = await session.scalar(
                    text("SELECT public.cleanup_password_reset_requests()")
                )
            removed += batch
            if batch == 0:
                break
        async with reset_store() as session:
            remaining = {
                str(row)
                for row in (
                    await session.scalars(
                        text(
                            "SELECT id FROM user_invitation WHERE id = ANY(CAST(:ids AS uuid[])) "
                            "UNION ALL SELECT id FROM password_reset_request "
                            "WHERE id = ANY(CAST(:ids AS uuid[]))"
                        ),
                        {"ids": list(ids.values())},
                    )
                ).all()
            }
    finally:
        async with reset_store() as session:
            await session.execute(
                text("DELETE FROM organization_deletion WHERE organization_id=:org"),
                {"org": erasing[0]},
            )
            await session.execute(
                text("DELETE FROM user_invitation WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": list(ids.values())},
            )
            await session.execute(
                text(
                    "DELETE FROM password_reset_request WHERE id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": list(ids.values())},
            )
    assert removed >= 4
    # Rows of the organization in erasure are deleted like its reset rows; young
    # finished rows and pending rows stay.
    assert remaining == {ids["erasing_young_invitation"], ids["live_open_invitation"]}


@pytest.mark.asyncio
async def test_creating_an_invitation_deletes_finished_invitations_older_than_seven_days(
    client_a_admin, signup_mail
):
    from tests.password_reset_support import reset_store

    revoked_old = await invite(client_a_admin, unique("alt-widerrufen@example.test"))
    assert (
        await client_a_admin.delete(f"/api/users/invitations/{revoked_old['id']}")
    ).status_code == 204
    expired_old = await invite(client_a_admin, unique("alt-abgelaufen@example.test"))
    revoked_young = await invite(client_a_admin, unique("jung-widerrufen@example.test"))
    assert (
        await client_a_admin.delete(f"/api/users/invitations/{revoked_young['id']}")
    ).status_code == 204
    async with reset_store() as session:
        for column, invitation, days in (
            ("revoked_at", revoked_old, 8),
            ("expires_at", expired_old, 8),
            ("revoked_at", revoked_young, 6),
        ):
            await session.execute(
                text(
                    f"UPDATE user_invitation SET {column}=now()-interval '{days} days' "
                    f"WHERE id=:id"
                ),
                {"id": invitation["id"]},
            )
    # Nothing deletes them before the next invitation is created.
    fresh = await invite(client_a_admin, unique("neu@example.test"))
    async with reset_store() as session:
        remaining = {
            str(row)
            for row in (
                await session.scalars(
                    text(
                        "SELECT id FROM user_invitation WHERE id = ANY(CAST(:ids AS uuid[]))"
                    ),
                    {
                        "ids": [
                            revoked_old["id"],
                            expired_old["id"],
                            revoked_young["id"],
                            fresh["id"],
                        ]
                    },
                )
            ).all()
        }
    assert remaining == {revoked_young["id"], fresh["id"]}


@pytest.mark.asyncio
async def test_resend_makes_the_acting_administrator_the_inviter(
    client_unauth, signup_mail
):
    import psycopg2

    from app.core.config import settings
    from tests.conftest import (
        MIGRATOR_SYNC_URL,
        _api_test_client,
        _make_session_cookie,
    )

    org, first, second = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with (
        psycopg2.connect(MIGRATOR_SYNC_URL) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "INSERT INTO organization(id,name) VALUES (%s,'Resende Einladung')",
            (str(org),),
        )
        for user, email in (
            (first, "erster-admin@example.test"),
            (second, "zweiter-admin@example.test"),
        ):
            cursor.execute(
                'INSERT INTO "user"(id,organization_id,email,password_hash) '
                "VALUES (%s,%s,%s,'synthetic-not-a-login')",
                (str(user), str(org), email),
            )
            cursor.execute(
                "INSERT INTO membership(organization_id,user_id,role) "
                "VALUES (%s,%s,'OWNER_ADMIN')",
                (str(org), str(user)),
            )
    try:
        x_cookie = _make_session_cookie(first, org)
        y_cookie = _make_session_cookie(second, org)
        async with (
            _api_test_client(
                cookies={settings.session_cookie_name: x_cookie}
            ) as x_client,
            _api_test_client(
                cookies={settings.session_cookie_name: y_cookie}
            ) as y_client,
        ):
            invitation = await invite(x_client, unique("nachfolge@example.test"))
            invitation_token(signup_mail, organization="Resende Einladung")
            # X (the original inviter) is demoted; Y resends and becomes the
            # inviter, so acceptance works again.
            demoted = await y_client.patch(
                f"/api/users/{first}", json={"role": "CLERK"}
            )
            assert demoted.status_code == 200, demoted.text
            resent = await y_client.post(
                f"/api/users/invitations/{invitation['id']}/resend"
            )
            assert resent.status_code == 200, resent.text
            from tests.password_reset_support import reset_store

            async with reset_store() as session:
                inviter = await session.scalar(
                    text("SELECT invited_by FROM user_invitation WHERE id=:id"),
                    {"id": invitation["id"]},
                )
            assert inviter == second
            accepted = await client_unauth.post(
                "/api/auth/invitation/accept",
                json={
                    "token": invitation_token(
                        signup_mail, organization="Resende Einladung"
                    ),
                    "password": INVITED_PASSWORD,
                },
            )
            assert accepted.status_code == 204, accepted.text
    finally:
        with (
            psycopg2.connect(MIGRATOR_SYNC_URL) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                "DELETE FROM audit_event WHERE organization_id=%s", (str(org),)
            )
            cursor.execute(
                "DELETE FROM user_invitation WHERE organization_id=%s", (str(org),)
            )
            cursor.execute(
                "DELETE FROM membership WHERE organization_id=%s", (str(org),)
            )
            cursor.execute('DELETE FROM "user" WHERE organization_id=%s', (str(org),))
            cursor.execute("DELETE FROM organization WHERE id=%s", (str(org),))


def test_invitation_migration_roundtrip_is_exact_and_refuses_retained_state(
    run_migrations,
):
    from alembic.operations import Operations
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine
    from tests.conftest import MIGRATOR_SYNC_URL
    from tests.test_session_review_access import _chain_snapshot

    scripts = ScriptDirectory("migrations")
    migration = scripts.get_revision("0184").module
    engine = create_engine(MIGRATOR_SYNC_URL)
    try:
        with engine.connect() as connection, connection.begin() as transaction:
            # Earlier tests in this session commit accepted and revoked
            # invitations; the refusal rehearsal needs an empty table.
            connection.execute(text("DELETE FROM public.user_invitation"))
            before = _chain_snapshot(connection)
            guard = "SELECT prosrc FROM pg_proc WHERE oid='public.organization_lifecycle_guard()'::regprocedure"
            assert "TG_TABLE_NAME='user_invitation' AND TG_OP='DELETE'" in (
                connection.scalar(text(guard))
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('public.user_invitation')")
                    )
                    is None
                )
                # The guard returns to the 0147 text, which never names the table.
                restored = connection.scalar(text(guard))
                assert "user_invitation" not in restored
                assert (
                    "('organization_deletion','audit_event','password_reset_request') THEN"
                    in restored
                )
                migration.upgrade()
                assert _chain_snapshot(connection) == before
                connection.execute(
                    text(
                        "INSERT INTO organization(name) VALUES ('Invitation roundtrip') RETURNING id"
                    )
                )
                actor = connection.execute(
                    text("""INSERT INTO "user"(organization_id,email,display_name,password_hash)
                    SELECT id,'invitation-roundtrip@example.test','Invitation','hash' FROM organization WHERE name='Invitation roundtrip'
                    RETURNING id,organization_id""")
                ).one()
                connection.execute(
                    text("""INSERT INTO user_invitation(organization_id,email,role,invited_by,token_hash,expires_at)
                    VALUES (:org,'invitation-roundtrip@example.test','CLERK',:user,sha256('roundtrip'::bytea),now() + interval '72 hours')"""),
                    {"user": actor.id, "org": actor.organization_id},
                )
                with pytest.raises(
                    DBAPIError, match="Cannot discard retained invitation state"
                ):
                    with connection.begin_nested():
                        migration.downgrade()
            transaction.rollback()
    finally:
        engine.dispose()


from tests.password_reset_support import signup_mail as signup_mail  # noqa: E402,F401
