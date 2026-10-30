"""Bounded diagnostic filtering without losing structural error information."""

import copy
from http import HTTPStatus
import json
import logging
import time

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.logging import RequestFormatter
from app.core.scrub import scrub, scrub_text


@pytest.mark.parametrize("text", ["a-" * 32000, "x-" * 32000, "/" + "p" * 20000], ids=["a-hyphens", "x-hyphens", "long-path"])
def test_hostile_text_is_scrubbed_in_under_fifty_milliseconds(text):
    started = time.perf_counter()
    filtered = scrub_text(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.050, f"scrubbing took {elapsed * 1000:.3f} ms"
    assert filtered.endswith("[truncated]")
    assert len(filtered) <= 8192 + len("[truncated]")


def test_long_access_record_is_formatted_in_under_five_milliseconds():
    path = "/" + "a" * 14000
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "GET %s HTTP/1.1" 404', ("127.0.0.1", path), None)
    started = time.perf_counter()
    formatted = RequestFormatter().format(record)
    elapsed = time.perf_counter() - started
    assert elapsed < 0.005, f"access formatting took {elapsed * 1000:.3f} ms"
    assert path in formatted
    assert "request_id=" in formatted


@pytest.mark.parametrize(("logger", "level"), [("app.test", logging.INFO), ("app.test", logging.WARNING), ("uvicorn.access", logging.INFO), ("uvicorn.access", logging.ERROR)])
def test_only_error_records_outside_access_logs_are_scrubbed(logger, level):
    text = "owner Schmidtke not found alice@example.org"
    record = logging.LogRecord(logger, level, __file__, 1, text, (), None)
    assert text in RequestFormatter().format(record)


def test_postgres_details_are_removed_from_tracker_and_error_logs():
    original = Exception('duplicate key value violates unique constraint "person_name_key" (SQLSTATE 23505)\nDETAIL: Failing row contains (Jürgen, Schmidtke, private-row-value).\nKey (name)=(private-key-value) already exists.\nHINT: private-hint-value\nFailing row contains (private-continuation-value).')
    error = IntegrityError("INSERT INTO person (...) VALUES (...)", {}, original, hide_parameters=True)
    message = str(error)
    event = scrub({"exception": {"values": [{"type": "IntegrityError", "value": message}]}})
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "Database exception", (), (type(error), error, None))
    for result in (json.dumps(event, ensure_ascii=False), RequestFormatter().format(record)):
        for private in ("Jürgen", "Schmidtke", "private-row-value", "private-key-value", "private-hint-value", "private-continuation-value"):
            assert private not in result
        assert "person_name_key" in result
        assert "23505" in result


@pytest.mark.parametrize("name", ["Müller, Jürgen", "MÜLLER, JÜRGEN", "Ludwig van Beethoven", "Max von Muster", "José de García", "José da Silva", "Maria zu Münster", "José García"])
def test_error_context_names_include_accents_particles_and_surname_first(name):
    assert name not in scrub_text("Exception: " + name)


@pytest.mark.parametrize("message", ["duplicate Key (owner_name)=(Schmidtke) already exists", "owner Schmidtke not found"])
def test_structured_error_values_are_masked(message):
    assert "Schmidtke" not in scrub_text(message)


@pytest.mark.parametrize("iban", ["DE89-3704-0044-0532-0130-00", "de89 3704 0044 0532 0130 00", "gb82-west-1234-5698-7654-32"])
def test_grouped_and_lowercase_ibans_are_masked(iban):
    assert "[redacted:iban]" in scrub_text(iban)
    assert iban not in scrub_text(iban)


@pytest.mark.parametrize("text", ["1234567", "123456789012345", "2026-01-01", "created 2026-01-01T12:34:56Z", "INFO Max Mustermann"])
def test_numbers_dates_and_names_outside_error_context_remain_useful(text):
    assert scrub_text(text) == text


@pytest.mark.parametrize("phone", ["+49 30 12345678", "03012345678", "030/12345678", "(030) 12345678", "123 456 7890"])
def test_phone_shapes_are_masked(phone):
    assert phone not in scrub_text(phone)
    assert "[redacted:phone]" in scrub_text(phone)


@pytest.mark.parametrize("status", list(HTTPStatus))
def test_http_reason_phrases_are_preserved_even_in_exceptions(status):
    assert scrub_text("Exception: " + status.phrase) == "Exception: " + status.phrase


def test_every_diagnostic_container_and_allowed_header_value_is_scrubbed():
    private = "Exception: José García de89 3704 0044 0532 0130 00 alice@example.org"
    event = {
        "tags": {"custom": private}, "extra": {"nested": [private]},
        "contexts": {"custom": {"value": private}},
        "breadcrumbs": {"values": [{"data": {"value": private}}]},
        "request": {"headers": {
            "Content-Type": "application/json", "Content-Length": "12345678", "Accept": "application/json",
            "User-Agent": private, "X-Request-ID": "tracking-123", "X-Person-Name": "Schmidtke",
            "Authorization": "secret-auth", "Cookie": "secret-cookie", "Referer": "private-referrer",
        }},
    }
    before = copy.deepcopy(event)
    filtered = scrub(event)
    assert event == before
    serialized = json.dumps(filtered, ensure_ascii=False)
    for value in ("José García", "de89", "alice@example.org", "Schmidtke", "secret-auth", "secret-cookie", "private-referrer"):
        assert value not in serialized
    headers = filtered["request"]["headers"]
    assert set(headers) == {"Content-Type", "Content-Length", "Accept", "User-Agent", "X-Request-ID"}
    assert headers["Content-Length"] == "12345678"
    assert headers["X-Request-ID"] == "tracking-123"


def test_valid_tracker_identifiers_survive_scrubbing():
    original = {"event_id": "de890044053201300000000000000000", "contexts": {"trace": {"trace_id": "de890044053201300000000000000000", "span_id": "de89004405320130", "parent_span_id": "de89004405320130"}}}
    assert scrub(original) == original


def test_truncated_quoted_secrets_are_masked_before_the_cutoff():
    filtered = scrub_text('password="' + "q" * 20000)
    assert "qqqq" not in filtered
    assert "[redacted:password]" in filtered
    assert filtered.endswith("[truncated]")


@pytest.mark.parametrize("message", ['RuntimeError: password=super-secret-value', 'Exception: password="super-secret-value"', 'Exception: {"detail": "password=super-secret-value"}', 'Exception: error=password=super-secret-value'])
def test_exception_prefixes_and_nested_assignments_cannot_hide_secrets(message):
    error = RuntimeError(message)
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "Failure", (), (type(error), error, None))
    for filtered in (scrub_text(message), RequestFormatter().format(record), json.dumps(scrub({"exception": {"values": [{"value": message}]}}))):
        assert "super-secret-value" not in filtered
        assert "[redacted:password]" in filtered
