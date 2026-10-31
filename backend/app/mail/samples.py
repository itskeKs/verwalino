"""Synthetic examples shared by snapshots and the database-free preview gallery."""

from datetime import date, datetime, timezone

from app.mail.contexts import (
    AiBudgetExhausted, EmailChangeConfirm, EmailChangeNotice, ExportReady,
    OperatorAlert, OperatorNotice, OrganizationDeleted, OrganizationDeletionScheduled,
    PasswordChanged, PasswordReset, QuotaStorageFull, QuotaStorageWarning,
    SignupConfirmation, SignupWelcome, UserInvitation,
)

ORGANIZATION = "Hausverwaltung Muster GmbH"
LONG_ORGANIZATION = (
    "Hausverwaltung Muster GmbH – Verwaltung von Wohn- und Gewerbeimmobilien "
    "für die Eigentümergemeinschaften Am Sonnenhang, Berliner Straße und Im Großenbruch"
)
PERSON = "Erika Mustermann"
NOW = datetime(2026, 10, 31, 13, 5, tzinfo=timezone.utc)
ORIGIN = "https://verwaltung.example.de"
TOKEN = "synthetischer-vorschau-token-0123456789abcdef0123456789abcdef"

SAMPLES = {context.kind: context for context in (
    SignupConfirmation(confirm_link=f"{ORIGIN}/signup/confirm?token={TOKEN}", valid_hours=24),
    SignupWelcome(admin_name=PERSON, organization_name=ORGANIZATION, login_link=f"{ORIGIN}/login"),
    UserInvitation(inviter_name=PERSON, organization_name=LONG_ORGANIZATION,
                   set_password_link=f"{ORIGIN}/einladung?token={TOKEN}", valid_hours=24),
    EmailChangeConfirm(confirm_link=f"{ORIGIN}/email/bestaetigen?token={TOKEN}", valid_hours=24),
    EmailChangeNotice(new_address="m.mustermann@example.de", changed_at=NOW,
                      reset_request_link=f"{ORIGIN}/passwort-vergessen"),
    PasswordReset(reset_link=f"{ORIGIN}/passwort-zuruecksetzen?token={TOKEN}", valid_minutes=60),
    PasswordChanged(changed_at=NOW, sessions_revoked=True, reset_request_link=f"{ORIGIN}/passwort-vergessen"),
    QuotaStorageWarning(organization_name=ORGANIZATION, used_bytes=1717986918,
                        limit_bytes=2 * 1024**3, percent=80, settings_link=f"{ORIGIN}/einstellungen"),
    QuotaStorageFull(organization_name=ORGANIZATION, used_bytes=2 * 1024**3,
                     limit_bytes=2 * 1024**3, settings_link=f"{ORIGIN}/einstellungen"),
    AiBudgetExhausted(organization_name=ORGANIZATION, resets_on=date(2026, 11, 1),
                      settings_link=f"{ORIGIN}/einstellungen"),
    ExportReady(organization_name=ORGANIZATION, size_bytes=25 * 1024**2,
                 available_until=datetime(2026, 11, 1, 13, 5, tzinfo=timezone.utc),
                 export_link=f"{ORIGIN}/einstellungen/datenexport"),
    OrganizationDeletionScheduled(organization_name=ORGANIZATION, effective_on=date(2026, 11, 14),
                                   cancel_until=date(2026, 11, 13), export_link=f"{ORIGIN}/einstellungen/datenexport"),
    OrganizationDeleted(organization_name=ORGANIZATION, retention_until=date(2036, 12, 31)),
    OperatorAlert(check="Anmeldung und Dokumentabruf", environment="hosted-eu", failed_at=NOW,
                   summary="Die synthetische Betriebsprüfung konnte das Testdokument nach der Anmeldung nicht abrufen. Der Abruf wurde mit HTTP 503 beantwortet.",
                   run_url="https://betrieb.example.de/runs/2026-10-31-1305",
                   issue_url="https://betrieb.example.de/issues/42"),
    OperatorNotice(title="Geplante Wartung am 3. November", paragraphs=[
        "Am 3. November 2026 wird Verwalino zwischen 20:00 und 20:30 Uhr gewartet.",
        "Während dieser Zeit kann die Anmeldung kurzzeitig nicht verfügbar sein. Speichern Sie Ihre Eingaben vor Beginn der Wartung.",
    ], action_label="Betriebshinweis ansehen", action_link="https://betrieb.example.de/hinweise/wartung-2026-11-03"),
)}
