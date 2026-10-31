"""Bounded, immutable inputs for developer-owned transactional templates."""

from datetime import date
from typing import Annotated, ClassVar
from unicodedata import category
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict,
    Field, StringConstraints, model_validator,
)


def _clean(value: object) -> object:
    if isinstance(value, str):
        if any(category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value):
            raise ValueError("Control characters are not permitted")
        return value.strip()
    return value


def _link(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError as exc:
        raise ValueError("Invalid absolute URL") from exc
    if (not host or url.username is not None or url.password is not None
            or "\\" in value or any(c.isspace() for c in value)
            or url.scheme not in {"http", "https"}
            or (url.scheme == "http" and host not in {"localhost", "127.0.0.1", "::1"})):
        raise ValueError("Links require HTTPS, or HTTP on loopback, without userinfo")
    return value


Name = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=200), BeforeValidator(_clean)]
Paragraph = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2000), BeforeValidator(_clean)]
MailLink = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=2048), BeforeValidator(_clean), AfterValidator(_link)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Bytes = Annotated[int, Field(strict=True, ge=0)]


class MailContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: ClassVar[str]


class SignupConfirmation(MailContext):
    kind = "signup_confirmation"
    confirm_link: MailLink
    valid_hours: PositiveInt


class SignupWelcome(MailContext):
    kind = "signup_welcome"
    admin_name: Name
    organization_name: Name
    login_link: MailLink


class UserInvitation(MailContext):
    kind = "user_invitation"
    inviter_name: Name
    organization_name: Name
    set_password_link: MailLink
    valid_hours: PositiveInt


class EmailChangeConfirm(MailContext):
    kind = "email_change_confirm"
    confirm_link: MailLink
    valid_hours: PositiveInt


def _address(value: str) -> str:
    from app.services.mail_service import validate_address

    return validate_address(value)


class EmailChangeNotice(MailContext):
    kind = "email_change_notice"
    new_address: Annotated[str, StringConstraints(strict=True, min_length=3, max_length=254), BeforeValidator(_clean), AfterValidator(_address)]
    changed_at: AwareDatetime
    reset_request_link: MailLink

    @property
    def masked_address(self) -> str:
        local, domain = self.new_address.rsplit("@", 1)
        return f"{local[0]}•••@{domain}"


class PasswordReset(MailContext):
    kind = "password_reset"
    reset_link: MailLink
    valid_minutes: PositiveInt


class PasswordChanged(MailContext):
    kind = "password_changed"
    changed_at: AwareDatetime
    sessions_revoked: bool
    reset_request_link: MailLink


class QuotaStorageWarning(MailContext):
    kind = "quota_storage_warning"
    organization_name: Name
    used_bytes: Bytes
    limit_bytes: PositiveInt
    percent: Annotated[int, Field(strict=True, ge=0)]
    settings_link: MailLink


class QuotaStorageFull(MailContext):
    kind = "quota_storage_full"
    organization_name: Name
    used_bytes: Bytes
    limit_bytes: PositiveInt
    settings_link: MailLink


class AiBudgetExhausted(MailContext):
    kind = "ai_budget_exhausted"
    organization_name: Name
    resets_on: date
    settings_link: MailLink


class ExportReady(MailContext):
    kind = "export_ready"
    organization_name: Name
    size_bytes: Bytes
    available_until: AwareDatetime
    export_link: MailLink


class OrganizationDeletionScheduled(MailContext):
    kind = "organization_deletion_scheduled"
    organization_name: Name
    effective_on: date
    export_link: MailLink
    cancel_until: date | None = None


class OrganizationDeleted(MailContext):
    kind = "organization_deleted"
    organization_name: Name
    retention_until: date | Annotated[int, Field(strict=True, ge=2000, le=9999)]


class OperatorAlert(MailContext):
    kind = "operator_alert"
    check: Name
    environment: Name
    failed_at: AwareDatetime
    summary: Paragraph
    run_url: MailLink | None = None
    issue_url: MailLink | None = None


class OperatorNotice(MailContext):
    kind = "operator_notice"
    title: Name
    paragraphs: Annotated[list[Paragraph], Field(min_length=1, max_length=10)]
    action_label: Name | None = None
    action_link: MailLink | None = None

    @model_validator(mode="after")
    def paired_action(self):
        if (self.action_label is None) != (self.action_link is None):
            raise ValueError("Action label and link must be provided together")
        return self


# The only inventory: template selection, samples and exhaustive tests use it.
CONTEXT_TYPES = {context.kind: context for context in (
    SignupConfirmation, SignupWelcome, UserInvitation, EmailChangeConfirm,
    EmailChangeNotice, PasswordReset, PasswordChanged, QuotaStorageWarning,
    QuotaStorageFull, AiBudgetExhausted, ExportReady, OrganizationDeletionScheduled,
    OrganizationDeleted, OperatorAlert, OperatorNotice,
)}
