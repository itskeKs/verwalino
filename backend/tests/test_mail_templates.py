"""Mail contracts. Refresh reviewed snapshots only with UPDATE_MAIL_SNAPSHOTS=1."""

import os
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.mail import RenderedMail, render_mail
from app.mail.contexts import (
    CONTEXT_TYPES, MailLink, OperatorNotice, PasswordReset, SignupWelcome,
)
from app.mail.samples import SAMPLES


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.hrefs = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.hrefs.extend(value for key, value in attrs if key == "href")


@pytest.mark.parametrize("kind", CONTEXT_TYPES)
def test_every_kind_matches_reviewed_snapshots_and_mail_contract(kind):
    assert set(SAMPLES) == set(CONTEXT_TYPES)
    rendered = render_mail(SAMPLES[kind], deployment_mode="hosted")
    assert rendered.text.strip()
    assert rendered.subject.startswith("Verwalino: ")
    assert not any(c in rendered.subject for c in "\r\n")
    for forbidden in ("<img", "<link", "<script", "@import", "url(", "src=", "<svg"):
        assert forbidden not in rendered.html.lower()
    assert all(link.startswith(("https://", "http://localhost", "mailto:"))
               for link in Links(rendered.html).hrefs)
    assert "https://verwalino.de/impressum/" in rendered.html
    assert "https://verwalino.de/datenschutz/" in rendered.html
    for extension, value in (("subject.txt", rendered.subject), ("txt", rendered.text), ("html", rendered.html)):
        path = Path(__file__).with_name("mail_snapshots") / f"{kind}.{extension}"
        if os.environ.get("UPDATE_MAIL_SNAPSHOTS") == "1":
            path.parent.mkdir(exist_ok=True)
            path.write_text(value, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == value


@pytest.mark.parametrize("kind", CONTEXT_TYPES)
def test_selfhost_footer_has_no_hosted_operator_details(kind):
    rendered = render_mail(SAMPLES[kind], deployment_mode="selfhost")
    # Sample action URLs use example.de so this checks the entire message.
    for part in (rendered.text, rendered.html):
        assert "verwalino.de" not in part
        assert "fogu" not in part
        assert "selbst betriebenen Verwalino-Installation" in part


def test_html_escapes_names_but_plain_text_preserves_them():
    value = '<script>alert(1)</script> & "Müller"'
    context = SignupWelcome(admin_name=value, organization_name=value, login_link="https://example.de/login")
    rendered = render_mail(context)
    assert "<script" not in rendered.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &#34;Müller&#34;" in rendered.html
    assert value in rendered.text


@pytest.mark.parametrize("kind", CONTEXT_TYPES)
@pytest.mark.parametrize("control", ["\r", "\n", "\t", "\x00", "\x1f", "\x7f", "\x85", "\u2028"])
def test_all_string_fields_refuse_controls_even_at_edges(kind, control):
    context = SAMPLES[kind]
    for name, value in context.model_dump().items():
        if isinstance(value, str):
            with pytest.raises(ValidationError):
                type(context)(**{**context.model_dump(), name: control + value})
        elif isinstance(value, list):
            with pytest.raises(ValidationError):
                type(context)(**{**context.model_dump(), name: [control + value[0]]})


@pytest.mark.parametrize("link", ["javascript:alert(1)", "http://example.de", "https://user:pass@example.de/x", "//example.de", "https:///x", "https://example.de/" + "x" * 2048])
def test_links_refuse_unsafe_or_oversized_urls(link):
    with pytest.raises(ValidationError):
        PasswordReset(reset_link=link, valid_minutes=60)


@pytest.mark.parametrize("link", ["http://localhost:8000/reset", "http://127.0.0.1/reset", "http://[::1]/reset", "https://example.de/reset"])
def test_links_accept_https_and_loopback_http(link):
    assert PasswordReset(reset_link=link, valid_minutes=60).reset_link == link


@pytest.mark.parametrize("kind", ["signup_confirmation", "email_change_confirm", "password_reset"])
def test_unverified_address_contexts_only_accept_links_and_integer_validity(kind):
    from typing import get_args
    from pydantic import TypeAdapter

    for name, field in CONTEXT_TYPES[kind].model_fields.items():
        if field.annotation is str:
            assert field.metadata == list(get_args(MailLink)[1:])
            with pytest.raises(ValidationError):
                TypeAdapter(field.rebuild_annotation()).validate_python("account data")
        else:
            assert field.annotation is int
    with pytest.raises(ValidationError):
        CONTEXT_TYPES[kind](**SAMPLES[kind].model_dump(), name="Erika")


def test_contexts_are_frozen_strip_strings_and_bound_inputs():
    context = SignupWelcome(admin_name="  Erika  ", organization_name=" Muster ", login_link="https://example.de")
    assert context.admin_name == "Erika"
    assert context.organization_name == "Muster"
    with pytest.raises(ValidationError):
        context.admin_name = "Other"
    with pytest.raises(ValidationError):
        SignupWelcome(admin_name="x" * 201, organization_name="Muster", login_link="https://example.de")
    for paragraphs in ([], ["x"] * 11, ["x" * 2001]):
        with pytest.raises(ValidationError):
            OperatorNotice(title="Hinweis", paragraphs=paragraphs)
    for action in ({"action_label": "Öffnen"}, {"action_link": "https://example.de"}):
        with pytest.raises(ValidationError):
            OperatorNotice(title="Hinweis", paragraphs=["Text"], **action)


@pytest.mark.parametrize("value", [b"Erika\r\nBcc: other@example.de", b"Erika"])
def test_strings_cannot_bypass_validation_through_byte_coercion(value):
    for context, fields in (
        (SignupWelcome, {"admin_name": value, "organization_name": "Muster", "login_link": "https://example.de"}),
        (OperatorNotice, {"title": "Hinweis", "paragraphs": [value]}),
        (PasswordReset, {"reset_link": value, "valid_minutes": 60}),
        (CONTEXT_TYPES["email_change_notice"], {**SAMPLES["email_change_notice"].model_dump(), "new_address": value}),
    ):
        with pytest.raises(ValidationError):
            context(**fields)


def test_dates_sizes_masking_validity_and_optional_copy():
    notice = SAMPLES["email_change_notice"]
    rendered = render_mail(notice, deployment_mode="hosted")
    assert "m•••@example.de" in rendered.text
    assert notice.new_address not in rendered.text + rendered.html
    assert "31.10.2026, 14:05 Uhr" in rendered.text
    for kind in ("email_change_notice", "password_changed", "export_ready", "operator_alert"):
        context = SAMPLES[kind]
        name = next(name for name, value in context.model_dump().items() if isinstance(value, datetime))
        with pytest.raises(ValidationError):
            type(context)(**{**context.model_dump(), name: datetime(2026, 10, 31, 13, 5)})
    assert "1,6 GiB von 2 GiB" in render_mail(SAMPLES["quota_storage_warning"]).text
    assert "25 MiB" in render_mail(SAMPLES["export_ready"]).text
    assert "1 Stunde" in render_mail(SAMPLES["password_reset"]).text
    assert "15 Minuten" in render_mail(PasswordReset(reset_link="https://example.de", valid_minutes=15)).text
    changed = SAMPLES["password_changed"]
    assert "Alle bisherigen Sitzungen wurden beendet." in render_mail(changed).text
    assert "Alle bisherigen Sitzungen" not in render_mail(type(changed)(**{**changed.model_dump(), "sessions_revoked": False})).text
    deleted = SAMPLES["organization_deleted"]
    assert "2036" in render_mail(type(deleted)(**{**deleted.model_dump(), "retention_until": 2036})).text


def test_operator_text_is_escaped_and_optional_action_is_rendered():
    value = '<b>Wartung</b> & "Hinweis"'
    rendered = render_mail(OperatorNotice(title=value, paragraphs=[value], action_label=value, action_link="https://example.de"))
    assert "<b>" not in rendered.html
    assert value in rendered.text
    assert "Falls die Schaltfläche nicht funktioniert" in rendered.html
    assert "Falls die Schaltfläche" not in render_mail(OperatorNotice(title="Hinweis", paragraphs=["Text"])).html


def test_deployment_mode_defaults_to_settings_and_rejects_unknown_modes(monkeypatch):
    monkeypatch.setattr(settings, "deployment_mode", "hosted")
    assert "fogu" in render_mail(SAMPLES["signup_confirmation"]).text
    with pytest.raises(ValueError):
        render_mail(SAMPLES["signup_confirmation"], deployment_mode="other")


def test_long_organization_and_maximum_link_survive_rendering():
    link = "https://example.de/login?token="
    link += "a" * (2048 - len(link))
    rendered = render_mail(SignupWelcome(admin_name="Erika Mustermann", organization_name="M" * 200, login_link=link))
    assert link in rendered.html and link in rendered.text


def test_apply_to_and_fake_smtp_preserve_headers_and_multipart_content():
    # The existing fake lives in a module importing numerous DB seed fixtures;
    # reuse it here without importing that module's fixtures into this namespace.
    from tests.test_mail_service import FakeSMTP

    FakeSMTP.messages = []
    FakeSMTP.calls = []
    FakeSMTP.error = None
    message = EmailMessage(policy=policy.SMTP)
    headers = {"From": "verwaltung@example.de", "To": "erika@example.de", "Date": "Sat, 31 Oct 2026 13:05:00 +0000", "Message-ID": "<sample@example.de>"}
    for name, value in headers.items():
        message[name] = value
    message["Subject"] = "Old subject"
    rendered = render_mail(SAMPLES["signup_confirmation"])
    rendered.apply_to(message)
    assert all(str(message[name]) == value for name, value in headers.items())
    with pytest.raises(FrozenInstanceError):
        rendered.subject = "Other"
    with FakeSMTP("localhost", 1025, timeout=10) as smtp:
        smtp.ehlo()
        assert smtp.send_message(message, from_addr=headers["From"], to_addrs=[headers["To"]]) == {}
    raw, _, _ = FakeSMTP.messages[0]
    assert max(map(len, raw.split(b"\r\n"))) <= 998
    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    assert parsed.get_content_type() == "multipart/alternative"
    assert str(parsed["Subject"]) == rendered.subject
    parts = list(parsed.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_content().replace("\r\n", "\n") == rendered.text
    assert parts[1].get_content().replace("\r\n", "\n") == rendered.html
