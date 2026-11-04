"""Agreed residential deposit limit under § 551 (1) BGB."""

from decimal import Decimal


def validate_deposit(usage_type: str, base_rent: Decimal, deposit: Decimal | None) -> None:
    """Reject more than three net monthly rents; advances are excluded."""
    if usage_type != "WOHNUNG" or deposit is None:
        return
    cap = 3 * base_rent
    if deposit > cap:
        amount = format(cap, ",.2f").translate(str.maketrans(",.", ".,"))
        raise ValueError(f"Die Kaution darf höchstens drei Nettokaltmieten betragen ({amount} €).")
