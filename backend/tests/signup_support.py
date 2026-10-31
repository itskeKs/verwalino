"""Captured signup mail shared by API tests and hosted scenarios."""
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import parse_qs, urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from tests.setup_support import fresh_setup  # noqa: F401
from tests.test_mail_service import FakeSMTP

BODY = {"organization_name": "Hausverwaltung mit langem Namen GmbH", "admin_name": "Erika Mustermann",
        "email": " Signup@Example.test ", "password": "A-long-signup-password-123", "website": ""}
MESSAGE = {"message": "Wenn die Adresse frei ist, haben wir eine E-Mail geschickt"}
REFUSED = {"detail": "Bestätigung nicht möglich. Bitte fordern Sie einen neuen Link an."}


@pytest.fixture
def signup_mail(monkeypatch):
    FakeSMTP.messages, FakeSMTP.calls, FakeSMTP.error = [], [], None
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *_a, **_k: pytest.fail("Unexpected TLS transport"))
    for name, value in {"public_host": "signup.example.test", "smtp_host": "127.0.0.1",
                        "smtp_port": 1025, "smtp_from": "verwaltung@example.invalid", "smtp_security": "plain",
                        "smtp_username": None, "smtp_password": None}.items():
        monkeypatch.setattr(settings, name, value, raising=False)
    return FakeSMTP


class SignupInspection:
    """Owner-only observations; HTTP sessions keep the restricted runtime role."""

    def __init__(self, connection):
        self.connection = connection

    async def execute(self, statement, parameters=None):
        await self.connection.execute(text('RESET ROLE'))
        try:
            return await self.connection.execute(statement, parameters or {})
        finally:
            await self.connection.execute(text('SET LOCAL ROLE app_rw'))

    async def scalar(self, statement, parameters=None):
        return (await self.execute(statement, parameters)).scalar()


@pytest_asyncio.fixture
async def signup(fresh_setup, signup_mail, monkeypatch):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    client, connection = fresh_setup
    inspection = SignupInspection(connection)
    await inspection.execute(text("DELETE FROM public.signup_request"))
    return client, inspection, signup_mail


def confirmation_token(mail):
    message = BytesParser(policy=policy.default).parsebytes(mail.messages[-1][0])
    assert message["Subject"] == "Verwalino: E-Mail-Adresse bestätigen"
    body = message.get_content()
    assert "24 Stunden" in body
    link = re.search(r"https://signup.example.test/signup/confirm\?token=\S+", body)[0]
    return parse_qs(urlsplit(link).query)["token"][0]
