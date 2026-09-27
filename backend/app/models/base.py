"""Reusable ORM building blocks: timestamps, soft delete, optimistic locking."""
from datetime import datetime

from sqlalchemy import Boolean, Integer, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.datetime_type import UTCDateTime, utcnow

__all__ = ["SoftDeleteMixin", "TimestampMixin", "UTCDateTime", "VersionedMixin", "utcnow"]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=utcnow, nullable=False
    )


class SoftDeleteMixin:
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class VersionedMixin:
    """Optimistic concurrency control: clients send back the version they read;
    a stale write is rejected with HTTP 409 instead of silently clobbering a
    concurrent approval."""
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
