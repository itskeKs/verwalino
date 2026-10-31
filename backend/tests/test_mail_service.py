"""T47: explicit SMTP transport and retained, authorized document attempts."""
import asyncio
import uuid
from email import policy
from email.parser import BytesParser

import pytest
from sqlalchemy import text

from app.core.config import Settings, settings
from tests.test_m7_dunning import m7_seed  # noqa: F401
from tests.test_scoped_account_statements import mutate
from tests.test_m3_settlement import m3_seed, _create_run, _extras_for_exit  # noqa: F401
from tests.test_m8_meetings import m8_seed, invitation_data  # noqa: F401
from tests.test_financial_scope_boundary import worker_tenant, scope_seed  # noqa: F401
from tests.test_scoped_account_statements import statement_fixture, client as scoped_client
from tests.test_scoped_dunning import grant, tenant_body


@pytest.fixture(autouse=True)
def isolate_smtp_configuration(monkeypatch):
    for name, field in Settings.model_fields.items():
        if name.startswith("smtp_") or name == "mail_allowlist":
            monkeypatch.delenv(f"HV_{name.upper()}", raising=False)
            monkeypatch.setattr(settings, name, field.default)

    def forbid_transport(*args, **kwargs):
        raise AssertionError("SMTP tests must install an explicit fake transport")

    monkeypatch.setattr("smtplib.SMTP", forbid_transport)
    monkeypatch.setattr("smtplib.SMTP_SSL", forbid_transport)


class FakeSMTP:
    messages = []
    calls = []
    error = None

    def __init__(self, host, port, *, timeout, context=None):
        self.calls.append(("connect", host, port, timeout))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        self.calls.append(("ehlo",))

    def starttls(self, *, context):
        self.calls.append(("starttls",))

    def login(self, username, password):
        self.calls.append(("login", username, password))

    def send_message(self, message, *, from_addr, to_addrs):
        self.calls.append(("send",))
        if self.error:
            raise self.error
        self.messages.append((message.as_bytes(), from_addr, to_addrs))
        return {}


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.messages = []
    FakeSMTP.calls = []
    FakeSMTP.error = None
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(settings, "smtp_port", 1025, raising=False)
    monkeypatch.setattr(settings, "smtp_from", "verwaltung@example.invalid", raising=False)
    monkeypatch.setattr(settings, "smtp_security", "plain", raising=False)
    return FakeSMTP


def test_mail_transport_disabled_without_configuration():
    from app.services.mail_service import send_pdf, MailConfigurationError
    with pytest.raises(MailConfigurationError):
        send_pdf(Settings(_env_file=None), recipient="tenant@example.invalid", subject="Mahnung",
                 body="Anbei.", pdf=b"%PDF-1.7 synthetic", filename="mahnung.pdf", message_id="<test@example.invalid>")


def test_mail_transport_exact_pdf_tls_auth_and_no_delivery_claim(smtp):
    from app.services.mail_service import send_pdf
    config = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_port=1025,
                      smtp_from="verwaltung@example.invalid", smtp_security="starttls",
                      smtp_username="synthetic", smtp_password="fake-only")
    pdf = b"%PDF-1.7 synthetic retained document"
    send_pdf(config, recipient="tenant@example.invalid", subject="Mahnung", body="Anbei Ihre Mahnung.",
             pdf=pdf, filename="mahnung.pdf", message_id="<test@example.invalid>")
    raw, sender, recipients = smtp.messages[0]
    message = BytesParser(policy=policy.default).parsebytes(raw)
    assert sender == "verwaltung@example.invalid" and recipients == ["tenant@example.invalid"]
    assert message["Message-ID"] == "<test@example.invalid>"
    attachment, = list(message.iter_attachments())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True) == pdf
    assert attachment.get_filename() == "mahnung.pdf"
    assert [call[0] for call in smtp.calls] == ["connect", "ehlo", "starttls", "ehlo", "login", "send"]
    assert ("login", "synthetic", "fake-only") in smtp.calls


