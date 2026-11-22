"""Which process of an installation claims waiting invoices and which runs the inbox's model stage, per stack (issues #814, #501).

The decision is one rule (``core/invoice_claimant.py``) over what each stack file says about each worker service; no
test here starts a container. Expected behaviour comes from the issues: the portable stack, staging and the hosted
server process invoices in the declaration worker, ``docker-compose.prod.yml`` in its dedicated invoice worker and not
in its declaration worker, and with the operator's switch off nobody does. The extraction stage of the inbox, which
reads an unstructured document with the organization's models, runs in the declaration worker of every stack and in no
other process; the inbox worker has no session secret and no route to a provider.
"""
import re
from pathlib import Path

import pytest
import yaml

from app.core.config import settings
from app.core.invoice_claimant import claims_invoices, runs_inbox_extraction
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
    default (text version 3, which names the inbox as well), the one that names declarations and the inbox but no invoice
    once the operator switched invoice processing off (version 4)."""
    api = services(stack)[STACKS[stack][1]]
    for operator, names_invoices in (({}, True), ({SWITCH: "false"}, False)):
        monkeypatch.setattr(settings, "invoice_extraction_enabled", switch_on(api, operator))
        assert confirmation_text_version() == ("3" if names_invoices else "4")
        assert ("Rechnungen" in confirmation_required_message()) is names_invoices


# ---- the inbox's model stage (issue #501, owner decision of 2026-11-22) -------------------------------------------------

# The one process of each stack that runs the extraction stage of the inbox: its declaration worker.
INBOX_STAGE = {"production": ["worker"], "portable": ["declaration-worker"], "staging": ["declaration-worker"]}


def kind_of(service):
    return service.get("environment", {}).get("HV_WORKER_KIND", "declaration_import")


def worker_services(stack):
    return {name: service for name, service in services(stack).items() if service.get("entrypoint") == ENTRYPOINT}


def inbox_stage_runners(stack):
    return [name for name, service in worker_services(stack).items() if runs_inbox_extraction(kind_of(service))]


def networks_of(service):
    networks = service.get("networks", [])
    return set(networks if isinstance(networks, list) else networks)


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_each_stack_runs_the_inbox_model_stage_in_its_declaration_worker_and_nowhere_else(stack):
    assert inbox_stage_runners(stack) == INBOX_STAGE[stack]


@pytest.mark.no_db
def test_the_inbox_stage_does_not_depend_on_the_invoice_switch_production_runs_it_with_the_switch_off():
    production = services("production")
    assert production["worker"]["environment"][SWITCH] == "false" and claimants("production", {}) == ["invoice-worker"]
    assert inbox_stage_runners("production") == ["worker"], "the declaration worker of the stack with a dedicated invoice worker runs it too"
    assert "invoice-worker" not in inbox_stage_runners("production")


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_the_process_that_runs_the_inbox_model_stage_has_what_the_stage_needs(stack):
    """The documents to read, the worker role of the queue, the secret the organization's key is stored under, the route to
    its provider and the PDF renderer (the health check of this worker kind probes it)."""
    for name in INBOX_STAGE[stack]:
        service = services(stack)[name]
        environment = service["environment"]
        assert "HV_SESSION_SECRET" in environment, name
        assert "app_worker" in environment["HV_DATABASE_URL_WORKER"], name
        assert environment["HV_DOCUMENT_STORAGE_ROOT"] == "/data/documents", name
        assert any(str(volume).split(":")[1:2] == ["/data/documents"] for volume in service["volumes"]), name
        assert "egress" in networks_of(service) and "backend" in networks_of(service), name
        assert {"app.workers.declaration_import", "--healthcheck"} <= set(service["healthcheck"]["test"]), "its health check probes the renderer and the storage"


@pytest.mark.no_db
@pytest.mark.parametrize("stack", STACKS)
def test_the_inbox_worker_has_no_session_secret_no_route_to_a_provider_and_no_model_stage(stack):
    """A process without the session secret must never be given the stage: it could only end a document with a key that
    cannot be read, and sending without the secret is impossible by construction."""
    inbox = {name: service for name, service in services(stack).items() if kind_of(service) == "workflow_runner"}
    assert set(inbox) <= {"inbox-worker"}
    for name, service in inbox.items():
        environment = service["environment"]
        assert not runs_inbox_extraction(kind_of(service))
        assert "HV_SESSION_SECRET" not in environment, name
        assert not [key for key in environment if "KEY" in key.upper() or "SECRET" in key.upper() or "TOKEN" in key.upper() or "OPENAI" in key.upper()], name
        assert networks_of(service) == {"backend"}, "no network with a route to the internet"
        assert environment[SWITCH] == "false"
        assert "--stage" not in service["command"], "it runs the stages the runner defaults to, which have no model"
        assert all(volume.get("read_only") if isinstance(volume, dict) else str(volume).endswith(":ro") for volume in service["volumes"]), "the documents and the signatures are read-only"
