from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums import RpaJobStatus


class RpaProcessCreate(BaseModel):
    code: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=3, max_length=140)
    description: Optional[str] = None
    handler: str = Field(min_length=3, max_length=120, description="e.g. app.worker.handlers.create_vendor")
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_base_seconds: float = Field(default=2.0, ge=0.1, le=3600)
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class RpaProcessUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=3, max_length=140)
    description: Optional[str] = None
    handler: Optional[str] = None
    max_attempts: Optional[int] = Field(default=None, ge=1, le=10)
    backoff_base_seconds: Optional[float] = Field(default=None, ge=0.1, le=3600)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=3600)
    config: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class RpaProcessRead(RpaProcessCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    config: dict[str, Any] = {}
    created_at: datetime
    jobs_total: int = 0
    jobs_failed: int = 0


class RpaJobCreate(BaseModel):
    process_code: str = Field(min_length=1, description="Existing process code, e.g. create_vendor_in_sap")
    request_id: Optional[int] = None
    priority: int = Field(default=5, ge=1, le=9)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Optional[str] = Field(default=None, max_length=120)


class RpaJobLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    attempt: int
    level: str
    step: Optional[str] = None
    message: str
    worker_id: Optional[str] = None
    created_at: datetime


class RpaJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_ref: str
    process_id: int
    process_code: Optional[str] = None
    process_name: Optional[str] = None
    request_id: Optional[int] = None
    request_reference: Optional[str] = None
    status: RpaJobStatus
    priority: int
    payload: dict[str, Any] = {}
    result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    attempts: int
    max_attempts: int
    next_run_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    worker_id: Optional[str] = None
    created_at: datetime


class RpaJobComplete(BaseModel):
    """Reported by a worker after finishing an attempt."""
    success: bool
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = Field(default=None, max_length=2000)
    retryable: bool = Field(default=True, description="False -> fail immediately, do not retry")


class RpaQueueStats(BaseModel):
    queued: int
    running: int
    retry_scheduled: int
    succeeded: int
    failed: int
    cancelled: int
    dead_letter: int
    total: int
    avg_duration_seconds: Optional[float] = None
    success_rate: Optional[float] = None
    oldest_queued_seconds: Optional[float] = None


class WorkerLogCreate(BaseModel):
    """Payload a worker posts for each execution step it logs."""
    level: str = Field(default="INFO", pattern="^(INFO|WARN|ERROR|DEBUG)$")
    step: Optional[str] = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=2000)
    meta: Optional[dict[str, Any]] = None
