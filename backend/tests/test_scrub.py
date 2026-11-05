"""Bounded diagnostic filtering without losing structural error information."""

import copy
from http import HTTPStatus
import json
import logging
import random
import time
from urllib.parse import quote
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.logging import RequestFormatter
from app.core.scrub import scrub, scrub_text


@pytest.mark.parametrize("text", ["a-" * 32000, "x-" * 32000, "/" + "p" * 20000], ids=["a-hyphens", "x-hyphens", "long-path"])
def test_hostile_text_is_scrubbed_within_two_seconds(text):
    started = time.perf_counter()
    filtered = scrub_text(text)
    elapsed = time.perf_counter() - started
    assert elapsed < 2, f"scrubbing took {elapsed:.3f} s"
    assert filtered.endswith("[truncated]")
    assert len(filtered) <= 8192 + len("[truncated]")


def test_long_access_record_is_formatted_within_two_seconds():
    path = "/" + "a" * 14000
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "GET %s HTTP/1.1" 404', ("127.0.0.1", path), None)
    started = time.perf_counter()
    formatted = RequestFormatter().format(record)
    elapsed = time.perf_counter() - started
    assert elapsed < 2, f"access formatting took {elapsed:.3f} s"
    assert path in formatted
    assert "request_id=" in formatted


def test_overlong_final_exception_line_keeps_its_type_and_masks_its_password():
    error = RuntimeError('password="' + "private-secret-" * 1000 + '"')
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "Failure", (), (type(error), error, None))
    formatted = RequestFormatter().format(record)
    assert "RuntimeError: password=[redacted:password]" in formatted
    assert "private-secret" not in formatted
    assert "[truncated]" in formatted


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


@pytest.mark.parametrize("level", ["info", "warning"])
@pytest.mark.parametrize("prefix", ["", "INFO "])
def test_leveled_breadcrumbs_mask_names_and_preserve_correlation(level, prefix):
    event = {"breadcrumbs": {"values": [{"level": level, "message": prefix + "Suche Mieter Erika Musterfrau", "data": {"request_id": "tracking-123"}}]}}
    breadcrumb = scrub(event)["breadcrumbs"]["values"][0]
    assert "Erika" not in breadcrumb["message"]
    assert "Musterfrau" not in breadcrumb["message"]
    assert "[redacted:name]" in breadcrumb["message"]
    assert breadcrumb["level"] == level
    assert breadcrumb["data"]["request_id"] == "tracking-123"


@pytest.mark.parametrize(("message", "private", "mask"), [
    ("Kontoauszug_DE89370400440532013000.pdf fehlt", "DE89370400440532013000", "iban"),
    ("Kontoauszug_DE89370400440532013000_2026.pdf fehlt", "DE89370400440532013000", "iban"),
    ("1DE89370400440532013000_2026", "DE89370400440532013000", "iban"),
    ("DE89370400440532013000123", "DE89370400440532013000", "iban"),
    ("x" * 80 + "_alice@example.org_2026.pdf", "alice@example.org", "email"),
    ("alice@one.two.three.four.example.org", "alice@one.two.three.four.example.org", "email"),
    ("alice@one.two.three.four.example.org_2026", "alice@one.two.three.four.example.org", "email"),
    ("MÜLLER, hans", "MÜLLER, hans", "name"),
    ("müller, Hans", "müller, Hans", "name"),
])
def test_identifiers_and_mixed_case_names_are_masked_in_events_and_error_logs(message, private, mask):
    error = RuntimeError(message)
    event = scrub({"exception": {"values": [{"type": "RuntimeError", "value": str(error)}]}})
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, message, (), (type(error), error, None))
    for filtered in (scrub_text(message, error_context=True), json.dumps(event, ensure_ascii=False), RequestFormatter().format(record)):
        assert private not in filtered
        assert f"[redacted:{mask}]" in filtered


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize(("prefix", "private"), [("", "padding"), ("\nDETAIL: ", "private-row-value"), ("password=", "private-password")], ids=["padding", "postgres-detail", "credential"])
def test_long_tracebacks_keep_the_final_exception_line_and_mask_its_name(cached, prefix, private):
    try:
        try:
            raise ValueError(prefix + private * (11000 // len(private) + 1))
        except ValueError as cause:
            raise RuntimeError("owner Schmidtke not found") from cause
    except RuntimeError as error:
        exc_info = (type(error), error, error.__traceback__)
    traceback = logging.Formatter().formatException(exc_info)
    assert len(traceback) > 11000
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, "Failure", (), None if cached else exc_info)
    if cached:
        record.exc_text = traceback
    formatted = RequestFormatter().format(record)
    assert formatted.endswith("RuntimeError: owner [redacted:name] not found")
    assert "Schmidtke" not in formatted
    assert private not in formatted
    assert "[truncated]" in formatted
    assert "request_id=" in formatted


@pytest.mark.parametrize("message", ["duplicate Key (owner_name)=(Schmidtke) already exists", "owner Schmidtke not found"])
def test_structured_error_values_are_masked(message):
    assert "Schmidtke" not in scrub_text(message)


@pytest.mark.parametrize("iban", ["DE89-3704-0044-0532-0130-00", "de89 3704 0044 0532 0130 00", "gb82-west-1234-5698-7654-32"])
def test_grouped_and_lowercase_ibans_are_masked(iban):
    assert "[redacted:iban]" in scrub_text(iban)
    assert iban not in scrub_text(iban)


@pytest.mark.parametrize("identity", [
    "ab123456-7890-4abc-8def-123456789abc",
    "12345678-ab12-4abc-8def-123456789abc",
    "12345678-1234-4abc-ab12-123456789abc",
    "12345678-1234-4234-8234-123456789012",
])
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_canonical_uuid_identifiers_survive_text_events_and_error_logs(identity, case):
    identity = case(identity)
    message = f"organization {identity} erased"
    assert scrub_text(message) == message
    assert scrub({"exception": {"values": [{"value": message}]}})["exception"]["values"][0]["value"] == message
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, message, (), None)
    assert message in RequestFormatter().format(record)


