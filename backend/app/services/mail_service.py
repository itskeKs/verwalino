"""Explicit SMTP submission. Acceptance is not evidence of recipient delivery."""
from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate

from app.core.config import Settings


class MailConfigurationError(ValueError):
    pass


def validate_address(value: str | None) -> str:
    # Exactly one bare mailbox; never accept header/address-list syntax from contacts.
    if not value or len(value) > 254 or not re.fullmatch(
        r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value
    ):
        raise MailConfigurationError("Eine gültige einzelne E-Mail-Adresse wird benötigt")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in value:
        raise MailConfigurationError("Ungültige E-Mail-Adresse")
    if any(not label or label.startswith("-") or label.endswith("-") for label in domain.split(".")):
        raise MailConfigurationError("Ungültige E-Mail-Adresse")
    return value


def validate_configuration(config: Settings) -> str:
    if not config.smtp_host or not config.smtp_from:
        raise MailConfigurationError("SMTP ist nicht konfiguriert")
    sender = validate_address(config.smtp_from)
    if config.smtp_security not in {"starttls", "tls", "plain"}:
        raise MailConfigurationError("SMTP-Sicherheit muss starttls, tls oder plain sein")
    if config.smtp_security == "plain" and config.smtp_host not in {"localhost", "127.0.0.1", "::1"}:
        raise MailConfigurationError("Unverschlüsseltes SMTP ist nur auf Loopback zulässig")
    if bool(config.smtp_username) != bool(config.smtp_password):
        raise MailConfigurationError("SMTP-Benutzer und Passwort müssen gemeinsam gesetzt werden")
    if config.smtp_security == "plain" and config.smtp_username:
        raise MailConfigurationError("SMTP-Anmeldung benötigt TLS")
    return sender


def send_pdf(config: Settings, *, recipient: str, subject: str, body: str,
             pdf: bytes, filename: str, message_id: str) -> None:
    sender = validate_configuration(config)
    recipient = validate_address(recipient)
    if not pdf.startswith(b"%PDF-"):
        raise MailConfigurationError("Nur ein gerendertes PDF darf versendet werden")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message["Date"] = formatdate(localtime=False)
    message.set_content(body)
    message.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    _submit(config, message, sender, recipient)


def send_signup_confirmation(config: Settings, *, recipient: str, link: str) -> None:
    sender = validate_configuration(config)
    recipient = validate_address(recipient)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = "Verwalino: E-Mail-Adresse bestätigen"
    message["Date"] = formatdate(localtime=False)
    message.set_content(
        "Bitte bestätigen Sie Ihre E-Mail-Adresse, um Ihre Organisation in Verwalino anzulegen.\n\n"
        f"{link}\n\n"
        "Der Link ist einmalig gültig und läuft nach 24 Stunden ab.\n"
        "Geben Sie zur Bestätigung das bei der Registrierung gewählte Passwort ein.\n"
        "Wenn Sie diese Registrierung nicht selbst angefordert haben, ignorieren Sie diese E-Mail.\n"
    )
    _submit(config, message, sender, recipient)


def _submit(config: Settings, message: EmailMessage, sender: str, recipient: str) -> None:
    context = ssl.create_default_context()
    if config.smtp_security == "tls":
        connection = smtplib.SMTP_SSL(config.smtp_host, config.smtp_port,
                                      timeout=config.smtp_timeout_seconds, context=context)
    else:
        connection = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.smtp_timeout_seconds)
    accepted = False
    try:
        with connection as smtp:
            smtp.ehlo()
            if config.smtp_security == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if config.smtp_username:
                smtp.login(config.smtp_username, config.smtp_password.get_secret_value())
            refused = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
            accepted = True
    except (smtplib.SMTPException, OSError):
        # QUIT failure cannot negate the already observed positive DATA reply.
        if not accepted:
            raise


def valid_public_origin(value: str | None) -> bool:
    """One trusted-origin rule for every mailbox link."""
    from urllib.parse import urlsplit
    try:
        url = urlsplit(value or '')
        return bool(url.scheme in {'https', 'http'} and url.hostname and not url.username and not url.password
                    and not url.query and not url.fragment and url.path in {'', '/'}
                    and (url.scheme == 'https' or url.hostname in {'localhost', '127.0.0.1', '::1'}))
    except ValueError:
        return False


def password_reset_available(config: Settings) -> bool:
    """Public capability only: SMTP and a trusted link origin must be usable."""
    try:
        validate_configuration(config)
    except MailConfigurationError:
        return False
    return valid_public_origin(config.public_url)


def _password_message(config: Settings, recipient: str, subject: str, body: str) -> EmailMessage:
    # Pure construction also runs for unknown addresses; validation and SMTP
    # happen only in the post-response submission task.
    message = EmailMessage()
    try:
        sender = validate_address(config.smtp_from)
    except MailConfigurationError:
        sender = ''
    message['From'] = sender
    message['To'] = recipient
    message['Subject'] = subject
    message['Date'] = formatdate(localtime=False)
    message.set_content(body)
    return message


def build_password_reset(config: Settings, *, recipient: str, link: str) -> EmailMessage:
    return _password_message(config, recipient, 'Verwalino: Passwort zurücksetzen',
        'Sie haben einen Link zum Zurücksetzen Ihres Passworts angefordert.\n\n'
        f'{link}\n\n'
        'Der Link ist einmalig gültig und läuft nach 1 Stunde ab.\n'
        'Wenn Sie diese Anfrage nicht selbst gestellt haben, ignorieren Sie diese E-Mail.\n')


def build_password_changed(config: Settings, *, recipient: str) -> EmailMessage:
    return _password_message(config, recipient, 'Verwalino: Passwort geändert',
        'Ihr Passwort wurde geändert. Alle bisherigen Sitzungen wurden beendet.\n'
        'Wenn das nicht Sie waren, fordern Sie sofort einen neuen Link zum Zurücksetzen '
        'Ihres Passworts an und wenden Sie sich an Ihre Verwaltung.\n')


def submit_password_mail(config: Settings, message: EmailMessage) -> None:
    sender = validate_configuration(config)
    recipient = validate_address(str(message['To']))
    _submit(config, message, sender, recipient)
