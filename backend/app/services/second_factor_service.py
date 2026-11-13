"""TOTP second factor for staff accounts (issue #208).

RFC 6238 codes (30-second step, 6 digits, tolerance one step each way,
replay protection per time step). The authenticator secret is stored
encrypted (Fernet, the existing secret handling of ai_settings_service with
its own domain string); recovery codes are stored only as HMAC-SHA256
digests keyed with the deployment secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid

from cryptography.fernet import Fernet

from app.core.config import settings

TOTP_STEP_SECONDS = 30
TOTP_DIGITS = 6
TOTP_WINDOW_STEPS = 1
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 8
# Unambiguous alphabet: no 0/O, 1/I/L.
RECOVERY_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"

PENDING_TOKEN_MAX_AGE_SECONDS = 300


def _cipher(organization_id: uuid.UUID) -> Fernet:
    # Domain and tenant separation bind ciphertext to this deployment and tenant.
    material = f"verwalino:user-totp:v1:{organization_id}:{settings.session_secret}".encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material).digest()))


def encrypt_secret(organization_id: uuid.UUID, secret: str) -> str:
    return _cipher(organization_id).encrypt(secret.encode()).decode()


def decrypt_secret(organization_id: uuid.UUID, ciphertext: str) -> str:
    return _cipher(organization_id).decrypt(ciphertext.encode()).decode()


def generate_secret() -> str:
    """A fresh base32 authenticator secret (160 random bits, no padding)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def otpauth_uri(email: str, organization_name: str, secret: str) -> str:
    from urllib.parse import quote

    label = quote(f"verwalino:{email}")
    issuer = quote(organization_name)
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={issuer}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP_SECONDS}"
    )


def totp_at(secret: str, timestamp: float) -> str:
    """The RFC 6238 code of the secret at the given Unix timestamp."""
    counter = int(timestamp // TOTP_STEP_SECONDS)
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{code % 10 ** TOTP_DIGITS:0{TOTP_DIGITS}d}"


def current_totp(secret: str) -> str:
    return totp_at(secret, time.time())


def verify_totp(
    secret: str, code: str, timestamp: float, last_step: int | None
) -> tuple[bool, int | None]:
    """Check the code within +/- one step; refuse an already used step.

    Returns (accepted, used_step); used_step is None when refused. Callers
    persist used_step as last_totp_step for replay protection.
    """
    if not code or not code.isdigit() or len(code) != TOTP_DIGITS:
        return False, None
    step = int(timestamp // TOTP_STEP_SECONDS)
    for candidate in range(step - TOTP_WINDOW_STEPS, step + TOTP_WINDOW_STEPS + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(totp_at(secret, candidate * TOTP_STEP_SECONDS), code):
            return True, candidate
    return False, None


def recovery_code_hash(organization_id: uuid.UUID, code: str) -> str:
    material = f"verwalino:recovery-code:v1:{organization_id}:{settings.session_secret}".encode()
    return hmac.new(hashlib.sha256(material).digest(), code.encode(), hashlib.sha256).hexdigest()


def generate_recovery_codes() -> list[str]:
    """Ten distinct printable one-time codes, shown to the user once."""
    codes = set()
    while len(codes) < RECOVERY_CODE_COUNT:
        raw = "".join(secrets.choice(RECOVERY_CODE_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        codes.add(f"{raw[:4]}-{raw[4:]}")
    return sorted(codes)
