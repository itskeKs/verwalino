"""Canonical JSON serialization + SHA-256 for the snapshot (section 1.8).

The snapshot_sha256 must be reproducible to be useful as tamper evidence, so
the JSON serialization is CANONICAL:

  - keys sorted; compact separators (no whitespace drift); UTF-8.
  - money/quotas are STRINGS (e.g. "300.00", "210.500000"), NEVER JSON numbers
    (float drift); the caller emits already-formatted strings via Decimal.
  - dates as ISO strings (YYYY-MM-DD).
  - list order is the caller's responsibility (stable, sorted by id).

Determinism test (section 9, I-E): finalizing identical inputs twice yields a
bit-identical canonical payload and identical hash (ADR-0005 guarantees the
deterministic rounding that makes this possible).

This module is pure and reusable for M4 and the optional audit hash-chain.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal


def _default(obj: object) -> object:
    # Decimal must never reach here as a JSON number -- callers stringify money
    # explicitly. If a Decimal slips through, stringify it (defensive) so we
    # never emit a float. UUID/date are stringified by the caller too.
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not canonical-serializable")


def canonical_json(payload: dict) -> bytes:
    """Serialize `payload` to canonical UTF-8 JSON bytes.

    sort_keys + compact separators => stable byte output for identical content.
    ensure_ascii=False keeps UTF-8 (German umlauts) stable and compact.
    """
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
    )
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of bytes (lower-case, 64 chars)."""
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(payload: dict) -> str:
    """Convenience: canonical_json -> sha256 hex."""
    return sha256_hex(canonical_json(payload))
