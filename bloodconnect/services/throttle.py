"""Login lockout stored in the database.

Rate limits in memory only see one app process. Serverless hosts run several at once, so failed
logins are also counted here, where every process shares them. The count is keyed by account kind
and email whether or not the account exists, so a lockout never reveals which emails are registered.
"""

import hashlib
import math
from datetime import datetime, timedelta

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert

from ..extensions import db
from ..models import LoginThrottle, utcnow

MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)
LOCKOUT = timedelta(minutes=15)


def throttle_key(kind: str, email: str) -> str:
    return hashlib.sha256(f"{kind}:{email.strip().lower()}".encode()).hexdigest()


def minutes_locked(kind: str, email: str, now: datetime | None = None) -> int:
    """Whole minutes until this login may be tried again, or 0 if it isn't locked."""
    now = now or utcnow()
    locked_until = db.session.scalar(
        select(LoginThrottle.locked_until).where(LoginThrottle.key == throttle_key(kind, email))
    )
    if locked_until is None or locked_until <= now:
        return 0
    return max(1, math.ceil((locked_until - now).total_seconds() / 60))


def record_failure(kind: str, email: str, now: datetime | None = None) -> None:
    """Count one failed login atomically; the fifth within the window locks the login."""
    now = now or utcnow()
    table = LoginThrottle.__table__
    stale = table.c.window_started_at < now - WINDOW
    failures = case((stale, 1), else_=table.c.failures + 1)
    statement = (
        insert(LoginThrottle)
        .values(key=throttle_key(kind, email), failures=1, window_started_at=now, locked_until=None)
        .on_conflict_do_update(
            index_elements=[table.c.key],
            set_={
                "failures": failures,
                "window_started_at": case((stale, now), else_=table.c.window_started_at),
                "locked_until": case((failures >= MAX_FAILURES, now + LOCKOUT), else_=table.c.locked_until),
            },
        )
    )
    db.session.execute(statement)
    db.session.commit()


def clear(kind: str, email: str) -> None:
    db.session.execute(delete(LoginThrottle).where(LoginThrottle.key == throttle_key(kind, email)))
    db.session.commit()
