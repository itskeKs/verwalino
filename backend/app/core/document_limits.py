"""Shared file-content safety cap, independent of deployment and plan selection."""

from decimal import Decimal, ROUND_HALF_UP

MAX_UPLOAD_BYTES = 25 * 1024**2


def format_upload_limit(value: int) -> str:
    """Match the frontend's German MiB/GiB display, including half-up rounding."""
    unit, divisor = ("GiB", 1024**3) if value >= 1024**3 else ("MiB", 1024**2)
    amount = (Decimal(value) / divisor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    number = f"{amount:,.2f}".rstrip("0").rstrip(".").translate(str.maketrans(",.", ".,"))
    return f"{number} {unit}"
