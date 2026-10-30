"""Unauthenticated first-run entry points; all creation is gated in PostgreSQL."""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.auth import LoginResponse, _set_session_cookie
from app.core.auth_db import lookup_login_principal
from app.core.db import get_session_factory
from app.core.security import create_session_token, hash_password, normalize_email, validate_new_password

logger = logging.getLogger(__name__)


class SetupRoute(APIRoute):
    """Keep tokens/passwords out of validation responses and diagnostics."""

    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            if request.method != "GET":
                # Check closure before FastAPI parses even malformed JSON.
                await require_setup()
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=422, content={
                    "detail": "Eingaben ungültig. Bitte prüfen Sie die Felder und verwenden Sie ein Passwort mit 12 bis 128 Zeichen.",
                })
        return handle


router = APIRouter(prefix="/api/setup", tags=["setup"], route_class=SetupRoute)
REFUSED = "Einrichtung nicht möglich. Bitte prüfen Sie den Einrichtungs-Token."


class SetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token: SecretStr = Field(max_length=256)
    organization_name: str = Field(min_length=1, max_length=255)
    admin_email: str
    admin_name: str = Field(min_length=1, max_length=255)
    password: SecretStr

    @field_validator("token", "organization_name", "admin_name", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            if re.search(r"[\x00-\x1f\x7f-\x9f]", value):
                raise ValueError("Control characters are not allowed")
            return value.strip()
        return value

    @field_validator("admin_email", mode="before")
    @classmethod
    def email_is_valid(cls, value: object) -> str:
        normalized = normalize_email(cls.clean_text(value))
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Invalid email")
        return normalized

    @field_validator("password")
    @classmethod
    def password_is_valid(cls, value: SecretStr) -> SecretStr:
        validate_new_password(value.get_secret_value())
        return value


async def setup_required() -> bool:
    async with get_session_factory()() as session, session.begin():
        return not await session.scalar(text("SELECT EXISTS(SELECT 1 FROM public.organization)"))


async def require_setup() -> None:
    if not await setup_required():
        # Match the production SPA fallback as well as FastAPI's default 404.
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("")
async def status(response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    return {"required": await setup_required()}


@router.post("", status_code=201, response_model=LoginResponse)
async def complete_setup(body: SetupRequest, response: Response) -> LoginResponse:
    password_hash = hash_password(body.password.get_secret_value())
    try:
        async with get_session_factory()() as session, session.begin():
            org = await session.scalar(text("""SELECT public.setup_first_organization(
                :token,:organization_name,:email,:name,:password_hash)"""), {
                    "token": body.token.get_secret_value(), "organization_name": body.organization_name,
                    "email": body.admin_email, "name": body.admin_name, "password_hash": password_hash,
                })
            principal = await lookup_login_principal(session, body.admin_email)
            if principal is None or principal.organization_id != org:
                raise HTTPException(status_code=403, detail=REFUSED)
    except DBAPIError as error:
        # Hide sensitive setup parameters without changing other routes' errors.
        error.hide_parameters = True
        # Classification uses SQLSTATE, never driver message text or input values.
        sqlstate = getattr(error.orig, "sqlstate", None)
        if sqlstate != "P0001":
            logger.warning("Setup database error (SQLSTATE %s)", sqlstate)
            raise HTTPException(status_code=503, detail="Einrichtung derzeit nicht verfügbar.") from None
        await require_setup()
        raise HTTPException(status_code=403, detail=REFUSED) from None
    response.headers["Cache-Control"] = "no-store"
    _set_session_cookie(response, create_session_token(
        principal.user_id, principal.organization_id, principal.session_version))
    return LoginResponse(user_id=str(principal.user_id), organization_id=str(org), role=principal.role)


@router.api_route("", methods=["HEAD", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"], include_in_schema=False)
async def unsupported_method() -> None:
    # SetupRoute checks closure before this method refusal or any body parsing.
    raise HTTPException(status_code=405, detail="Method Not Allowed", headers={"Allow": "GET, POST"})
