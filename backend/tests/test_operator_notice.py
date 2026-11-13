"""Operator sub-processor notice: dry run, send, audit record and idempotency."""
import uuid
from datetime import date, timedelta

import psycopg2
import pytest

from app.core.security import hash_password
from tests.conftest import MIGRATOR_SYNC_URL
from tests.test_mail_service import FakeSMTP

NOTICE = "test-subprocessor-1"
SUBPROCESSOR = "Beispiel Cloud GmbH"
ACTION = "operator.subprocessor_notice.sent"


@pytest.fixture
def notice_org(monkeypatch):
    """One organization with an active OWNER_ADMIN, visible to the command only."""
    FakeSMTP.messages, FakeSMTP.calls, FakeSMTP.error = [], [], None
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", lambda *_a, **_k: pytest.fail("Unexpected TLS transport"))
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO organization (id, name) VALUES (%s, %s)", (str(org_id), "Muster GmbH"))
        cursor.execute('INSERT INTO "user" (id, organization_id, email, password_hash, display_name)'
                       " VALUES (%s, %s, %s, %s, %s)",
                       (str(user_id), str(org_id), "chef@muster.de", hash_password("testpass1234"), "C. Muster"))
        cursor.execute("INSERT INTO membership (id, organization_id, user_id, role) VALUES (%s, %s, %s, 'OWNER_ADMIN')",
                       (str(uuid.uuid4()), str(org_id), str(user_id)))
    yield org_id, "chef@muster.de"
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM audit_event WHERE organization_id=%s", (str(org_id),))
        cursor.execute("DELETE FROM membership WHERE organization_id=%s", (str(org_id),))
        cursor.execute('DELETE FROM "user" WHERE organization_id=%s', (str(org_id),))
        cursor.execute("DELETE FROM organization WHERE id=%s", (str(org_id),))


def audit_rows(org_id):
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT action, payload_diff FROM audit_event WHERE organization_id=%s", (str(org_id),))
        return cursor.fetchall()


@pytest.mark.asyncio
async def test_dry_run_lists_recipients_without_sending_or_recording(notice_org):
    from app import operator_notice
    org_id, email = notice_org
    result = await operator_notice.run(notice_id=NOTICE, subprocessor=SUBPROCESSOR,
                                       effective_date=date.today() + timedelta(days=31), dry_run=True,
                                       organization_ids=[org_id])
    assert result.recipients == [(org_id, email)]
    assert (result.sent, result.skipped) == (0, 0)
    assert FakeSMTP.messages == []
    assert audit_rows(org_id) == []


@pytest.mark.asyncio
async def test_send_delivers_notice_mail_and_records_one_audit_event(notice_org):
    from app import operator_notice
    org_id, email = notice_org
    effective = date.today() + timedelta(days=31)
    result = await operator_notice.run(notice_id=NOTICE, subprocessor=SUBPROCESSOR,
                                       effective_date=effective, organization_ids=[org_id])
    assert result.recipients == [(org_id, email)]
    assert (result.sent, result.skipped) == (1, 0)
    assert len(FakeSMTP.messages) == 1
    raw = FakeSMTP.messages[-1][0].decode()
    assert email in raw and SUBPROCESSOR in raw
    assert "Reply-To: " in raw
    assert effective.strftime("%d.%m.%Y") in raw
    assert (date.today() + timedelta(days=21)).strftime("%d.%m.%Y") in raw
    assert "Hosted-Verwaltino" not in raw
    rows = audit_rows(org_id)
    assert len(rows) == 1
    action, payload = rows[0]
    assert action == ACTION
    assert payload["notice_id"] == NOTICE
    assert payload["subprocessor"] == SUBPROCESSOR
    assert payload["recipient"] == email
    assert payload["effective_date"] == effective.isoformat()
    assert payload["objection_deadline"] == (date.today() + timedelta(days=21)).isoformat()


@pytest.mark.asyncio
async def test_second_run_for_the_same_notice_id_sends_nothing(notice_org):
    from app import operator_notice
    org_id, email = notice_org
    args = {"notice_id": NOTICE, "subprocessor": SUBPROCESSOR,
            "effective_date": date.today() + timedelta(days=31), "organization_ids": [org_id]}
    assert (await operator_notice.run(**args)).sent == 1
    again = await operator_notice.run(**args)
    assert (again.sent, again.skipped) == (0, 1)
    assert again.recipients == [(org_id, email)]
    assert len(FakeSMTP.messages) == 1
    assert len(audit_rows(org_id)) == 1


@pytest.mark.asyncio
async def test_effective_date_less_than_thirty_days_after_sending_is_refused(notice_org):
    from app import operator_notice
    org_id, _ = notice_org
    with pytest.raises(RuntimeError):
        await operator_notice.run(notice_id=NOTICE, subprocessor=SUBPROCESSOR,
                                  effective_date=date.today() + timedelta(days=29),
                                  organization_ids=[org_id])
    assert FakeSMTP.messages == []
    assert audit_rows(org_id) == []


@pytest.fixture
def notice_org_two_admins(notice_org):
    """A second active OWNER_ADMIN in the same organization."""
    second_id, second_email = uuid.uuid4(), f"zweite-{uuid.uuid4().hex}@muster.de"
    org_id, _ = notice_org
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute('INSERT INTO "user" (id, organization_id, email, password_hash, display_name, is_active)'
                       " VALUES (%s, %s, %s, %s, %s, true)",
                       (str(second_id), str(org_id), second_email, hash_password("testpass1234"), "Z. Zweite"))
        cursor.execute("INSERT INTO membership (id, organization_id, user_id, role) VALUES (%s, %s, %s, 'OWNER_ADMIN')",
                       (str(uuid.uuid4()), str(org_id), str(second_id)))
    yield org_id, second_email
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM membership WHERE user_id=%s", (str(second_id),))
        cursor.execute('DELETE FROM "user" WHERE id=%s', (str(second_id),))


@pytest.mark.asyncio
async def test_every_administrator_receives_the_notice_and_a_repeat_sends_nothing(notice_org_two_admins):
    from app import operator_notice
    org_id, second_email = notice_org_two_admins
    args = {"notice_id": NOTICE, "subprocessor": SUBPROCESSOR,
            "effective_date": date.today() + timedelta(days=31), "organization_ids": [org_id]}
    result = await operator_notice.run(**args)
    assert sorted(email for _, email in result.recipients) == sorted(["chef@muster.de", second_email])
    assert (result.sent, result.skipped) == (2, 0)
    assert len(FakeSMTP.messages) == 2
    assert len(audit_rows(org_id)) == 2
    again = await operator_notice.run(**args)
    assert (again.sent, again.skipped) == (0, 2)
    assert len(FakeSMTP.messages) == 2
    assert len(audit_rows(org_id)) == 2