@pytest.fixture(params=["send_pdf", "send_signup_confirmation", "submit_password_mail"])
def mail_sender(request):
    from app.services import mail_service
    kwargs = (dict(subject="Mahnung", body="Anbei", pdf=b"%PDF-x", filename="x.pdf",
                   message_id="<x@example.invalid>") if request.param == "send_pdf"
              else dict(link="https://staging.example.invalid/signup/confirm?token=synthetic"))

    def send(config, recipient):
        if request.param == "submit_password_mail":
            message = mail_service.build_password_reset(config, recipient=recipient, link=kwargs["link"])
            mail_service.submit_password_mail(config, message)
            return
        getattr(mail_service, request.param)(config, recipient=recipient, **kwargs)

    return send


@pytest.mark.parametrize("security", ["plain", "tls"])
@pytest.mark.parametrize("recipient,allowed", [
    ("canary@example.invalid", True),
    ("CANARY@example.invalid", False),
    ("tenant@example.invalid", False),
])
def test_mail_allowlist_restricts_submission_before_connection(smtp, caplog, mail_sender, security, recipient, allowed):
    from app.services.mail_service import MailConfigurationError
    config = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_from="v@example.invalid",
                      smtp_security=security, mail_allowlist="canary@example.invalid")
    if allowed:
        mail_sender(config, recipient)
        assert len(smtp.messages) == 1
        assert smtp.messages[0][2] == [recipient]
    else:
        with pytest.raises(MailConfigurationError, match="Empfänger"):
            mail_sender(config, recipient)
        assert not smtp.calls and not smtp.messages
        assert "WARNING" in caplog.text
        assert recipient not in caplog.text


@pytest.mark.parametrize("allowlist", ["", "bad", "a@example.invalid,b@example.invalid"])
def test_invalid_mail_allowlist_fails_closed(smtp, mail_sender, caplog, allowlist):
    from app.services.mail_service import MailConfigurationError
    config = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_from="v@example.invalid",
                      smtp_security="plain", mail_allowlist=allowlist)
    with pytest.raises(MailConfigurationError):
        mail_sender(config, "canary@example.invalid")
    assert not smtp.calls and not smtp.messages
    assert "WARNING" in caplog.text
    assert "canary@example.invalid" not in caplog.text
    if allowlist:
        assert allowlist not in caplog.text


def test_submit_enforces_allowlist_for_any_sender_before_connection(smtp):
    from email.message import EmailMessage
    from app.services.mail_service import _submit, MailConfigurationError
    config = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_from="v@example.invalid",
                      smtp_security="plain", mail_allowlist="canary@example.invalid")
    with pytest.raises(MailConfigurationError):
        _submit(config, EmailMessage(), config.smtp_from, "tenant@example.invalid")
    assert not smtp.calls and not smtp.messages


@pytest.mark.parametrize("recipient", ["", "bad", "a@example.invalid\r\nBcc: x@example.invalid", "a@example.invalid,b@example.invalid"])
def test_mail_transport_rejects_invalid_recipient_before_connection(smtp, recipient):
    from app.services.mail_service import send_pdf, MailConfigurationError
    with pytest.raises(MailConfigurationError):
        send_pdf(Settings(_env_file=None, smtp_host="127.0.0.1", smtp_from="v@example.invalid", smtp_security="plain"),
                 recipient=recipient, subject="Mahnung", body="Anbei", pdf=b"%PDF-x", filename="x.pdf", message_id="<x@example.invalid>")
    assert not smtp.calls