def test_generated_uuid_identifiers_remain_complete_in_mixed_text():
    generator = random.Random(196)
    for _ in range(2000):
        identity = str(UUID(int=generator.getrandbits(128), version=4))
        for value in (identity, identity.upper(), identity.title()):
            message = f"organization {value} erased; run_{value}.json; /payments/{value}"
            assert scrub_text(message) == message


@pytest.mark.parametrize("iban", [
    "DE89370400440532013000", "DE89 3704 0044 0532 0130 00", "DE89-3704-0044-0532-0130-00",
    "GB82WEST12345698765432", "GB82 WEST 1234 5698 7654 32", "GB82-WEST-1234-5698-7654-32",
])
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
@pytest.mark.parametrize("identity", ["ab123456-7890-4abc-8def-123456789abc", "12345678-1234-4234-8234-123456789012"])
@pytest.mark.parametrize("separator", [" ", "-", "/"])
def test_uuid_spans_do_not_hide_ibans_or_other_secrets(iban, case, identity, separator):
    from app.diagnostics import diagnostic_log_lines

    iban = case(iban)
    message = f"organization {identity}; {iban}{separator}{identity}; {identity}{separator}{iban}; +49 30 12345678; alice@example.org password=private-value"
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, message, (), (RuntimeError, RuntimeError(message), None))
    record.request_id = identity
    formatted = RequestFormatter().format(record)
    event = {
        "exception": {"values": [{"value": message}]},
        "request": {"url": f"https://example.org/payments/{identity}?iban={quote(iban)}",
                    "headers": {"User-Agent": message, "Authorization": identity}},
        "extra": {"run_id": identity, "payment_id": identity},
    }
    filtered_event = scrub(event)
    for filtered in (scrub_text(message), formatted, json.dumps(filtered_event)):
        assert identity in filtered
        for private in (iban, "+49 30 12345678", "alice@example.org", "private-value"):
            assert private not in filtered
        for mask in ("iban", "phone", "email", "password"):
            assert f"[redacted:{mask}]" in filtered
    assert filtered_event["extra"] == event["extra"]
    assert filtered_event["request"]["url"] == f"https://example.org/payments/{identity}?iban=[redacted:query]"
    assert filtered_event["request"]["headers"] == {"User-Agent": scrub_text(message, error_context=True)}
    assert diagnostic_log_lines(formatted.splitlines()) == [
        f"{formatted[:23]} ERROR request_id={identity} RuntimeError",
    ]


@pytest.mark.parametrize("candidate", ["DE00370400440532013000", "ZZ89370400440532013000", "DE89370400440532013000123"])
def test_invalid_iban_shapes_are_still_conservatively_masked(candidate):
    assert scrub_text(candidate) == "[redacted:iban]"


@pytest.mark.parametrize("identity", ["ab123456-7890-4abc-8def-123456789abc", "12345678-1234-4234-8234-123456789012"])
def test_uuid_valued_credentials_and_queries_remain_redacted(identity):
    assert identity not in scrub_text(f"password={identity} token={identity} https://example.org/?id={identity}")


@pytest.mark.parametrize("template", ["organization {identity}{separator}{phone} erased", "contact {phone}{separator}{identity} erased"])
@pytest.mark.parametrize("separator", [" ", "-", "/", "- ", " - ", "/ ", " / ", ";", ",", ":"])
@pytest.mark.parametrize(("phone", "masked"), [
    ("03012345678", "[redacted:phone]"), ("+49 30 12345678", "[redacted:phone]"),
    ("030/12345678", "[redacted:phone]"), ("(030) 12345678", "[redacted:phone]"),
    ("123 456 7890", "[redacted:phone]"), ("(12345678)", "[redacted:phone])"),
])
@pytest.mark.parametrize("identity", ["12345678-1234-4234-8234-123456789012", "ab123456-7890-4abc-8def-123456789abc"])
def test_uuid_survives_next_to_a_redacted_phone(template, separator, phone, masked, identity):
    message = template.format(identity=identity, separator=separator, phone=phone)
    expected = template.format(identity=identity, separator=separator, phone=masked)
    assert scrub_text(message) == expected
    assert scrub({"exception": {"values": [{"value": message}]}})["exception"]["values"][0]["value"] == expected
    record = logging.LogRecord("app.test", logging.ERROR, __file__, 1, message, (), None)
    assert expected in RequestFormatter().format(record)


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
