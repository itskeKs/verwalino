"""What a started declaration import is doing, for its status page and its status line.

One table here maps the import's lifecycle status and the kind of every recorded pass to the German label the
user reads; the status page and the status line take their words from it and nowhere else. Everything derives
from what the server recorded: the import's status, the passes of its current run (one ledger row per finished
provider attempt, with the time it was written) and the page transcripts that were saved. Nothing is estimated:
a step is running only while the status names it, and a count is a count of recorded rows.

The ledger row of a call is written when the call has ended, so the page cannot know which call of a stage is in
flight, only that the stage is running and how many calls of it have finished. The steps are therefore the
stages the pipeline records (preflight, extracting, reconciling) and the reading of pages, which happens inside
the first of them and is a step of its own only once a page of this import has been read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

StepState = Literal["DONE", "RUNNING", "WAITING"]


@dataclass(frozen=True, slots=True)
class _Step:
    key: str
    label: str
    # What the step counts, singular then plural: pages read, finished model requests. None: it counts nothing.
    count: tuple[str, str] | None = None


_REQUESTS = ("{n} Modellanfrage abgeschlossen", "{n} Modellanfragen abgeschlossen")

# The steps of a run in the order the pipeline takes them.
STEPS: tuple[_Step, ...] = (
    _Step("preflight", "Dateien prüfen"),
    _Step("reading", "Seiten lesen", ("{n} Seite gelesen", "{n} Seiten gelesen")),
    _Step("extraction", "Angaben auslesen", _REQUESTS),
    _Step("reconciliation", "Angaben abgleichen", _REQUESTS),
    _Step("review", "Zur Prüfung bereit"),
)

# The step each kind of recorded pass (`pass_id` of the ledger) belongs to. The invoice path's `extract_invoice`
# is metered elsewhere and never reaches this ledger.
PASS_STEPS: dict[str, str] = {
    "read_page": "reading",
    "inventory": "extraction",
    "extract_core": "extraction",
    "extract_rules": "extraction",
    "reconcile": "reconciliation",
    "recheck": "reconciliation",
}

# The step that runs while the import has this status. While a page of the run has been read, PREFLIGHT runs the
# reading step instead.
RUNNING_STATUS_STEPS: dict[str, str] = {
    "PREFLIGHT": "preflight",
    "EXTRACTING": "extraction",
    "RECONCILING": "reconciliation",
}

# The status line of every status that no running step names.
STATUS_LABELS: dict[str, str] = {
    "DRAFT": "Dokumente vorbereiten",
    "QUEUED": "Wartet auf Verarbeitung",
    "RETRY_WAIT": "Neuer Versuch ist eingeplant",
    "NEEDS_REVIEW": "Fachliche Prüfung",
    "APPLYING": "Wird übernommen",
    "APPLIED": "Übernommen",
    "FAILED": "Verarbeitung fehlgeschlagen",
    "CANCELLED": "Abgebrochen",
}

# Waiting for a worker: every step waits. Past the processing: every step is done. Any other status
# (DRAFT, FAILED, CANCELLED) has no list of steps.
_WAITING_STATUSES = {"QUEUED", "RETRY_WAIT"}
_FINISHED_STATUSES = {"NEEDS_REVIEW", "APPLYING", "APPLIED"}

_ORDER = [step.key for step in STEPS]
_STEPS = {step.key: step for step in STEPS}


@dataclass(frozen=True, slots=True)
class StepView:
    label: str
    state: StepState
    detail: str | None
    # Seconds since the step began, only for the running step and only when a run recorded a point to count from.
    elapsed_seconds: int | None


@dataclass(frozen=True, slots=True)
class Progress:
    status_label: str
    steps: tuple[StepView, ...]


def _current_run(status: str, runs: Sequence):
    """The run whose passes show what the import does now: the open one, or the last one of a finished import."""
    open_runs = [run for run in runs if run.completed_at is None]
    if open_runs:
        return open_runs[-1]
    return runs[-1] if runs and status in _FINISHED_STATUSES else None


def build_progress(
    status: str,
    *,
    runs: Sequence = (),
    passes: Sequence = (),
    pages_read: int = 0,
    now: datetime,
) -> Progress:
    """The steps of an import and its status line, from the recorded data only.

    `runs` are the import's extraction runs, oldest first. `passes` are the ledger rows of all of them (each with
    `declaration_extraction_run_id`, `created_at` and `safe_metadata`). `pages_read` is the number of page transcripts
    saved for the import, whichever run saved them: a resumed run reuses them.
    """
    run = _current_run(status, runs)
    recorded = [
        (PASS_STEPS[row.safe_metadata["pass_id"]], row.created_at, row.safe_metadata["failure_class"] is None)
        for row in passes
        if run is not None and row.declaration_extraction_run_id == run.id and row.safe_metadata["pass_id"] in PASS_STEPS
    ]
    running = RUNNING_STATUS_STEPS.get(status)
    if running == "preflight" and any(step == "reading" for step, _at, _ok in recorded):
        running = "reading"
    if status in _WAITING_STATUSES or status in _FINISHED_STATUSES or running:
        # The reading is a step once a page of this import was read, in this run or in an earlier one.
        keys = [key for key in _ORDER if key != "reading" or pages_read > 0 or any(step == key for step, _at, _ok in recorded)]
    else:
        keys = []
    position = len(keys) if status in _FINISHED_STATUSES else keys.index(running) if running else -1

    def view(index: int, key: str) -> StepView:
        count = pages_read if key == "reading" else sum(1 for step, _at, ok in recorded if step == key and ok)
        template = _STEPS[key].count
        elapsed = None
        if index == position and run is not None:
            # Counted from the last thing the run recorded for an earlier step, or from the start of the run.
            earlier = _ORDER[:_ORDER.index(key)]
            began = max([run.started_at, *(at for step, at, _ok in recorded if step in earlier)])
            elapsed = max(0, int((now - began).total_seconds()))
        return StepView(
            label=_STEPS[key].label,
            state="DONE" if index < position else "RUNNING" if index == position else "WAITING",
            detail=template[0 if count == 1 else 1].format(n=count) if template and count > 0 else None,
            elapsed_seconds=elapsed,
        )

    return Progress(
        status_label=_STEPS[running].label if running else STATUS_LABELS[status],
        steps=tuple(view(index, key) for index, key in enumerate(keys)),
    )
