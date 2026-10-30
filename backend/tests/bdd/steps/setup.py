"""First-run setup steps; privileged fixture provisioning is kept outside the steps."""
from pytest_bdd import given, then, when

from tests.setup_support import BODY


@given("a fresh installation with an unused setup token")
def fresh_installation(world, fresh_setup):
    world.anonymous = fresh_setup[0]
    assert world.look(world.anonymous, "/api/setup").json() == {"required": True}


@when("the operator sets up the installation with that token")
def submit_setup(world):
    world.send(world.anonymous, "POST", "/api/setup", json=BODY)


@when("the operator sets up the installation with a wrong token")
def submit_wrong_setup(world):
    world.send(world.anonymous, "POST", "/api/setup", json=BODY | {"token": "00" * 32})


@then("the administrator is signed in")
def owner_is_signed_in(world):
    assert world.response.status_code == 201, world.describe_response()
    answer = world.look(world.anonymous, "/api/auth/session")
    assert answer.status_code == 200 and answer.json()["role"] == "OWNER_ADMIN"


@then("the administrator can sign in again")
def owner_can_sign_in(world):
    world.send(world.anonymous, "POST", "/api/auth/logout")
    answer = world.send(world.anonymous, "POST", "/api/auth/login", json={"email": BODY["admin_email"], "password": BODY["password"]})
    assert answer.status_code == 200, world.describe_response()


@then("setup is refused")
def setup_is_refused(world):
    assert world.response.status_code == 403, world.describe_response()


@then("the installation still requires setup")
def setup_is_required(world):
    assert world.look(world.anonymous, "/api/setup").json() == {"required": True}


@then("a second setup is not found")
@then("the same token cannot set up the installation again")
def second_setup_is_absent(world):
    assert world.response.status_code == 201, world.describe_response()
    answer = world.send(world.anonymous, "POST", "/api/setup", json=BODY)
    assert answer.status_code == 404, world.describe_response()
