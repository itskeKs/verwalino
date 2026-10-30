"""Inbox commands use HTTP; only worker ticks run in-process, without a daemon."""
import hashlib

from pytest_bdd import given, parsers, then, when

from tests.bdd.steps.browser_setup import create, create_scope
from tests.test_allocation_adoptions import command as adoption_command
from tests.test_allocation_proposals import command as allocation_command
from tests.test_allocation_treatments import command as treatment_command
from tests.test_assignment_proposals import command as assignment_command
from tests.test_context_decisions import command as context_command
from tests.test_invoice_extraction_worker import pdf_bytes, worker_tenant  # noqa: F401
from tests.test_invoice_reviews import command as review_command
from tests.test_invoice_source_custody import store  # noqa: F401
from tests.test_source_partitions import command as partition_command


def prepare_reviewer(world, user_id):
    world.data["property"] = create(world, "/api/properties", {
        "name": "Sonnenhof inbox review", "management_type": "MIET",
        "data_audience": {"label": "Sonnenhof review team", "reason": "Synthetic inbox review",
                          "grants": [{"user_id": str(user_id), "capabilities": ["read", "prepare", "approve", "export"],
                                      "reason": "Synthetic inbox review"}]},
    }, keyed=True)
    world.data["scope"] = create_scope(world, str(user_id), "Mietkreis Sonnenhof inbox")


@given("an inbox reviewer with a property and accounting scope")
def inbox_reviewer(world, worker_tenant, store):
    world.actor, ids = worker_tenant
    world.data["organization_id"] = ids["org_a_id"]
    prepare_reviewer(world, ids["user_a_admin_id"])


@when("the reviewer uploads a text invoice to the inbox")
def upload_invoice(world):
    data = pdf_bytes()
    batch = create(world, "/api/intake-batches", {"items": [{
        "client_item_key": "invoice", "filename": "synthetic-invoice.pdf", "mime_type": "application/pdf",
        "content_sha256": hashlib.sha256(data).hexdigest(), "content_byte_size": len(data),
    }]}, keyed=True)
    response = world.act("POST", f"/api/intake-batches/{batch['id']}/items",
                         data={"client_item_key": "invoice"}, files={"file": ("synthetic-invoice.pdf", data, "application/pdf")})
    assert response.status_code == 202, world.describe_response()
    assert response.json()["status"] == "received"
    world.data.update(batch=batch, original=response.json())


@when("the inbox processes the document")
def process_invoice(world, monkeypatch):
    from app.core.intake_extraction import extract_logical
    from app.core.intake_inspection import result
    from app.workers import intake_extraction, workflow
    from app.workers.workflow_runner import WorkflowRunner, build_adapters

    # Existing inspector double; extraction uses the real text-layer fallback.
    inspect_once, extract_once = workflow.process_once, intake_extraction.process_once

    async def inspect(factory, org):
        return await inspect_once(factory, org, inspector=lambda data, mime: result(data, "clean", mime=mime, pages=1))

    async def extract(factory, org):
        return await extract_once(factory, org, processor=extract_logical)

    def forbidden(*args):
        raise AssertionError("A text invoice must not invoke OCR")

    monkeypatch.setattr(workflow, "process_once", inspect)
    monkeypatch.setattr(intake_extraction, "process_once", extract)
    monkeypatch.setattr("app.core.intake_extraction.ocr_page", forbidden)

    async def organizations():
        return (world.data["organization_id"],)

    runner = WorkflowRunner(organizations, build_adapters())
    stages = []
    for _ in range(10):
        tick = world.run(runner.run_once())
        if tick.code == "IDLE":
            break
        assert tick.code == "PROCESSED", tick
        stages.append(tick.stage)
    assert stages == ["intake_inspection", "outbox", "intake_extraction"]
    assert world.run(runner.run_once()).code == "IDLE"


def get(world, path, **params):
    response = world.act("GET", path, params=params)
    assert response.status_code == 200, world.describe_response()
    return response.json()


