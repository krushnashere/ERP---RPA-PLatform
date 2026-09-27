"""RPA orchestration models: process definitions, job queue, execution logs.

The queue is *the database* - no external broker needed for this scale, which
makes jobs durable, queryable and auditable. Workers claim rows atomically
(UPDATE ... WHERE status='queued' ... LIMIT 1 / SELECT FOR UPDATE SKIP LOCKED on
Postgres), so N workers can run concurrently without double-processing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Float,
    CheckConstraint,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import RpaJobStatus
from app.models.base import TimestampMixin, UTCDateTime, utcnow


class RpaProcess(Base, TimestampMixin):
    """A registered automation, e.g. 'create_vendor_in_sap'."""
    __tablename__ = "rpa_processes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(140), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    handler: Mapped[str] = mapped_column(String(120), nullable=False)  # python dotted path
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    backoff_base_seconds: Mapped[float] = mapped_column(Float, default=2.0, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)

    jobs: Mapped[list["RpaJob"]] = relationship(back_populates="process")
    request_types: Mapped[list["RequestType"]] = relationship(back_populates="rpa_process")  # type: ignore[name-defined]

    __table_args__ = (
        CheckConstraint("max_attempts >= 1", name="ck_rpa_process_attempts"),
    )


class RpaJob(Base, TimestampMixin):
    """One unit of work sitting in the queue."""
    __tablename__ = "rpa_jobs"
    __table_args__ = (
        Index("ix_rpa_job_claimable", "status", "next_run_at"),
        Index("ix_rpa_job_priority", "priority", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_ref: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    process_id: Mapped[int] = mapped_column(ForeignKey("rpa_processes.id"), nullable=False, index=True)
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("approval_requests.id", ondelete="SET NULL"), index=True)

    status: Mapped[RpaJobStatus] = mapped_column(
        SAEnum(RpaJobStatus, native_enum=False, length=20),
        default=RpaJobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)  # 1 = highest
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    leased_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    worker_id: Mapped[Optional[str]] = mapped_column(String(64))
    cancelled_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True)

    process: Mapped["RpaProcess"] = relationship(back_populates="jobs")
    request: Mapped[Optional["ApprovalRequest"]] = relationship(back_populates="rpa_jobs")
    logs: Mapped[list["RpaJobLog"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="RpaJobLog.created_at"
    )

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 3)
        return None


class RpaJobLog(Base):
    """Step-by-step execution log written by the worker (great for debugging)."""
    __tablename__ = "rpa_job_logs"
    __table_args__ = (Index("ix_rpa_log_job", "job_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("rpa_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    level: Mapped[str] = mapped_column(String(10), default="INFO", nullable=False)
    step: Mapped[Optional[str]] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    meta_json: Mapped[Optional[str]] = mapped_column(Text)
    worker_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    job: Mapped["RpaJob"] = relationship(back_populates="logs")
