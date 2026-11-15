"""Book a payment and reverse it at a chosen instant (retention anchor tests)."""
from datetime import datetime as real_datetime, timezone

import pytest


@pytest.fixture
def payment_clock(monkeypatch):
    """Return a setter for the instant PaymentService reads as "now" (UTC, ISO text)."""
    from app.services import payment_service

    now = {"at": real_datetime.now(timezone.utc)}

    class Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return now["at"]

    monkeypatch.setattr(payment_service, "datetime", Clock)

    def set_clock(iso: str) -> None:
        now["at"] = real_datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    return set_clock


async def book_then_reverse(client, body, payment_clock, *, booked_on, reversed_on):
    """Create, book and reverse one payment; returns (booked original, Storno) as API bodies."""
    payment_clock(booked_on)
    created = await client.post("/api/payments", json=body)
    assert created.status_code == 201, created.text
    booked = await client.post(f"/api/payments/{created.json()['id']}/book")
    assert booked.status_code == 200, booked.text
    payment_clock(reversed_on)
    reversed_ = await client.post(f"/api/payments/{booked.json()['id']}/reverse")
    assert reversed_.status_code == 201, reversed_.text
    return booked.json(), reversed_.json()
