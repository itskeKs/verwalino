"""Meldungen: what the signed-in user is asked to look at, built from rows that already exist.

A Meldung is read, never stored: there is no table of Meldungen and no read state, the count in the frame is the
number of rows this service returns. Each kind of Meldung has one source below; a later kind adds its source here
and its rows join the same answer.

The first kind is the Teilungserklärung import (issue #871). Its Meldung belongs to the user who started the import
and to nobody else, an administrator included, and exists only while the import is started and not ended, waits for
the starter's review or has failed; `declarations/progress.py` decides which status is which and says the words.

The second kind is the Posteingang batch (issue #872). Its Meldung belongs to the user who uploaded the batch and to nobody
else, and exists while a file of the batch is received and waits, is being processed, waits for the organization's setup, or
has failed. A batch whose files are all at rest (their documents wait for review in the Posteingang, a file is still
missing, or the files wait for the decision how an original divides) is no Meldung: the Posteingang names those. The state
of a file is the one the batch page shows (`intake_batch_status`); `INTAKE_FILE_STATES` below decides which of them is a
Meldung, and the words of the stages are the batch page's own table, which the Meldung's row reads (`stage`).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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

INTAKE_KIND = "INTAKE_BATCH"
INTAKE_TITLE = "Dokumentenstapel"

# How far a file of a batch is, as the batch page names it (`stage` of `intake_batch_status`), the least advanced first.
INTAKE_STAGES = ("inspection", "preparation", "extraction", "split", "review")

# Which state of a file (its `status` and `stage`, the words of `intake_batch_status`) is a Meldung of its batch, and in which
# state: a file that is received and waits for the inspection, one that is processed, one that stopped with a failure. None:
# no Meldung, because the file is at rest: its upload is missing (the batch page offers to send it), the documents wait
# for review in the Posteingang, or they wait for the decision how an original divides. Every status and stage the database
# names has a decision here, a test fails for one that has none.
INTAKE_FILE_STATES: dict[tuple[str, str], MeldungState | None] = {
    ("waiting", "inspection"): None,
    ("received", "inspection"): "WAITING",
    ("processing", "inspection"): "RUNNING",
    ("failed", "inspection"): "FAILED",
    ("processing", "preparation"): "RUNNING",
    ("failed", "preparation"): "FAILED",
    ("processing", "extraction"): "RUNNING",
    ("failed", "extraction"): "FAILED",
    ("review", "split"): None,
    ("review", "review"): None,
}

# The reasons a file's extraction ended that the batch page does not call a failure: the organization's setup for the AI
# import is missing, the file is kept and can be requested again (`AWAITS_SETUP` in `frontend/src/lib/invoiceProcessing.ts`,
# which a test compares). Such a file waits.
AWAITS_SETUP = frozenset({"ai_import_disabled", "ai_inbox_confirmation_missing", "ai_document_model_missing", "ai_key_unreadable"})

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
    if state == "FAILED" and stage == "extraction" and error_code in AWAITS_SETUP:
        return "WAITING"
    return state


def intake_batch_state(groups: Sequence[FileGroup]) -> tuple[MeldungState, str] | None:
    """The state of a batch's Meldung and the stage it names, from how many of its files are in each state.

    The most urgent state of any file is the batch's: a failure before work that runs before work that waits. The stage is
    the least advanced one among the files in that state. None: no file is in a state that is a Meldung.
    """
    stages: dict[MeldungState, list[str]] = {}
    for group in groups:
        state = intake_file_state(group.status, group.stage, group.error_code)
        if state is not None:
            stages.setdefault(state, []).append(group.stage)
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
        """The user's Posteingang batches with a file that waits, runs or failed; every one of them is the user's own."""
        found = []
        for batch in await self._batches.progress(MELDUNGEN_LIMIT):
            decided = intake_batch_state(batch.groups)
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
