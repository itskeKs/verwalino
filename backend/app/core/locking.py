"""Bound contended organization writes without weakening stricter callers."""
from sqlalchemy import text

LOCK_WAIT_SECONDS = 5
LOCK_TIMEOUT_SQL = f"""SELECT set_config('lock_timeout',
    LEAST(NULLIF(setting::int,0),{LOCK_WAIT_SECONDS * 1000})::text,true)
    FROM pg_settings WHERE name='lock_timeout'"""


async def bound_lock_waits(db):
    """Keep the bound through subsequent row/trigger locks until transaction end."""
    # pg_settings reports lock_timeout in milliseconds; zero means unlimited.
    await db.execute(text(LOCK_TIMEOUT_SQL))
