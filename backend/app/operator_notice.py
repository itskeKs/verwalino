"""Operator-only sub-processor notice; never exposed through HTTP.

Announces a new sub-processor (Art. 28 GDPR) to every organization's
administrators: at least 30 days before it becomes effective, with a
21-day objection period from receipt. One audit event per notified
recipient records the notice; a re-run sends only what is missing.
"""
import argparse
import asyncio
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from email.message import EmailMessage
from email.utils import formatdate
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.mail import render_mail
from app.mail.contexts import OperatorNotice
from app.services import mail_service

logger = logging.getLogger(__name__)

NOTICE_ACTION = "operator.subprocessor_notice.sent"
NOTICE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
NOTICE_ADVANCE_DAYS = 30
OBJECTION_DAYS = 21


@dataclass
class NoticeResult:
    notice_id: str
    dry_run: bool
    recipients: list[tuple[UUID, str]] = field(default_factory=list)
    sent: int = 0
    skipped: int = 0
    failed: list[tuple[UUID, str]] = field(default_factory=list)


def validate(notice_id: str, subprocessor: str, effective_date: date) -> None:
    if not NOTICE_ID_PATTERN.fullmatch(notice_id):
        raise RuntimeError("Notice id must be 4-64 lowercase letters, digits and dashes")
    if not subprocessor or len(subprocessor) > 200:
        raise RuntimeError("Sub-processor name is required (at most 200 characters)")
    if effective_date < date.today() + timedelta(days=NOTICE_ADVANCE_DAYS):
        raise RuntimeError(f"Effective date must be at least {NOTICE_ADVANCE_DAYS} days after sending")


def german_day(day: date) -> str:
    return day.strftime("%d.%m.%Y")


def notice_mail(*, subprocessor: str, effective_date: date, objection_deadline: date):
    rendered = render_mail(OperatorNotice(
        title=f"Neuer Unterauftragsverarbeiter: {subprocessor}",
        paragraphs=(
            f"Wir beauftragen den Dienstleister „{subprocessor}“ als Unterauftragsverarbeiter "
            "im Sinne von Art. 28 DSGVO mit der Verarbeitung von Daten im Rahmen des Betriebs "
            "der Verwalino-Installation Ihrer Organisation.",
            f"Wirksamkeit: {german_day(effective_date)} (mindestens {NOTICE_ADVANCE_DAYS} Tage "
            "nach Zugang dieses Hinweises).",
            f"Widerspruchsfrist: {german_day(objection_deadline)} ({OBJECTION_DAYS} Tage ab Zugang "
            "dieses Hinweises). Widersprechen Sie durch Antwort auf diese E-Mail; der Betreiber "
            "bearbeitet Widersprüche außerhalb der Anwendung.",
            "Ohne fristgerechten Widerspruch wird die Beauftragung zum Wirksamkeitsdatum wirksam.",
        ),
    ))
    sender = mail_service.validate_address(settings.smtp_from)
    message = EmailMessage()
    message["From"] = sender
    message["Reply-To"] = sender
    message["Date"] = formatdate(localtime=False)
    rendered.apply_to(message)
    return message


async def run(*, notice_id: str, subprocessor: str, effective_date: date,
              dry_run: bool = False, organization_ids=None) -> NoticeResult:
    validate(notice_id, subprocessor, effective_date)
    objection_deadline = date.today() + timedelta(days=OBJECTION_DAYS)
    engine = create_async_engine(settings.database_url_migrator)
    result = NoticeResult(notice_id=notice_id, dry_run=dry_run)
    try:
        async with AsyncSession(engine) as db:
            async with db.begin():
                if await db.scalar(text('SELECT session_user')) != 'app_migrator':
                    raise RuntimeError('Operator database role required')
                recipients = (await db.execute(text('''SELECT o.id, u.email FROM organization o
                  JOIN membership m ON m.organization_id=o.id AND m.role='OWNER_ADMIN'
                  JOIN "user" u ON u.id=m.user_id AND u.organization_id=o.id
                  WHERE u.is_active AND (:all_orgs OR o.id=ANY(CAST(:orgs AS uuid[])))
                  ORDER BY o.id, u.email'''),
                  {'all_orgs': organization_ids is None,
                   'orgs': [str(item) for item in (organization_ids or [])]})).all()
        result.recipients = [(row[0], row[1]) for row in recipients]
        for org, email in recipients:
            async with AsyncSession(engine) as db:
                async with db.begin():
                    await db.execute(text("SELECT set_config('app.current_org_id',:org,true)"), {'org': str(org)})
                    already = await db.scalar(text('''SELECT EXISTS(SELECT 1 FROM audit_event
                      WHERE organization_id=:org AND action=:action
                        AND payload_diff->>'notice_id'=:notice_id
                        AND payload_diff->>'recipient'=:email)'''),
                      {'org': str(org), 'action': NOTICE_ACTION, 'notice_id': notice_id, 'email': email})
                    if already:
                        result.skipped += 1
                        continue
                    if dry_run:
                        continue
                    message = notice_mail(subprocessor=subprocessor, effective_date=effective_date,
                                          objection_deadline=objection_deadline)
                    message["To"] = mail_service.validate_address(email)
                    try:
                        await asyncio.to_thread(mail_service.submit_notification_mail, settings, message)
                    except (mail_service.MailConfigurationError, RuntimeError, OSError) as error:
                        # No audit event is written for a failed recipient, so a
                        # re-run sends exactly what is still missing.
                        result.failed.append((org, email))
                        logger.warning('Sub-processor notice %s to %s (%s) failed: %s', notice_id, email, org, type(error).__name__)
                        continue
                    await db.execute(text('''INSERT INTO audit_event(id,organization_id,actor_user_id,
                        entity_type,entity_id,action,payload_diff) VALUES (:id,:org,NULL,
                        'operator_notice',NULL,:action,CAST(:payload AS jsonb))'''),
                      {'id': uuid.uuid4(), 'org': str(org), 'action': NOTICE_ACTION,
                       'payload': json.dumps({'notice_id': notice_id, 'subprocessor': subprocessor,
                                              'recipient': email,
                                              'effective_date': effective_date.isoformat(),
                                              'objection_deadline': objection_deadline.isoformat()})})
                    result.sent += 1
                    logger.info('Sub-processor notice %s sent to %s (%s)', notice_id, email, org)
    finally:
        await engine.dispose()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['subprocessor-notice'])
    parser.add_argument('--notice-id', required=True)
    parser.add_argument('--subprocessor', required=True)
    parser.add_argument('--effective-date', required=True, type=date.fromisoformat,
                        help='ISO date; must be at least 30 days after sending')
    parser.add_argument('--dry-run', action='store_true', help='list recipients without sending')
    args = parser.parse_args()
    result = asyncio.run(run(notice_id=args.notice_id, subprocessor=args.subprocessor,
                             effective_date=args.effective_date, dry_run=args.dry_run))
    heading = 'Recipients (dry run, nothing sent):' if result.dry_run else 'Recipients:'
    print(heading)
    for org, email in result.recipients:
        print(f'  {email} ({org})')
    if result.dry_run:
        print(f'Would send {len(result.recipients)} notice(s).')
    else:
        print(f'Notices sent: {result.sent}; already notified (nothing sent): {result.skipped}')
    for _, email in result.failed:
        print(f'Delivery failed (no notice recorded, retry on the next run): {email}', file=sys.stderr)
    if result.failed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
