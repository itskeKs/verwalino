"""Password hashing (Argon2id) and server-side session management.

Session data is a signed JSON blob stored in an HTTP-only, Secure,
SameSite=lax cookie.  The server is the single source of truth for
`organization_id` and `user_id` -- never derived from client input.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing -- Argon2id (architecture.md section 4.3)
# ---------------------------------------------------------------------------
_ph = PasswordHasher()
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MAX_EMAIL_LENGTH = 320


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def password_needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


def validate_new_password(password: str) -> str:
    """Reject only newly set passwords outside the supported policy."""
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise ValueError(
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters"
        )
    return password


def normalize_email(value: object) -> str:
    """Return the one accepted email representation for auth and persistence."""
    if not isinstance(value, str):
        raise ValueError("Email must be a string")
    normalized = value.strip().lower()
    if not 1 <= len(normalized) <= MAX_EMAIL_LENGTH:
        raise ValueError(f"Email must be between 1 and {MAX_EMAIL_LENGTH} characters")
    return normalized


# ---------------------------------------------------------------------------
# Server-side session serialization (signed cookie)
# ---------------------------------------------------------------------------
_serializer = URLSafeTimedSerializer(settings.session_secret)
_SALT = "hv-session"


def create_session_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    session_version: int = 0,
) -> str:
    """Return a signed session token (cookie value)."""
    payload: dict[str, Any] = {
        "uid": str(user_id),
        "oid": str(organization_id),
    }
    if type(session_version) is not int or session_version < 0:
        raise ValueError("Invalid session generation")
    if session_version:
        payload["sv"] = session_version
    return _serializer.dumps(payload, salt=_SALT)


def decode_session_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a session token.  Returns None on failure."""
    try:
        data = _serializer.loads(
            token,
            salt=_SALT,
            max_age=settings.session_max_age_seconds,
        )
        # Validate expected keys
        if not isinstance(data, dict) or set(data) not in ({"uid", "oid"}, {"uid", "oid", "sv"}):
            return None
        generation = data.get("sv", 0)
        if type(generation) is not int or generation < 0:
            return None
        user_id = data["uid"]
        organization_id = data["oid"]
        if not isinstance(user_id, str) or not isinstance(organization_id, str):
            return None
        uuid.UUID(user_id)
        uuid.UUID(organization_id)
        return data
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError, AttributeError):
        return None