@then("the invoice proposal is ready for review without creating a cost")
def proposal_ready(world):
    state = get(world, f"/api/intake-batches/{world.data['batch']['id']}")
    assert state["counts"]["review"] == 1, state
    ref, = get(world, "/api/document-refs/page", source_id=world.data["original"]["original_id"])["items"]
    logical, = get(world, "/api/logical-documents", document_ref_id=ref["id"])["items"]
    assert get(world, f"/api/logical-documents/{logical['id']}/processing")["status"] == "succeeded"
    extraction, = get(world, f"/api/document-refs/{ref['id']}/extractions", logical_document_id=logical["id"])["items"]
    assert extraction["status"] == "succeeded"
    assert get(world, "/api/cost-entries") == []
    world.data.update(ref=ref, logical=logical, extraction=extraction)


def post(world, path, command):
    response = world.act("POST", path, **command)
    assert response.status_code == 201, world.describe_response()
    return response.json()


@when(parsers.parse("the reviewer checks the original and confirms a corrected amount of {amount} EUR"))
def confirm_invoice(world, amount):
    ref, logical = world.data["ref"], world.data["logical"]
    base = f"/api/logical-documents/{logical['id']}"
    original = world.act("GET", f"/api/intake-batches/originals/{world.data['original']['original_id']}/content")
    assert original.status_code == 200, world.describe_response()
    assert hashlib.sha256(original.content).hexdigest() == ref["content_sha256"]
    partition = post(world, f"/api/document-refs/{ref['id']}/partition", partition_command([logical]))
    assignment = post(world, base + "/assignment-proposals", assignment_command(partition, world.data["extraction"], world.data["scope"]["id"]))
    review = post(world, base + "/invoice-reviews", review_command(assignment, gross_amount=amount,
        seller_name="Stadtwerke Test GmbH"))
    preview = get(world, f"/api/assignment-proposals/{assignment['id']}/context-preview")
    decision = post(world, base + "/context-decisions", context_command(assignment, review, preview, quotation="Stadtwerke Test GmbH"))
    category = next(c for c in get(world, "/api/cost-categories") if c["plan_version"] == 1 and c["is_active"]
                    and not c["reserve_relevant"] and c["owner_billing_relevant_default"] and c["cost_group"] == "UMLAGEFAEHIG")
    allocation = post(world, base + "/allocation-proposals", allocation_command(decision, [{
        "cost_category_id": category["id"], "cost_category_plan_version": category["plan_version"],
        "description": "Reviewed Sonnenhof utility invoice", "gross_amount": amount, "vat_amount": None,
    }]))
    preview = get(world, f"/api/allocation-proposals/{allocation['id']}/treatment-preview")
    treatment = post(world, base + "/allocation-treatments", treatment_command(allocation, preview))
    preview = get(world, f"/api/allocation-treatments/{treatment['id']}/adoption-preview")
    assert preview["can_adopt"], preview
    assert get(world, "/api/cost-entries") == []
    command = adoption_command(treatment, preview)
    adopted = post(world, base + "/allocation-adoptions", command)
    assert post(world, base + "/allocation-adoptions", command) == adopted
    world.data.update(review=review, adoption=adopted, amount=amount)


@then("a confirmed cost contains the reviewed invoice values")
def confirmed_cost(world):
    adopted = world.data["adoption"]
    assert adopted["financial_state"] == "confirmed"
    cost, = adopted["costs"]
    saved = get(world, f"/api/cost-entries/{cost['cost_entry_id']}")
    review = world.data["review"]
    assert saved["status"] == "confirmed" and saved["source"] == "PDF_IMPORT"
    for field in ("gross_amount", "invoice_date", "service_period_from", "service_period_to", "document_number"):
        assert saved[field] == review[field], (field, saved, review)
    assert saved["property_id"] == world.data["property"]["id"]
    assert adopted["scope_id"] == world.data["scope"]["id"]
    assert len(get(world, "/api/cost-entries")) == 1
    state = get(world, f"/api/intake-batches/{world.data['batch']['id']}")
    assert state["counts"]["confirmed"] == 1, state
