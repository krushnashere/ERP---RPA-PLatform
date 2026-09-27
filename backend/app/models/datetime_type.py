"""Timezone-safe DateTime column type.

PostgreSQL stores `TIMESTAMP WITH TIME ZONE`, SQLite does not - it happily drops
the offset. Without this, an app that runs on both would mix naive and aware
datetimes and blow up with
    TypeError: can't compare offset-naive and offset-aware datetimes

`UTCDateTime` normalises in both directions:
  * bind (Python -> DB): always convert to UTC and strip tzinfo
  * result (DB -> Python): always re-attach UTC

So application code can use timezone-aware datetimes everywhere, and every
comparison (`next_run_at <= now`, SLA checks, lease expiry) is consistent.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Timezone-aware UTC now."""
    return datetime.now(timezone.utc)


def as_aware(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime (defensive helper used by the API layer)."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
