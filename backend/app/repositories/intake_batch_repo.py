"""What the files of a user's Posteingang batches are doing, read for the Meldungen (issue #872).

The intake tables are private to the application's database role: they are read through database functions that check the
actor. `intake_batch_progress` (migration 0205) answers for the newest batches of the signed-in user in one statement and
names no file, error text or document.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class FileGroup:
    """How many files of a batch are in one state: the words `intake_batch_status` gives a file."""

    status: str
    stage: str
    error_code: str | None
    count: int


@dataclass(frozen=True, slots=True)
class BatchProgress:
    id: uuid.UUID
    created_at: datetime
    item_count: int
    groups: tuple[FileGroup, ...]


class IntakeBatchRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID):
        self._session = session
        self._org_id = org_id

    async def progress(self, limit: int) -> list[BatchProgress]:
        """The signed-in user's newest `limit` batches with the states of their files, newest first.

        The database function takes the organization from the session and refuses another one, and answers only for the
        user the session belongs to; a batch of a scope the user may not read is left out as in the Posteingang's list.
        """
        rows = (await self._session.execute(
            text("SELECT intake_batch_progress(:org, :take)"), {"org": self._org_id, "take": limit},
        )).scalar_one()
        return [
            BatchProgress(
                id=uuid.UUID(row["id"]),
                created_at=datetime.fromisoformat(row["created_at"]),
                item_count=row["item_count"],
                groups=tuple(FileGroup(g["status"], g["stage"], g["error_code"], g["count"]) for g in row["groups"]),
            )
            for row in rows
        ]
