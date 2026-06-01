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
from argon2.exceptions import VerifyMismatchError
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.core.config import settings

# ---------------------------------------------------------------------------
# Password hashing -- Argon2id (architecture.md section 4.3)
# ---------------------------------------------------------------------------
_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    return _ph.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, plain)
    except VerifyMismatchError:
        return False


def password_needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


# ---------------------------------------------------------------------------
# Server-side session serialization (signed cookie)
# ---------------------------------------------------------------------------
_serializer = URLSafeTimedSerializer(settings.session_secret)
_SALT = "hv-session"


def create_session_token(
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    role: str,
) -> str:
    """Return a signed session token (cookie value)."""
    payload: dict[str, Any] = {
        "uid": str(user_id),
        "oid": str(organization_id),
        "role": role,
    }
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
        if not all(k in data for k in ("uid", "oid", "role")):
            return None
        return data
    except (BadSignature, SignatureExpired):
        return None
