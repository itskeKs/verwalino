"""Inviting colleagues by e-mail and accepting through the single-use link."""

import re
import uuid
from urllib.parse import parse_qs, urlsplit

from pytest_bdd import given, parsers, then, when

from app.core.config import settings
from tests.bdd.steps.password_reset import mailbox_message

INVITEE_PASSWORD = "InviteePassword789!"


@given("a test mailbox for invitations")
def invitation_mailbox(mailpit, monkeypatch):
    monkeypatch.setattr(settings, "public_host", "signup.example.test")


@when(parsers.parse('the administrator invites "{email}" as "{role}"'))
def invite_user(world, email, role):
    # Accepted colleagues persist for the whole BDD session: the address must
    # be unique per scenario even though the feature text stays stable.
    local, domain = email.rsplit("@", 1)
    email = f"{local}-{uuid.uuid4().hex[:8]}@{domain}"
    world.data["email"] = email
    world.data["invitee_role"] = role
    answer = world.send(
        world.actor,
        "POST",
        "/api/users/invitations",
        json={"email": email, "role": role},
    )
    assert answer.status_code == 201, world.describe_response()


@then(
    "the test mailbox receives an invitation with a single-use link valid for 72 hours"
)
def invitation_mail_sent(world, mailpit):
    message = mailbox_message(
        world, mailpit, "Verwalino: Einladung von Verwaltung Alpha"
    )
    assert "72 Stunden" in message["Text"]
    match = re.search(r"https://\S+", message["Text"])
    assert match, "The invitation email must contain an HTTPS link"
    link = urlsplit(match[0])
    assert (link.scheme, link.netloc, link.path) == (
        "https",
        "signup.example.test",
        "/einladung",
    )
    query = parse_qs(link.query)
    assert list(query) == ["token"]
    token = query["token"][0]
    assert re.fullmatch(r"[0-9a-f]{64}", token), (
        "The emailed link must contain the invitation token"
    )
    world.data["invitation_token"] = token
    mailpit.clear()


@when(parsers.parse('the administrator revokes the invitation for "{email}"'))
def revoke_invitation(world, email):
    answer = world.send(world.actor, "GET", "/api/users/invitations")
    assert answer.status_code == 200, world.describe_response()
    invitation = next(
        item for item in answer.json() if item["email"] == world.data["email"]
    )
    answer = world.send(
        world.actor, "DELETE", f"/api/users/invitations/{invitation['id']}"
    )
    assert answer.status_code == 204, world.describe_response()


@when("the invitee sets a password through the emailed link")
def accept_invitation(world):
    world.send(
        world.anonymous,
        "POST",
        "/api/auth/invitation/accept",
        json={"token": world.data["invitation_token"], "password": INVITEE_PASSWORD},
    )


@when("the invitee sets a password through the emailed link again")
def accept_invitation_again(world):
    accept_invitation(world)


@then("the invitation is accepted without signing in")
def accepted_invitation(world):
    assert world.response.status_code == 204, world.describe_response()
    assert "set-cookie" not in world.response.headers
    assert world.look(world.anonymous, "/api/auth/session").status_code == 401


@then("the invitation is refused in German")
def refused_invitation(world):
    assert world.response.status_code == 403, world.describe_response()
    assert "Einladung" in world.response.json()["detail"]


@when(
    parsers.parse('the administrator of "{organization}" lists the pending invitations')
)
def list_pending_invitations(world, organization):
    answer = world.send(
        world.administrator_of(organization), "GET", "/api/users/invitations"
    )
    assert answer.status_code == 200, world.describe_response()


@then("they see no invitation for the invited address")
def no_invitation_for_the_address(world):
    assert world.data["email"] not in [item["email"] for item in world.response.json()]


@when("they try to revoke and resend the invitation")
def foreign_revoke_and_resend(world):
    # Alpha's own list names the invitation; Beta must not be able to act on it.
    ours = world.send(world.actor, "GET", "/api/users/invitations")
    invitation = next(
        item for item in ours.json() if item["email"] == world.data["email"]
    )
    beta = world.administrator_of("Verwaltung Beta")
    world.data["foreign_attempts"] = [
        world.send(
            beta, "DELETE", f"/api/users/invitations/{invitation['id']}"
        ).status_code,
        world.send(
            beta, "POST", f"/api/users/invitations/{invitation['id']}/resend"
        ).status_code,
    ]


@then("the invitation is not found either way")
def invitation_not_found_either_way(world):
    assert world.data["foreign_attempts"] == [404, 404]


@then("the invited address is still pending")
def invited_address_still_pending(world):
    assert world.data["email"] in [item["email"] for item in world.response.json()]


@when(
    parsers.parse(
        'the administrator invites the address of "{organization}"\'s administrator'
    )
)
def invite_foreign_admin_address(world, organization):
    foreign = world.administrator_of(organization)
    session = world.send(foreign, "GET", "/api/auth/session")
    assert session.status_code == 200, world.describe_response()
    user_id = session.json()["user_id"]
    users = world.send(foreign, "GET", "/api/users")
    assert users.status_code == 200, world.describe_response()
    email = next(item["email"] for item in users.json() if item["id"] == user_id)
    world.data["email"] = email
    world.send(
        world.actor,
        "POST",
        "/api/users/invitations",
        json={"email": email, "role": "CLERK"},
    )


@then("the invitation is refused like an address of the own organization")
def refused_like_an_own_address(world):
    assert world.response.status_code == 409, world.describe_response()
    assert world.response.json() == {
        "detail": "Diese E-Mail-Adresse wird bereits verwendet."
    }


@then("no invitation is pending and no mail is sent")
def no_pending_and_no_mail(world, mailpit):
    answer = world.send(world.actor, "GET", "/api/users/invitations")
    assert answer.status_code == 200, world.describe_response()
    assert world.data["email"] not in [item["email"] for item in answer.json()]
    inbox = mailpit.request("GET", "/api/v1/messages")
    assert inbox.json()["total"] == 0, "A refused invitation must not send mail"


@then("the invitee can sign in with the invited role")
def invitee_signs_in(world):
    answer = world.send(
        world.anonymous,
        "POST",
        "/api/auth/login",
        json={"email": world.data["email"], "password": INVITEE_PASSWORD},
    )
    assert answer.status_code == 200, world.describe_response()
    assert answer.json()["role"] == world.data["invitee_role"]
