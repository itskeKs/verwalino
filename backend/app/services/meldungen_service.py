"""Meldungen: what the signed-in user is asked to look at, built from rows that already exist.

A Meldung is read, never stored: there is no table of Meldungen and no read state, the count in the frame is the
number of rows this service returns. Each kind of Meldung has one source below; a later kind adds its source here
and its rows join the same answer.

The first kind is the Teilungserklärung import (issue #871). Its Meldung belongs to the user who started the import
and to nobody else, an administrator included, and exists only while the import is started and not ended, waits for
the starter's review or has failed; `declarations/progress.py` decides which status is which and says the words.

The second kind is the Posteingang batch (issue #872). Its Meldung belongs to the user who uploaded the batch and to nobody
else, and exists while a file of the batch is received and waits, is being processed, or has failed. A file at rest is none of
these: its documents wait for review in the Posteingang, a file is still missing, the files wait for the decision how an
original divides, or the evaluation waits for the organization's AI setup, which may never come (nothing runs and nothing
failed). A batch whose files are all at rest is no Meldung: the Posteingang names those. A batch that stopped with a failure
stays a Meldung for `FAILED_LISTED_FOR` after its latest failure and then leaves the list (it stays on its batch page); a batch
that still has a file that waits or runs stays listed whatever its age. The state of a file is the one the batch page shows
(`intake_batch_status`); `INTAKE_FILE_STATES` and `EXTRACTION_STOPS` below decide which of them is a Meldung, and the words of
the stages are the batch page's own table, which the Meldung's row reads (`stage`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Role
from app.declarations.progress import (
    MELDUNG_STATES,
    RUNNING_STATUS_STEPS,
    STATUS_LABELS,
    MeldungState,
    build_progress,
    meldung_text,
)
from app.repositories.declaration_import_repo import DeclarationImportRepository
from app.repositories.intake_batch_repo import FileGroup, IntakeBatchRepository

# Most Meldungen one answer holds. The frame asks on every page, so the answer is bounded; a user who has started
# more open imports than this is shown the newest ones, and the Posteingang batches are read from the newest this many
# batches of the user.
MELDUNGEN_LIMIT = 50

IMPORT_KIND = "DECLARATION_IMPORT"
IMPORT_TITLE = "Teilungserklärung einlesen"
NEW_PROPERTY_SUBJECT = "Neues Objekt"
EXISTING_PROPERTY_SUBJECT = "Bestehendes Objekt"

# How long a batch that stopped with a failure stays a Meldung, counted from the latest failure of one of its files (the
# latest change of a failed job of the batch, so a file that failed again after a retry starts it over; the upload time would
# list a batch uploaded a month ago whose retry failed just now for no time at all). A failure the user cannot retry (a rejected
# file, an extraction that failed three times) would otherwise stay listed for good (owner decision on issue #872). A failure
# is listed while it is younger than this, and on the day it is this old it leaves; the batch page keeps showing it.
FAILED_LISTED_FOR = timedelta(days=14)

INTAKE_KIND = "INTAKE_BATCH"
INTAKE_TITLE = "Dokumentenstapel"

# How far a file of a batch is, as the batch page names it (`stage` of `intake_batch_status`), the least advanced first.
INTAKE_STAGES = ("inspection", "preparation", "extraction", "split", "review")

# Which state of a file (its `status` and `stage`, the words of `intake_batch_status`) is a Meldung of its batch, and in which
# state: WAITING for a file the worker will pick up by itself, RUNNING for one that is processed, FAILED for one that stopped
# with a failure. None: the file is at rest, nothing runs and nothing moves it but a person or the organization's setup. Every
# status and stage the database names has a decision here, a test fails for one that has none; one line of reason each.
INTAKE_FILE_STATES: dict[tuple[str, str], MeldungState | None] = {
    # No file arrived: only its uploader sending it on the batch page moves it, and the batch page offers that. The reading the
    # issue leaves to the owner: a batch of such files is not a Meldung (the Posteingang names it).
    ("waiting", "inspection"): None,
    # Queued for the inspection: the worker takes it by itself.
    ("received", "inspection"): "WAITING",
    ("processing", "inspection"): "RUNNING",
    # Scanner or parser blocked or rejected the file: its uploader requests a blocked one again on the batch page, a rejected
    # one is failed for good; no setting of the organization moves either.
    ("failed", "inspection"): "FAILED",
    ("processing", "preparation"): "RUNNING",
    # The delivery of the inspected original stopped; nothing the organization sets or the uploader does moves it.
    ("failed", "preparation"): "FAILED",
    ("processing", "extraction"): "RUNNING",
    # The extraction ended without a proposal: `EXTRACTION_STOPS` decides by the reason.
    ("failed", "extraction"): "FAILED",
    # A person decides how the original divides (the Posteingang asks), and the proposals wait for review there: not the
    # setup, not the worker, and the Posteingang names both.
    ("review", "split"): None,
    ("review", "review"): None,
}

# What a file whose extraction ended without a proposal (`failed` at `extraction`) is, by the reason it ended (`error_code` of
# its extraction job): every reason the inbox's extraction stores has a decision here, a test fails for one that has none.
# None: at rest. FAILED: the batch page calls it a failure and its uploader is told. The reasons that wait for a person's
# request again are failures, not waiting: the worker does not take such a job up again by itself.
EXTRACTION_STOPS: dict[str, MeldungState | None] = {
    # Only the organization's settings move these, an administrator's action: the AI import is opt-in and nothing is read
    # locally, so an organization that never sets it up never gets past this. The file is kept, nothing runs and nothing
    # failed, and the batch page counts it as received (`AWAITS_SETUP` of `invoiceProcessing.ts`): at rest.
    **dict.fromkeys(("ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable"), None),
    # Failed; the passing of time (the budget's month), the provider, a repaired installation or the uploader's own request
    # moves it, so it is not a setup that may never come: the uploader requests the evaluation again (an administrator may raise
    # a limit first). A run that the settings changed under (`ai_settings_changed`) takes the current ones the next time.
    **dict.fromkeys(("organization_ai_budget_exceeded", "ai_settings_changed", "provider_unreachable", "model_reading_failed",
                     "model_extraction_failed", "model_result_invalid", "lease_lost", "render_timeout", "extraction_timeout",
                     "render_unavailable", "pdf_parser_unavailable", "processor_failed", "storage_integrity_mismatch",
                     "stored_original_unavailable"), "FAILED"),
    # Failed for good: the file cannot be evaluated or the attempts are used; only a new submission helps.
    **dict.fromkeys(("file_size_limit", "mime_mismatch", "invalid_page_selection", "source_page_count_changed", "unsupported_format",
                     "invalid_document", "image_pixel_limit", "attempt_limit"), "FAILED"),
    # Its uploader lost the permission to work on the document, so the request again needs the permission back, an
    # administrator's action; nothing runs and the batch page calls it failed, so its uploader is told.
    "initiator_permission_changed": "FAILED",
}

# The reasons of an extraction that wait for the organization's setup (the ones at rest); `AWAITS_SETUP` in
# `frontend/src/lib/invoiceProcessing.ts`, which the batch page counts as received and a test compares, is the same set.
AWAITS_SETUP = frozenset(code for code, state in EXTRACTION_STOPS.items() if state is None)

# What the badge of a batch says. The words of the stage are the batch page's, shown by the row beside it.
INTAKE_STATE_LABELS: dict[str, str] = {"WAITING": STATUS_LABELS["QUEUED"], "RUNNING": "Läuft", "FAILED": STATUS_LABELS["FAILED"]}

# The roles that may open the pages of an import (the import routes take the same roles). Whoever lost the role has no
# Meldung about an import: its target is a page that user cannot open.
_IMPORT_ROLES = {Role.OWNER_ADMIN.value, Role.CLERK.value}


@dataclass(frozen=True, slots=True)
class Meldung:
    kind: str
    id: uuid.UUID
    title: str
    # What the Meldung is about: the property, or the new property and the titles of its documents.
    subject: str
    state: str
    state_label: str
    # When the import was started (or last retried), or the batch was uploaded; absent when no start was recorded.
    at: datetime | None
    # The path of the application page that shows what the Meldung is about.
    target: str
    # How far the work has come, in the batch page's words (a key of its table); only a Posteingang batch names one.
    stage: str | None = None


def intake_file_state(status: str, stage: str, error_code: str | None) -> MeldungState | None:
    """The state of the Meldung one file of a batch stands for, if it stands for one."""
    state = INTAKE_FILE_STATES[(status, stage)]
    if state == "FAILED" and stage == "extraction":
        # A reason nobody decided is a failure: the user is told rather than left without a word.
        return EXTRACTION_STOPS.get(error_code or "", "FAILED")
    return state


def intake_batch_state(groups: Sequence[FileGroup], now: datetime) -> tuple[MeldungState, str] | None:
    """The state of a batch's Meldung and the stage it names, from how many of its files are in each state.

    The most urgent state of any file is the batch's: a failure before work that runs before work that waits. The stage is
    the least advanced one among the files in that state. None: no file is in a state that is a Meldung, or the batch has
    only failed files and the latest of those failures is `FAILED_LISTED_FOR` old.
    """
    stages: dict[MeldungState, list[str]] = {}
    failed_at: list[datetime] = []
    for group in groups:
        state = intake_file_state(group.status, group.stage, group.error_code)
        if state is not None:
            stages.setdefault(state, []).append(group.stage)
            if state == "FAILED" and group.failed_at is not None:
                failed_at.append(group.failed_at)
    # A file that waits or runs keeps the batch listed whatever its age; a failure without a time is never cut.
    if set(stages) == {"FAILED"} and len(failed_at) == len(stages["FAILED"]) and now - max(failed_at) >= FAILED_LISTED_FOR:
        return None
    for state in ("FAILED", "RUNNING", "WAITING"):
        if state in stages:
            return state, min(stages[state], key=INTAKE_STAGES.index)
    return None


class MeldungenService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, role: str):
        self._imports = DeclarationImportRepository(session, org_id)
        self._batches = IntakeBatchRepository(session, org_id)
        self._user_id = user_id
        self._role = role

    async def list(self) -> list[Meldung]:
        """The caller's Meldungen of every kind, newest first, at most `MELDUNGEN_LIMIT`."""
        found = [*await self._declaration_imports(), *await self._intake_batches()]
        # Stable: Meldungen of one time keep the order of their kinds; one that records no time is the oldest.
        found.sort(key=lambda meldung: meldung.at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return found[:MELDUNGEN_LIMIT]

    async def _intake_batches(self) -> list[Meldung]:
        """The user's Posteingang batches with a file that waits, runs or failed (not too long ago); every one of them is the user's own."""
        found = []
        now = datetime.now(timezone.utc)
        for batch in await self._batches.progress(MELDUNGEN_LIMIT):
            decided = intake_batch_state(batch.groups, now)
            if decided is None:
                continue
            state, stage = decided
            found.append(Meldung(
                kind=INTAKE_KIND,
                id=batch.id,
                title=INTAKE_TITLE,
                subject="1 Datei" if batch.item_count == 1 else f"{batch.item_count} Dateien",
                state=state,
                state_label=INTAKE_STATE_LABELS[state],
                at=batch.created_at,
                target=f"/posteingang/{batch.id}",
                stage=stage,
            ))
        return found

    async def _declaration_imports(self) -> list[Meldung]:
        if self._role not in _IMPORT_ROLES:
            return []
        statuses = [status for status, state in MELDUNG_STATES.items() if state is not None]
        rows = await self._imports.list_started_by(self._user_id, statuses, MELDUNGEN_LIMIT)
        if not rows:
            return []
        new_properties = [row.id for row in rows if row.mode == "NEW_PROPERTY"]
        titles = await self._imports.document_titles(new_properties) if new_properties else {}
        # The step that runs can depend on what the run recorded (a page was read), so the running imports are
        # read the way their status page reads them, once for all of them.
        running = [row.id for row in rows if row.status in RUNNING_STATUS_STEPS]
        runs = await self._imports.runs_of(running) if running else []
        passes = await self._imports.passes_of(running) if running else []
        pages = await self._imports.page_reading_counts(running) if running else {}
        now = datetime.now(timezone.utc)
        found = []
        for row in rows:
            progress = build_progress(
                row.status,
                runs=[run for run in runs if run.import_id == row.id],
                passes=passes,
                pages_read=pages.get(row.id, 0),
                now=now,
            )
            found.append(Meldung(
                kind=IMPORT_KIND,
                id=row.id,
                title=IMPORT_TITLE,
                subject=_subject(row.mode, row.property_name, titles.get(row.id, [])),
                state=MELDUNG_STATES[row.status],
                state_label=meldung_text(row.status, progress),
                at=row.started_at,
                target=_target(row.id, row.status),
            ))
        return found


def _subject(mode: str, property_name: str | None, document_titles: list[str]) -> str:
    if mode == "EXISTING_PROPERTY":
        return property_name or EXISTING_PROPERTY_SUBJECT
    return f"{NEW_PROPERTY_SUBJECT}: {', '.join(document_titles)}" if document_titles else NEW_PROPERTY_SUBJECT


def _target(import_id: uuid.UUID, status: str) -> str:
    """The page of the import: its review when the review is due, otherwise its status page."""
    path = f"/teilungserklaerung/import/{import_id}"
    return f"{path}/review" if status == "NEEDS_REVIEW" else path