async def issued_notice(api, seed):
    response = await api.post("/api/dunning/tenant", json={
        "tenant_profile_id": str(seed["tenant_m7"]), "lease_agreement_id": str(seed["lease_m7"]),
        "period_start": "2025-01-01", "period_end": "2025-06-30", "reference_date": "2026-01-15",
    })
    assert response.status_code == 201, response.text
    result = await api.post(f"/api/dunning/{response.json()['id']}/issue")
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
async def test_mail_allowlist_denial_returns_a_failed_receipt_without_smtp(client_a_admin, m7_seed, smtp, monkeypatch):
    monkeypatch.setattr(settings, "mail_allowlist", "canary@example.invalid")
    await mutate(text("UPDATE person SET email='tenant@example.invalid' WHERE id=:person"), {"person": m7_seed["person_m7"]})
    notice = await issued_notice(client_a_admin, m7_seed)
    url = f"/api/mail/dunning/{notice['id']}"
    response = await client_a_admin.post(url, json={"person_id": str(m7_seed["person_m7"]), "request_id": str(uuid.uuid4())})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "failed"
    assert not smtp.calls and not smtp.messages
    assert (await client_a_admin.get(url)).json()["attempts"] == [response.json()]


@pytest.mark.asyncio
async def test_mail_dunning_send_persists_attempt_idempotency_and_original(client_a_admin, m7_seed, migrator_session, smtp):
    await mutate(text("UPDATE person SET email='tenant@example.invalid' WHERE id=:person"), {"person": m7_seed["person_m7"]})
    notice = await issued_notice(client_a_admin, m7_seed)
    url = f"/api/mail/dunning/{notice['id']}"
    info = await client_a_admin.get(url)
    assert info.status_code == 200, info.text
    assert info.json()["recipients"] == [{"person_id": str(m7_seed["person_m7"]), "name": "Max Mahner", "email": "tenant@example.invalid"}]
    body = {"person_id": str(m7_seed["person_m7"]), "request_id": str(uuid.uuid4())}
    sent = await client_a_admin.post(url, json=body)
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "accepted"
    repeated = await client_a_admin.post(url, json=body)
    assert repeated.json() == sent.json()
    assert len(smtp.messages) == 1
    message = BytesParser(policy=policy.default).parsebytes(smtp.messages[0][0])
    assert list(message.iter_attachments())[0].get_payload(decode=True).startswith(b"%PDF")
    history = (await client_a_admin.get(url)).json()["attempts"]
    assert history == [sent.json()]
    retained = await client_a_admin.get(f"{url}/{body['request_id']}/pdf")
    assert retained.status_code == 200, retained.text
    assert retained.headers["content-type"] == "application/pdf"
    assert retained.content == list(message.iter_attachments())[0].get_payload(decode=True)
    assert (await client_a_admin.get(f"{url}/{uuid.uuid4()}/pdf")).status_code == 404
    assert (await client_a_admin.get(f"/api/dunning/{notice['id']}")).json() == notice


@pytest.mark.asyncio
async def test_mail_dunning_failures_and_recipient_guard(client_a_admin, client_a_readonly, client_b_admin, m7_seed, migrator_session, smtp):
    await mutate(text("UPDATE person SET email=NULL WHERE id=:person"), {"person": m7_seed["person_m7"]})
    notice = await issued_notice(client_a_admin, m7_seed)
    url = f"/api/mail/dunning/{notice['id']}"
    body = {"person_id": str(m7_seed["person_m7"]), "request_id": str(uuid.uuid4())}
    assert (await client_a_admin.post(url, json=body)).status_code == 422
    assert (await client_a_readonly.post(url, json=body)).status_code == 403
    assert (await client_b_admin.get(url)).status_code == 404
    assert (await client_a_admin.post(url, json={**body, "person_id": str(uuid.uuid4())})).status_code == 404
    assert not smtp.messages
    await mutate(text("UPDATE person SET email='tenant@example.invalid' WHERE id=:person"), {"person": m7_seed["person_m7"]})
    import smtplib
    smtp.error = smtplib.SMTPRecipientsRefused({"tenant@example.invalid": (550, b"refused")})
    failed = await client_a_admin.post(url, json=body)
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert (await client_a_admin.get(url)).json()["attempts"][-1]["status"] == "failed"
    smtp.error = TimeoutError("uncertain after DATA")
    unknown = await client_a_admin.post(url, json={**body, "request_id": str(uuid.uuid4())})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json()["status"] == "unknown"
    assert not smtp.messages


