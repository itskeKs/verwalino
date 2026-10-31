"""Render developer-owned mail templates without sending or accessing a database."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from textwrap import fill
from zoneinfo import ZoneInfo

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

from app.core.config import DeploymentMode, settings
from app.mail import brand
from app.mail.contexts import CONTEXT_TYPES, MailContext


def _size(value: int) -> str:
    amount = Decimal(value)
    units = ("Byte", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            rounded = amount.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            number = format(rounded, "f").removesuffix(".0").replace(".", ",")
            return f"{number} {unit}"
        amount /= 1024
    raise ValueError("Unreachable size unit")


def _date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _wrap(value: str) -> str:
    # Keep names, unbroken URLs and explicit paragraph/list boundaries intact.
    return "\n".join(fill(line, width=72, break_long_words=False, break_on_hyphens=False)
                     for line in value.splitlines())


ENVIRONMENT = Environment(
    loader=PackageLoader("app.mail", "templates/de"),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
ENVIRONMENT.filters.update(
    size=_size, date_de=_date, wrap_mail=_wrap,
    berlin_datetime=lambda value: value.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y, %H:%M"),
)
ENVIRONMENT.globals.update(brand=brand)


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    text: str
    html: str

    def apply_to(self, message: EmailMessage) -> None:
        """Fill a fresh, single-part message (policy.default); headers other than Subject stay."""
        if "Subject" in message:
            message.replace_header("Subject", self.subject)
        else:
            message["Subject"] = self.subject
        message.set_content(self.text)
        message.add_alternative(self.html, subtype="html")


def render_mail(context: MailContext, *, deployment_mode: DeploymentMode | str | None = None) -> RenderedMail:
    if CONTEXT_TYPES.get(context.kind) is not type(context):
        raise TypeError("Unsupported mail context")
    mode = DeploymentMode(settings.deployment_mode if deployment_mode is None else deployment_mode)
    values = {"mail": context, "hosted": mode == DeploymentMode.HOSTED}
    subject = ENVIRONMENT.get_template(f"{context.kind}.subject.txt").render(**values).strip()
    # A literal encoded word ("=?utf-8?b?...?=") would be decoded by the recipient's
    # client, smuggling characters the context validation refused into the subject.
    if not subject or any(c in subject for c in "\r\n") or "=?" in subject:
        raise ValueError("Mail subjects must be a non-empty single line without encoded words")
    text = ENVIRONMENT.get_template(f"{context.kind}.txt").render(**values).strip() + "\n"
    html = ENVIRONMENT.get_template(f"{context.kind}.html").render(**values).strip() + "\n"
    return RenderedMail(subject=subject, text=text, html=html)
