"""Which process of an installation claims waiting invoices, per stack (issue #814).

The decision is one rule (``core/invoice_claimant.py``) over what each stack file says about each worker service; no
test here starts a container. Expected behaviour comes from the issue: the portable stack, staging and the hosted
server process invoices in the declaration worker, ``docker-compose.prod.yml`` in its dedicated invoice worker and not
in its declaration worker, and with the operator's switch off nobody does.
"""
import re
from pathlib import Path

import pytest
import yaml

from app.core.config import settings
from app.core.invoice_claimant import claims_invoices
from app.services.ai_settings_service import confirmation_required_message, confirmation_text_version

ROOT = Path(__file__).resolve().parents[2]
SWITCH = "HV_INVOICE_EXTRACTION_ENABLED"
ENTRYPOINT = ["/usr/local/bin/worker-entrypoint.sh"]
STACKS = {
    "production": ("docker-compose.prod.yml", "app", ["invoice-worker"]),
    "portable": ("infra/docker-compose.yml", "app", ["declaration-worker"]),
    "staging": ("infra/docker-compose.staging.yml", "staging-app", ["declaration-worker"]),
}


def services(stack):
    return yaml.safe_load((ROOT / STACKS[stack][0]).read_text())["services"]


def value(raw, operator):
    """The value Compose gives a variable: ``${NAME:-default}`` reads the operator's environment, a literal is itself."""
    match = re.fullmatch(r"\$\{(\w+):-([^}]*)\}", str(raw))
    return operator.get(match.group(1), match.group(2)) if match else str(raw)


def switch_on(service, operator):
    """The process's own switch: an unset one is the setting's default, on (``core/config.py``)."""
    return value(service.get("environment", {}).get(SWITCH, "true"), operator).lower() == "true"


def claimants(stack, operator):
    """The worker services of a stack that claim invoices: the entrypoint's kind and the service's own switch."""
    found = []
    for name, service in services(stack).items():
        if service.get("entrypoint") != ENTRYPOINT:
            continue
        kind = service.get("environment", {}).get("HV_WORKER_KIND", "declaration_import")
        if claims_invoices(kind, enabled=switch_on(service, operator)):
            found.append(name)
    return found


@pytest.mark.no_db
@pytest.mark.parametrize("kind, enabled, expected", [
    ("invoice_extraction", True, True),
    ("declaration_import", True, True),
    ("workflow_runner", True, False),
    ("invoice_extraction", False, False),
    ("declaration_import", False, False),
    ("workflow_runner", False, False),
])
def test_only_the_invoice_and_the_declaration_worker_can_claim_invoices_and_only_with_the_switch_on(kind, enabled, expected):
    assert claims_invoices(kind, enabled=enabled) is expected


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_each_stack_has_exactly_one_process_that_claims_invoices(stack):
    assert claimants(stack, {}) == STACKS[stack][2]


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_with_the_operators_switch_off_nobody_claims_and_the_api_says_so(stack):
    off = {SWITCH: "false"}
    assert claimants(stack, off) == []
    assert not switch_on(services(stack)[STACKS[stack][1]], off)


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_the_api_processes_invoices_exactly_where_a_worker_claims_them(stack):
    """The API's switch drives the settings card, the confirmation text and the queue; a worker must back it."""
    api = services(stack)[STACKS[stack][1]]
    for operator in ({}, {SWITCH: "false"}, {SWITCH: "true"}):
        assert switch_on(api, operator) is bool(claimants(stack, operator)), (stack, operator)


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_the_operators_switch_is_one_expression_for_every_service_that_reads_it_and_the_inbox_worker_stays_off(stack):
    expression = "${" + SWITCH + ":-true}"
    _file, api, workers = STACKS[stack]
    for name in [api, *workers]:
        assert services(stack)[name]["environment"][SWITCH] == expression, name
    if stack == "production":
        # Its dedicated invoice worker claims; the declaration worker is left out by a fixed value, not by the operator.
        assert services(stack)["worker"]["environment"][SWITCH] == "false"
    if "inbox-worker" in services(stack):
        inbox = services(stack)["inbox-worker"]
        assert inbox["environment"][SWITCH] == "false"
        assert "--stage" not in inbox["command"], "the inbox worker must not select the invoice stage"


@pytest.mark.no_db
def test_production_keeps_its_dedicated_invoice_worker_and_its_declaration_worker_claims_no_invoice():
    production = services("production")
    assert production["worker"]["environment"][SWITCH] == "false"
    assert claimants("production", {}) == ["invoice-worker"]
    assert production["invoice-worker"]["environment"]["HV_WORKER_KIND"] == "invoice_extraction"


@pytest.mark.no_db
@pytest.mark.parametrize("stack", ["portable", "staging"])
def test_the_settings_card_names_invoices_where_the_stack_processes_them_and_only_there(stack, monkeypatch):
    """What the app shows follows what the stack file gives the app: the confirmation text that names invoices by
    default, the one for declarations alone once the operator switched invoice processing off."""
    api = services(stack)[STACKS[stack][1]]
    for operator, names_invoices in (({}, True), ({SWITCH: "false"}, False)):
        monkeypatch.setattr(settings, "invoice_extraction_enabled", switch_on(api, operator))
        assert (confirmation_text_version() == "2") is names_invoices
        assert ("Rechnungen" in confirmation_required_message()) is names_invoices
