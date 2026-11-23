"""Meldungen: what the signed-in user is asked to look at, built from rows that already exist.

A Meldung is read, never stored: there is no table of Meldungen and no read state, the count in the frame is the
number of rows this service returns. Each kind of Meldung has one source below; a later kind adds its source here
and its rows join the same answer.

The first kind is the Teilungserklärung import (issue #871). Its Meldung belongs to the user who started the import
and to nobody else, an administrator included, and exists only while the import is started and not ended, waits for
the starter's review or has failed; `declarations/progress.py` decides which status is which and says the words.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import Role
from app.declarations.progress import (
    MELDUNG_STATES,
    RUNNING_STATUS_STEPS,
    build_progress,
    meldung_text,
)
from app.repositories.declaration_import_repo import DeclarationImportRepository

# Most Meldungen one answer holds. The frame asks on every page, so the answer is bounded; a user who has started
# more open imports than this is shown the newest ones.
MELDUNGEN_LIMIT = 50

IMPORT_KIND = "DECLARATION_IMPORT"
IMPORT_TITLE = "Teilungserklärung einlesen"
NEW_PROPERTY_SUBJECT = "Neues Objekt"
EXISTING_PROPERTY_SUBJECT = "Bestehendes Objekt"

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
    # When the import was started (or last retried); absent when no start was recorded.
    at: datetime | None
    # The path of the application page that shows what the Meldung is about.
    target: str


class MeldungenService:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, role: str):
        self._imports = DeclarationImportRepository(session, org_id)
        self._user_id = user_id
        self._role = role

    async def list(self) -> list[Meldung]:
        """The caller's Meldungen, newest first, at most `MELDUNGEN_LIMIT`."""
        return await self._declaration_imports()

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
