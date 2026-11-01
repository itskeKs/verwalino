"""Bounds only for deletion lifecycle and hosted quota admission."""
import asyncio
import logging

from sqlalchemy import text

# Deletion and hosted quota transactions may contend with an import or report.
# Thirty seconds accommodates normal contention while bounding these paths,
# including their subsequent row/trigger locks, until commit or rollback.
# Ordinary tenant/review/worker transactions retain their previous wait policy.
LOCK_WAIT_SECONDS = 30
# Hosted directory admission uses the same budget; recount uses nonblocking
# flock and deletion CLI uses try-advisory locks / NOWAIT so operators can retry.
LOCK_TIMEOUT_SQL = f"""SELECT set_config('lock_timeout',
    LEAST(NULLIF(setting::int,0),{LOCK_WAIT_SECONDS * 1000})::text,true)
    FROM pg_settings WHERE name='lock_timeout'"""

LOCK_TIMEOUT_MESSAGE = ('Der Vorgang ist gerade durch eine andere Aktion gesperrt. '
                        'Bitte versuchen Sie es in Kürze erneut.')


class LockTimeoutError(TimeoutError):
    """A contended application lock can be retried without reporting a crash."""


class StorageLockTimeout(LockTimeoutError):
    """Hosted document directory admission exceeded its wait budget."""


def is_lock_timeout(error):
    original = getattr(error, 'orig', error)
    return (isinstance(error, LockTimeoutError)
            or getattr(original, 'sqlstate', None) == '55P03'
            or getattr(original, 'pgcode', None) == '55P03')


async def retry_worker_completion(operation):
    """Retry fresh transactions with the same output; DB lease fencing still applies."""
    while True:
        try:
            return await operation()
        except Exception as error:
            if not is_lock_timeout(error):
                raise
            logging.getLogger(__name__).warning('Worker completion lock held; retrying saved output')
            await asyncio.sleep(1)


async def bound_lock_waits(db):
    """Keep the bound through subsequent row/trigger locks until transaction end."""
    # pg_settings reports lock_timeout in milliseconds; zero means unlimited.
    await db.execute(text(LOCK_TIMEOUT_SQL))