@pytest.mark.asyncio
async def test_mail_parallel_replay_sends_once(client_a_admin, m7_seed, smtp):
    await mutate(text("UPDATE person SET email='tenant@example.invalid' WHERE id=:person"), {"person": m7_seed["person_m7"]})
    notice = await issued_notice(client_a_admin, m7_seed)
    url = f"/api/mail/dunning/{notice['id']}"
    body = {"person_id": str(m7_seed["person_m7"]), "request_id": str(uuid.uuid4())}
    responses = await asyncio.gather(*(client_a_admin.post(url, json=body) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert responses[0].json() == responses[1].json()
    assert len(smtp.messages) == 1


@pytest.mark.asyncio
async def test_mail_retains_prepared_when_completion_commit_fails(client_a_admin, m7_seed, smtp, monkeypatch):
    from app.services.document_mail_service import DocumentMailService
    await mutate(text("UPDATE person SET email='tenant@example.invalid' WHERE id=:person"), {"person": m7_seed["person_m7"]})
    notice = await issued_notice(client_a_admin, m7_seed)
    url = f"/api/mail/dunning/{notice['id']}"
    body = {"person_id": str(m7_seed["person_m7"]), "request_id": str(uuid.uuid4())}
    original = DocumentMailService.record

    async def fail_completion(self, kind, document_id, action, payload):
        if action == "mail_completed":
            raise RuntimeError("synthetic commit outage after SMTP acceptance")
        return await original(self, kind, document_id, action, payload)

    monkeypatch.setattr(DocumentMailService, "record", fail_completion)
    with pytest.raises(RuntimeError, match="synthetic commit outage"):
        await client_a_admin.post(url, json=body)
    assert len(smtp.messages) == 1
    retained = await client_a_admin.get(url)
    assert retained.status_code == 200, retained.text
    assert retained.json()["attempts"][0]["status"] == "prepared"
    repeated = await client_a_admin.post(url, json=body)
    assert repeated.status_code == 200 and repeated.json()["status"] == "prepared"
    assert len(smtp.messages) == 1


@pytest.mark.asyncio
async def test_mail_finalized_settlement_uses_original_snapshot(client_a_admin, m3_seed, smtp):
    run_id = await _create_run(client_a_admin, m3_seed, "lease_a", "600.00")
    url = f"/api/mail/settlement/{run_id}"
    assert (await client_a_admin.get(url)).status_code == 409
    finalized = await client_a_admin.post(f"/api/settlements/{run_id}/finalize", json=_extras_for_exit(m3_seed))
    assert finalized.status_code == 200, finalized.text
    info = await client_a_admin.get(url)
    assert info.status_code == 200, info.text
    recipient, = info.json()["recipients"]
    assert recipient["person_id"]
    assert (await client_a_admin.put(f"/api/persons/{recipient['person_id']}", json={"email": "settlement@example.invalid"})).status_code == 200
    original_pdf = await client_a_admin.get(f"/api/settlements/{run_id}/pdf")
    assert original_pdf.status_code == 200 and original_pdf.content.startswith(b"%PDF")
    import psycopg2
    from tests.conftest import MIGRATOR_SYNC_URL
    with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute('SELECT u.id,u.area FROM unit u JOIN lease_agreement l ON l.unit_id=u.id WHERE l.id=%s', (str(m3_seed["lease_a"]),))
            unit_id, original_area = cursor.fetchone()
    assert original_area is not None
    request_id = str(uuid.uuid4())
    try:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE unit SET area=area+100 WHERE id=%s RETURNING area', (str(unit_id),))
                assert cursor.fetchone()[0] != original_area
        sent = await client_a_admin.post(url, json={"person_id": recipient["person_id"], "request_id": request_id})
        assert sent.status_code == 200 and sent.json()["status"] == "accepted", sent.text
        message = BytesParser(policy=policy.default).parsebytes(smtp.messages[0][0])
        attachment = list(message.iter_attachments())[0].get_payload(decode=True)
        assert attachment == original_pdf.content
        retained = await client_a_admin.get(f"{url}/{sent.json()['id']}/pdf")
        assert retained.status_code == 200 and retained.content == attachment
    finally:
        with psycopg2.connect(MIGRATOR_SYNC_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute('UPDATE unit SET area=%s WHERE id=%s', (original_area, str(unit_id)))
    assert message["To"] == "settlement@example.invalid"
    from io import BytesIO
    import pdfplumber
    with pdfplumber.open(BytesIO(list(message.iter_attachments())[0].get_payload(decode=True))) as pdf:
        content = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Grundsteuer" in content and "Abrechnung" in content
    assert (await client_a_admin.get(f"/api/settlements/{run_id}")).json() == finalized.json()


@pytest.mark.asyncio
async def test_mail_invitation_single_selected_current_owner_page(client_a_admin, invitation_data, smtp):
    data = invitation_data
    person_id = data["people"][0]["id"]
    assert (await client_a_admin.put(f"/api/persons/{person_id}", json={"email": "anna@example.invalid"})).status_code == 200
    url = f"/api/mail/invitation/{data['meeting']['id']}"
    info = await client_a_admin.get(url)
    assert info.status_code == 200, info.text
    assert {p["person_id"] for p in info.json()["recipients"]} == {p["id"] for p in data["people"][:2]}
    sent = await client_a_admin.post(url, json={"person_id": person_id, "request_id": str(uuid.uuid4())})
    assert sent.status_code == 200 and sent.json()["status"] == "accepted", sent.text
    from io import BytesIO
    import pdfplumber
    message = BytesParser(policy=policy.default).parsebytes(smtp.messages[0][0])
    with pdfplumber.open(BytesIO(list(message.iter_attachments())[0].get_payload(decode=True))) as pdf:
        assert len(pdf.pages) == 1
        content = pdf.pages[0].extract_text()
    assert "Anna" in content and "Annaweg 4" in content and "Eröffnung" in content
    assert "Bertastr. 7" not in content and "Altweg" not in content and "Neuweg" not in content
    assert (await client_a_admin.get(f"/api/meetings/{data['meeting']['id']}")).json() == data["meeting"]


def test_mail_positive_data_reply_survives_quit_error(smtp, monkeypatch):
    import smtplib
    from app.services.mail_service import send_pdf

    def failed_quit(self, *args):
        raise smtplib.SMTPResponseException(500, b"QUIT failed after accepted DATA")

    monkeypatch.setattr(FakeSMTP, "__exit__", failed_quit)
    config = Settings(_env_file=None, smtp_host="127.0.0.1", smtp_security="plain", smtp_from="v@example.invalid")
    send_pdf(config, recipient="p@example.invalid", subject="Dokument", body="Anbei", pdf=b"%PDF-x",
             filename="x.pdf", message_id="<x@example.invalid>")
    assert len(smtp.messages) == 1


@pytest.mark.parametrize("security,host,username,password", [
    ("plain", "mail.example.invalid", None, None), ("plain", "127.0.0.1", "u", "p"),
    ("starttls", "127.0.0.1", "u", None), ("invalid", "127.0.0.1", None, None),
])
def test_mail_configuration_never_silently_sends_insecurely(smtp, security, host, username, password):
    from app.services.mail_service import send_pdf, MailConfigurationError
    config = Settings(_env_file=None, smtp_host=host, smtp_security=security, smtp_from="v@example.invalid",
                      smtp_username=username, smtp_password=password)
    with pytest.raises(MailConfigurationError):
        send_pdf(config, recipient="p@example.invalid", subject="Dokument", body="Anbei", pdf=b"%PDF-x",
                 filename="x.pdf", message_id="<x@example.invalid>")
    assert not smtp.messages and not smtp.calls


@pytest.mark.asyncio
async def test_mail_scoped_export_is_not_send_and_revocation_is_live(migrator_session, scope_seed, smtp):
    ids = await statement_fixture(migrator_session, scope_seed, "lease")
    await mutate(text("UPDATE person SET email='scoped@example.invalid' WHERE id=:person"), ids)
    await grant(ids, ["read", "prepare", "approve", "export"])
    async with scoped_client(ids) as api:
        created = await api.post("/api/dunning/tenant", json=tenant_body(ids))
        assert created.status_code == 201, created.text
        notice_id = created.json()["id"]
        assert (await api.post(f"/api/dunning/{notice_id}/issue")).status_code == 200
        url = f"/api/mail/dunning/{notice_id}"
        assert (await api.get(url)).status_code == 200
        body = {"person_id": str(ids["person"]), "request_id": str(uuid.uuid4())}
        denied = await api.post(url, json=body)
        assert denied.status_code in {403, 404}, denied.text
        assert not smtp.messages
        await grant(ids, ["read", "export", "send"])
        sent = await api.post(url, json=body)
        assert sent.status_code == 200 and sent.json()["status"] == "accepted", sent.text
        assert len(smtp.messages) == 1
        await grant(ids, ["read"])
        assert (await api.get(url)).status_code == 404
        assert (await api.post(url, json={**body, "request_id": str(uuid.uuid4())})).status_code == 404
        assert len(smtp.messages) == 1


@pytest.mark.asyncio
async def test_mail_historical_invitation_keeps_original_contact_export_boundary(client_a_admin, invitation_data, m8_seed, smtp):
    from datetime import date, timedelta
    from tests.test_property_source_access import grant as property_grant
    from tests.test_person_source_access import audience
    from tests.test_property_source_enforcement import synthetic_enforcement
    from tests.test_financial_source_disclosure import clear_disclosure_grants
    data = invitation_data
    original_person = data["people"][0]["id"]
    assert (await client_a_admin.put(f"/api/persons/{original_person}", json={"email": "former@example.invalid"})).status_code == 200
    url = f"/api/mail/invitation/{data['meeting']['id']}"
    sent = await client_a_admin.post(url, json={"person_id": original_person, "request_id": str(uuid.uuid4())})
    assert sent.status_code == 200 and sent.json()["status"] == "accepted", sent.text
    # End both former ownerships before the meeting, without changing retained mail.
    end = (date.fromisoformat(data["meeting"]["meeting_date"]) - timedelta(days=1)).isoformat()
    for unit in data["units"]:
        histories = await client_a_admin.get("/api/ownership", params={"unit_id": unit["id"]})
        assert histories.status_code == 200, histories.text
        for row in histories.json():
            if row["owner_profile_id"] == data["owners"][0]["id"]:
                response = await client_a_admin.patch(f"/api/ownership/{row['id']}", json={"valid_to": end})
                assert response.status_code == 200, response.text
    try:
        await property_grant(client_a_admin, m8_seed, uuid.UUID(data["property"]["id"]), caps=["read", "export"])
        for person in data["people"]:
            caps = ["read"] if person["id"] == original_person else ["read", "export"]
            await audience(client_a_admin, person["id"], m8_seed["user_a_admin_id"], caps)
        async with synthetic_enforcement(m8_seed):
            # Current invitation itself is accessible; only retained former-contact mail is denied.
            current = await client_a_admin.get(f"/api/meetings/{data['meeting']['id']}/invitation.pdf")
            assert current.status_code == 200, current.text
            assert (await client_a_admin.get(url)).status_code == 404
            assert (await client_a_admin.get(f"{url}/{sent.json()['id']}/pdf")).status_code == 404
        assert len(smtp.messages) == 1
    finally:
        await clear_disclosure_grants()
