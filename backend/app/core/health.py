"""Readiness probes for the application process and its local dependencies."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

from sqlalchemy import text

from app.core.config import settings
from app.core.db import get_engine

_MAX_PROBE_LOG_MESSAGE = 256
_URI_AUTHORITY_RE = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)([^/\s?#]+)")
_PASSWORD_FRAGMENT_RE = re.compile(
    r"(?i)(\b[\w-]*(?:password|passwd|pwd|secret|token|key)[\w-]*)(\s*[=:]\s*)([^\s,;]+)"
)


def _sanitize_probe_message(message: str) -> str:
    """Remove URI credentials and common password fragments from probe errors."""
    sanitized = _URI_AUTHORITY_RE.sub(r"\1[REDACTED]", message)
    sanitized = _PASSWORD_FRAGMENT_RE.sub(r"\1\2[REDACTED]", sanitized)
    if len(sanitized) > _MAX_PROBE_LOG_MESSAGE:
        sanitized = sanitized[: _MAX_PROBE_LOG_MESSAGE - 1] + "…"
    return sanitized


def _format_probe_error(error: Exception) -> str:
    return f"{type(error).__name__}: {_sanitize_probe_message(str(error))}"


async def probe_database() -> None:
    """Raise if the application database cannot answer a trivial query."""
    async with get_engine().connect() as connection:
        await connection.execute(text("SELECT 1"))


async def probe_document_storage() -> None:
    """Raise if the document volume cannot be entered, written, and read."""
    root = Path(settings.document_storage_root)
    root.mkdir(parents=True, exist_ok=True)
    if not os.access(root, os.R_OK | os.W_OK | os.X_OK):
        raise OSError("Document storage volume is not accessible")
    with tempfile.NamedTemporaryFile(dir=root) as probe:
        probe.write(b"ok")
        probe.flush()
        probe.seek(0)
        if probe.read() != b"ok":
            raise OSError("Document storage volume is not readable")


async def is_ready() -> bool:
    """Return whether all dependencies needed to serve requests are ready."""
    logger = logging.getLogger(__name__)
    try:
        await probe_database()
    except Exception as error:
        logger.warning("Readiness database probe failed: %s", _format_probe_error(error))
        return False
    try:
        await probe_document_storage()
    except Exception as error:
        logger.warning(
            "Readiness document storage probe failed: %s", _format_probe_error(error)
        )
        return False
    return True
