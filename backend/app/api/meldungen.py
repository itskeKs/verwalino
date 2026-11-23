"""The caller's Meldungen: one read route over rows that already exist (issue #871)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.live_principal import get_live_member_db
from app.core.tenant import AuthContext, get_current_user
from app.services.meldungen_service import Meldung, MeldungenService

router = APIRouter(prefix="/api/meldungen", tags=["meldungen"])


class MeldungOut(BaseModel):
    kind: Literal["DECLARATION_IMPORT"]
    id: uuid.UUID
    title: str
    subject: str
    state: Literal["WAITING", "RUNNING", "REVIEW", "FAILED"]
    state_label: str
    # When the import was started (or last retried); absent when no start was recorded.
    at: datetime | None
    # The path of the application page that shows what the Meldung is about.
    target: str


class MeldungenOut(BaseModel):
    items: list[MeldungOut]


@router.get("", response_model=MeldungenOut)
async def list_meldungen(
    auth: AuthContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_live_member_db),
):
    """The Meldungen of the signed-in user and of nobody else, newest first, at most 50."""
    meldungen = await MeldungenService(db, auth.organization_id, auth.user_id, auth.role).list()
    return MeldungenOut(items=[_out(meldung) for meldung in meldungen])


def _out(meldung: Meldung) -> MeldungOut:
    return MeldungOut(
        kind=meldung.kind, id=meldung.id, title=meldung.title, subject=meldung.subject, state=meldung.state,
        state_label=meldung.state_label, at=meldung.at, target=meldung.target)
