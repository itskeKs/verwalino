"""What an invoice has cost so far, summarized from its model calls (issue #500).

The summary is the one reading of ``invoice_model_call`` for the user: a stage that only reserved reports its
reservation as such, a model without a price reports no cost at all (never 0 USD), and a failed request keeps what
it consumed. These tests need no database: the rows are built in memory.
"""
from decimal import Decimal

import pytest

from app.models.invoice_model_call import InvoiceModelCall
from app.services.invoice_model_usage import summarize

pytestmark = pytest.mark.no_db


def call(stage="extract_invoice", model="gpt-5.6-luna", *, page=None, reserved=(5000, 3000, "0.04"), consumed=None,
         complete=None):
    """One ledger row: RESERVED when ``consumed`` is None, else SETTLED with those tokens and cost."""
    row = InvoiceModelCall(stage=stage, pdf_page=page if stage == "read_page" else None, model_name=model,
                           reserved_input_tokens=reserved[0], reserved_output_tokens=reserved[1], reserved_cost_usd=Decimal(reserved[2]))
    if consumed is None:
        row.status = "RESERVED"
    else:
        row.status = "SETTLED"
        row.input_tokens, row.output_tokens, row.cost_usd = consumed[0], consumed[1], Decimal(consumed[2])
        row.usage_complete = complete
    return row


def test_an_invoice_without_a_call_has_no_usage():
    assert summarize([]).model_dump() == {"stages": [], "total": None}


def test_reported_usage_of_both_stages_adds_up_in_the_total():
    usage = summarize([
        call("read_page", "gpt-5.4-mini", page=1, consumed=(1000, 200, "0.00165"), complete=True),
        call("read_page", "gpt-5.4-mini", page=2, consumed=(1200, 300, "0.00200"), complete=True),
        call(consumed=(2000, 100, "0.00085"), complete=True),
    ])
    reading, extraction = usage.stages
    assert (reading.stage, reading.model, reading.calls, reading.usage_status) == ("READING", "gpt-5.4-mini", 2, "REPORTED")
    assert (reading.reported_input_tokens, reading.reported_output_tokens, reading.estimated_cost_usd) == (2200, 500, Decimal("0.00365"))
    assert (extraction.stage, extraction.model, extraction.calls, extraction.usage_status) == ("EXTRACTION", "gpt-5.6-luna", 1, "REPORTED")
    total = usage.total
    assert (total.usage_status, total.input_tokens, total.output_tokens, total.estimated_cost_usd) == ("REPORTED", 4200, 600, Decimal("0.0045"))


def test_a_request_without_reported_usage_is_its_reservation_and_never_a_known_zero():
    usage = summarize([call(reserved=(5000, 3000, "0.04"), consumed=(5000, 3000, "0.04"), complete=False)])
    (stage,) = usage.stages
    assert stage.usage_status == "RESERVED"
    assert (stage.reported_input_tokens, stage.reported_output_tokens) == (None, None)
    assert (stage.input_tokens, stage.output_tokens, stage.estimated_cost_usd) == (5000, 3000, Decimal("0.04"))


def test_a_request_that_is_still_reserved_counts_at_its_reservation():
    usage = summarize([call(reserved=(5000, 3000, "0.04"))])
    (stage,) = usage.stages
    assert (stage.usage_status, stage.input_tokens, stage.output_tokens, stage.estimated_cost_usd) == ("RESERVED", 5000, 3000, Decimal("0.04"))
    assert usage.total.usage_status == "RESERVED" and usage.total.estimated_cost_usd == Decimal("0.04")


def test_reported_and_unreported_requests_of_one_stage_are_partial_and_conservative():
    usage = summarize([
        call("read_page", "gpt-5.4-mini", page=1, reserved=(4000, 6000, "0.03"), consumed=(1000, 200, "0.00165"), complete=True),
        call("read_page", "gpt-5.4-mini", page=2, reserved=(4000, 6000, "0.03"), consumed=(4000, 6000, "0.03"), complete=False),
    ])
    (stage,) = usage.stages
    assert stage.usage_status == "PARTIAL"
    assert (stage.reported_input_tokens, stage.reported_output_tokens) == (1000, 200)
    assert (stage.input_tokens, stage.output_tokens, stage.estimated_cost_usd) == (5000, 6200, Decimal("0.03165"))


def test_a_model_without_a_price_has_no_cost_and_hides_none_of_the_known_one():
    # A request at an unknown price reserves zero dollars: that is not a price.
    usage = summarize([
        call("read_page", "local-vision", page=1, reserved=(4000, 6000, "0"), consumed=(900, 100, "0"), complete=True),
        call(consumed=(2000, 100, "0.00085"), complete=True),
    ])
    reading, extraction = usage.stages
    assert reading.estimated_cost_usd is None and (reading.reported_input_tokens, reading.reported_output_tokens) == (900, 100)
    assert extraction.estimated_cost_usd == Decimal("0.00085")
    assert usage.total.estimated_cost_usd is None and usage.total.input_tokens == 2900


def test_two_models_of_one_stage_are_two_blocks_after_the_stage_before_them_in_the_order_of_their_first_call():
    usage = summarize([
        call(model="gpt-5.6-luna", consumed=(1000, 100, "0.001"), complete=True),
        call(model="gpt-5.4-mini", consumed=(2000, 200, "0.002"), complete=True),
        call("read_page", "gpt-5.4-mini", page=1, consumed=(10, 10, "0.0001"), complete=True),
    ])
    assert [(stage.stage, stage.model) for stage in usage.stages] == [
        ("READING", "gpt-5.4-mini"), ("EXTRACTION", "gpt-5.6-luna"), ("EXTRACTION", "gpt-5.4-mini")]
