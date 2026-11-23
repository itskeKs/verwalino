"""The steps of a started declaration import come from what the server recorded, in one table of German labels."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os
import re

import pytest

from app.declarations.observability import _PASS_IDS
from app.declarations.progress import PASS_STEPS, RUNNING_STATUS_STEPS, STATUS_LABELS, STEPS, build_progress
from app.declarations.provider import INVOICE_PASS_ID

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
RAW_NAMES = {"queued", "preflight", "extracting", "reconciling", "retry_wait", "review", "failed", "applied"}


def open_run(*, seconds_ago=600):
    return SimpleNamespace(id=uuid4(), started_at=NOW - timedelta(seconds=seconds_ago), completed_at=None)


def closed_run(*, seconds_ago=3600, lasted=300):
    started = NOW - timedelta(seconds=seconds_ago)
    return SimpleNamespace(id=uuid4(), started_at=started, completed_at=started + timedelta(seconds=lasted))


def recorded(run, kind, *, seconds_ago, failure=None):
    return SimpleNamespace(declaration_extraction_run_id=run.id, created_at=NOW - timedelta(seconds=seconds_ago),
                           safe_metadata={"pass_id": kind, "failure_class": failure})


def shown(progress):
    return [(step.label, step.state, step.detail, step.elapsed_seconds) for step in progress.steps]


def test_a_draft_has_no_steps_and_a_label():
    progress = build_progress("DRAFT", now=NOW)
    assert (progress.status_label, progress.steps) == ("Dokumente vorbereiten", ())


def test_a_queued_import_says_it_waits_and_every_step_waits():
    progress = build_progress("QUEUED", now=NOW)
    assert progress.status_label == "Wartet auf Verarbeitung"
    assert shown(progress) == [
        ("Dateien prüfen", "WAITING", None, None),
        ("Angaben auslesen", "WAITING", None, None),
        ("Angaben abgleichen", "WAITING", None, None),
        ("Zur Prüfung bereit", "WAITING", None, None),
    ]


def test_a_retry_says_a_new_attempt_is_scheduled_and_keeps_the_pages_it_already_read():
    earlier = closed_run()
    passes = [recorded(earlier, "inventory", seconds_ago=3500), recorded(earlier, "read_page", seconds_ago=3550)]
    progress = build_progress("RETRY_WAIT", runs=[earlier], passes=passes, pages_read=4, now=NOW)
    assert progress.status_label == "Neuer Versuch ist eingeplant"
    # The failed run's calls are not this attempt's; the saved page transcripts are reused by the next run.
    assert shown(progress) == [
        ("Dateien prüfen", "WAITING", None, None),
        ("Seiten lesen", "WAITING", "4 Seiten gelesen", None),
        ("Angaben auslesen", "WAITING", None, None),
        ("Angaben abgleichen", "WAITING", None, None),
        ("Zur Prüfung bereit", "WAITING", None, None),
    ]


def test_preflight_before_the_run_exists_runs_without_a_time():
    progress = build_progress("PREFLIGHT", runs=[closed_run()], now=NOW)
    assert progress.status_label == "Dateien prüfen"
    assert shown(progress)[0] == ("Dateien prüfen", "RUNNING", None, None)


def test_preflight_counts_its_time_from_the_start_of_the_run():
    run = open_run(seconds_ago=42)
    progress = build_progress("PREFLIGHT", runs=[run], now=NOW)
    assert shown(progress)[:2] == [("Dateien prüfen", "RUNNING", None, 42), ("Angaben auslesen", "WAITING", None, None)]


def test_a_page_read_in_this_run_makes_the_reading_the_running_step():
    run = open_run(seconds_ago=120)
    passes = [recorded(run, "read_page", seconds_ago=90), recorded(run, "read_page", seconds_ago=30)]
    progress = build_progress("PREFLIGHT", runs=[run], passes=passes, pages_read=2, now=NOW)
    assert progress.status_label == "Seiten lesen"
    assert shown(progress)[:3] == [
        ("Dateien prüfen", "DONE", None, None),
        ("Seiten lesen", "RUNNING", "2 Seiten gelesen", 120),
        ("Angaben auslesen", "WAITING", None, None),
    ]


def test_extraction_without_a_finished_call_runs_from_the_end_of_the_reading():
    run = open_run(seconds_ago=300)
    passes = [recorded(run, "read_page", seconds_ago=200)]
    progress = build_progress("EXTRACTING", runs=[run], passes=passes, pages_read=1, now=NOW)
    assert progress.status_label == "Angaben auslesen"
    assert shown(progress)[:4] == [
        ("Dateien prüfen", "DONE", None, None),
        ("Seiten lesen", "DONE", "1 Seite gelesen", None),
        ("Angaben auslesen", "RUNNING", None, 200),
        ("Angaben abgleichen", "WAITING", None, None),
    ]


def test_extraction_counts_only_finished_calls_of_its_three_pass_kinds():
    run = open_run(seconds_ago=300)
    passes = [
        recorded(run, "inventory", seconds_ago=250),
        recorded(run, "extract_core", seconds_ago=100, failure="PROVIDER_SCHEMA_FAILURE"),
        recorded(run, "extract_core", seconds_ago=60),
    ]
    extraction = shown(build_progress("EXTRACTING", runs=[run], passes=passes, now=NOW))[1]
    # The attempt that failed its schema is not a finished call. No earlier step recorded anything: the run's start counts.
    assert extraction == ("Angaben auslesen", "RUNNING", "2 Modellanfragen abgeschlossen", 300)


def test_a_single_finished_call_is_singular():
    run = open_run()
    passes = [recorded(run, "inventory", seconds_ago=100)]
    assert shown(build_progress("EXTRACTING", runs=[run], passes=passes, now=NOW))[1][2] == "1 Modellanfrage abgeschlossen"


def test_reconciling_counts_from_the_last_finished_extraction_call():
    run = open_run(seconds_ago=500)
    passes = [recorded(run, "inventory", seconds_ago=400), recorded(run, "extract_core", seconds_ago=300),
              recorded(run, "extract_rules", seconds_ago=90), recorded(run, "reconcile", seconds_ago=20)]
    progress = build_progress("RECONCILING", runs=[run], passes=passes, now=NOW)
    assert progress.status_label == "Angaben abgleichen"
    assert shown(progress) == [
        ("Dateien prüfen", "DONE", None, None),
        ("Angaben auslesen", "DONE", "3 Modellanfragen abgeschlossen", None),
        ("Angaben abgleichen", "RUNNING", "1 Modellanfrage abgeschlossen", 90),
        ("Zur Prüfung bereit", "WAITING", None, None),
    ]


def test_a_new_run_does_not_count_the_calls_of_an_earlier_one():
    earlier, current = closed_run(), open_run(seconds_ago=60)
    passes = [recorded(earlier, "inventory", seconds_ago=3500), recorded(earlier, "extract_core", seconds_ago=3400),
              recorded(current, "inventory", seconds_ago=30)]
    extraction = shown(build_progress("EXTRACTING", runs=[earlier, current], passes=passes, now=NOW))[1]
    assert extraction == ("Angaben auslesen", "RUNNING", "1 Modellanfrage abgeschlossen", 60)


def test_a_finished_import_shows_every_step_done_with_what_its_last_run_recorded():
    run = closed_run()
    passes = [recorded(run, "read_page", seconds_ago=3500), recorded(run, "inventory", seconds_ago=3400),
              recorded(run, "extract_core", seconds_ago=3300), recorded(run, "extract_rules", seconds_ago=3200),
              recorded(run, "reconcile", seconds_ago=3100), recorded(run, "recheck", seconds_ago=3050)]
    progress = build_progress("NEEDS_REVIEW", runs=[run], passes=passes, pages_read=1, now=NOW)
    assert progress.status_label == "Fachliche Prüfung"
    assert shown(progress) == [
        ("Dateien prüfen", "DONE", None, None),
        ("Seiten lesen", "DONE", "1 Seite gelesen", None),
        ("Angaben auslesen", "DONE", "3 Modellanfragen abgeschlossen", None),
        ("Angaben abgleichen", "DONE", "2 Modellanfragen abgeschlossen", None),
        ("Zur Prüfung bereit", "DONE", None, None),
    ]


@pytest.mark.parametrize("status, label", [("FAILED", "Verarbeitung fehlgeschlagen"), ("CANCELLED", "Abgebrochen")])
def test_a_failed_or_cancelled_import_lists_no_steps(status, label):
    run = closed_run()
    progress = build_progress(status, runs=[run], passes=[recorded(run, "inventory", seconds_ago=3000)], now=NOW)
    assert (progress.status_label, progress.steps) == (label, ())


def test_a_time_is_never_negative_and_a_kind_the_table_does_not_know_is_not_a_step():
    run = open_run(seconds_ago=-5)
    passes = [recorded(run, "extract_invoice", seconds_ago=1)]
    progress = build_progress("EXTRACTING", runs=[run], passes=passes, now=NOW)
    assert shown(progress)[1] == ("Angaben auslesen", "RUNNING", None, 0)


# ---- the guard: nothing the backend can produce reaches the page without a label ----------------------------------

def test_every_kind_of_pass_the_ledger_accepts_belongs_to_a_step():
    assert set(PASS_STEPS) == _PASS_IDS - {INVOICE_PASS_ID}
    assert set(PASS_STEPS.values()) <= {step.key for step in STEPS}


def test_every_step_has_a_label_and_no_label_is_a_raw_name():
    labels = [step.label for step in STEPS]
    assert len(set(labels)) == len(labels) and all(labels)
    assert not RAW_NAMES & {label.casefold() for label in [*labels, *STATUS_LABELS.values()]}


def stages_in_the_source():
    """Every value any code path writes to `declaration_import.current_stage`."""
    backend = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"""current_stage['"]?\s*[:=]\s*['"]([a-z_]+)['"]""")
    return {stage for path in [*(backend / "app").rglob("*.py"), *(backend / "migrations/versions").glob("*.py")]
            for stage in pattern.findall(path.read_text())}


# The status each stage the backend writes stands for; a stage that is not here is a stage without a label.
STAGE_STATUSES = {"queued": "QUEUED", "preflight": "PREFLIGHT", "extracting": "EXTRACTING", "reconciling": "RECONCILING",
                  "review": "NEEDS_REVIEW", "retry_wait": "RETRY_WAIT", "failed": "FAILED", "applied": "APPLIED"}


def test_every_stage_the_backend_writes_has_a_status_with_a_label():
    assert stages_in_the_source() == set(STAGE_STATUSES)
    for status in STAGE_STATUSES.values():
        assert status in STATUS_LABELS or status in RUNNING_STATUS_STEPS


def database_statuses():
    import psycopg2
    with psycopg2.connect(os.environ["HV_DATABASE_URL_SYNC"]) as connection, connection.cursor() as cursor:
        cursor.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'public.declaration_import'::regclass AND conname LIKE '%declaration_import_status'""")
        (definition,) = cursor.fetchone()
    return set(re.findall(r"'([A-Z_]+)'", definition))


def test_every_status_the_database_allows_has_a_label():
    statuses = database_statuses()
    assert {"DRAFT", "QUEUED", "NEEDS_REVIEW", "FAILED"} <= statuses
    for status in statuses:
        label = build_progress(status, now=NOW).status_label
        assert label and label.casefold() not in RAW_NAMES and label != status
