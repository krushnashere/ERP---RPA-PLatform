"""RPA orchestration API: processes, queue, worker endpoints, logs, retries."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.auth import AdminUser, CurrentUser, DbSession, HttpRequest
from app.config import settings
from app.core import audit
from app.core.helpers import Page, PageParams, bad_request, conflict, forbidden, not_found, paginate
from app.core.security import decode_token
from app.enums import RpaJobStatus, UserRole
from app.models import RpaJob, RpaJobLog, RpaProcess, User
from app.schemas.rpa import (
    RpaJobComplete,
    RpaJobCreate,
    RpaJobLogRead,
    RpaJobRead,
    RpaProcessCreate,
    RpaProcessRead,
    RpaProcessUpdate,
    RpaQueueStats,
    WorkerLogCreate,
)
from app.serializers import dumps, rpa_job_read, rpa_process_read
from app.services import rpa_queue
from jose import JWTError

router = APIRouter(prefix="/rpa", tags=["rpa"])

JOB_STATUS_VALUES = [s.value for s in RpaJobStatus]


def _process_counts(db, process_id: int) -> tuple[int, int]:
    total = db.scalar(select(func.count()).select_from(RpaJob).where(RpaJob.process_id == process_id)) or 0
    failed = db.scalar(
        select(func.count()).select_from(RpaJob).where(
            RpaJob.process_id == process_id,
            RpaJob.status.in_([RpaJobStatus.FAILED, RpaJobStatus.DEAD_LETTER]),
        )
    ) or 0
    return int(total), int(failed)


def _job_with_process(db, job_id: int) -> RpaJob:
    job = db.scalar(select(RpaJob).options(selectinload(RpaJob.process)).where(RpaJob.id == job_id))
    if job is None:
        raise not_found("RPA job")
    return job


# --------------------------------------------------------------------------- #
# processes
# --------------------------------------------------------------------------- #
@router.get("/processes", response_model=list[RpaProcessRead], summary="List registered automations")
def list_processes(actor: AdminUser, db: DbSession, include_inactive: bool = False) -> list[RpaProcessRead]:
    stmt = select(RpaProcess)
    if not include_inactive:
        stmt = stmt.where(RpaProcess.is_active.is_(True))
    out = []
    for p in db.scalars(stmt.order_by(RpaProcess.code)).all():
        total, failed = _process_counts(db, p.id)
        out.append(rpa_process_read(p, jobs_total=total, jobs_failed=failed))
    return out


@router.post("/processes", response_model=RpaProcessRead, status_code=status.HTTP_201_CREATED)
def create_process(body: RpaProcessCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> RpaProcessRead:
    if db.scalar(select(RpaProcess).where(RpaProcess.code == body.code)):
        raise conflict(f"Process code '{body.code}' already exists")
    process = RpaProcess(
        code=body.code, name=body.name, description=body.description, handler=body.handler,
        max_attempts=body.max_attempts, backoff_base_seconds=body.backoff_base_seconds,
        timeout_seconds=body.timeout_seconds, config_json=dumps(body.config), is_active=body.is_active,
    )
    db.add(process)
    db.flush()
    audit.record(
        db, entity_type="rpa_process", entity_id=process.id, action="rpa.process_created", actor=actor,
        new_values=body.model_dump(), http_request=request,
    )
    db.commit()
    return rpa_process_read(process)


@router.patch("/processes/{process_id}", response_model=RpaProcessRead)
def update_process(
    process_id: int, body: RpaProcessUpdate, actor: AdminUser, db: DbSession, request: HttpRequest
) -> RpaProcessRead:
    process = db.get(RpaProcess, process_id)
    if process is None:
        raise not_found("RPA process")
    data = body.model_dump(exclude_unset=True)
    old = {k: getattr(process, k) for k in data if k != "config"}
    for field, value in data.items():
        if field == "config":
            process.config_json = dumps(value)
        else:
            setattr(process, field, value)
    audit.record(
        db, entity_type="rpa_process", entity_id=process.id, action="rpa.process_updated", actor=actor,
        old_values=old, new_values=data, http_request=request,
    )
    db.commit()
    total, failed = _process_counts(db, process.id)
    return rpa_process_read(process, jobs_total=total, jobs_failed=failed)


# --------------------------------------------------------------------------- #
# queue
# --------------------------------------------------------------------------- #
@router.post("/jobs", response_model=RpaJobRead, status_code=status.HTTP_201_CREATED, summary="Enqueue a job manually")
def create_job(body: RpaJobCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> RpaJobRead:
    if body.request_id is not None:
        from app.models import ApprovalRequest

        req = db.get(ApprovalRequest, body.request_id)
        if req is None:
            raise bad_request("Unknown request_id")
    job = rpa_queue.enqueue_by_code(
        db, process_code=body.process_code, payload=body.payload, priority=body.priority,
        request_id=body.request_id, idempotency_key=body.idempotency_key, actor=actor, http_request=request,
    )
    db.commit()
    return rpa_job_read(_job_with_process(db, job.id))


@router.get("/jobs", response_model=Page[RpaJobRead], summary="Browse the queue with filters")
def list_jobs(
    actor: CurrentUser,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    status_filter: Annotated[Optional[list[str]], Query(alias="status")] = None,
    process_id: Optional[int] = Query(None, ge=1),
    request_id: Optional[int] = Query(None, ge=1),
    worker_id: Optional[str] = Query(None, max_length=64),
    search: Optional[str] = Query(None, max_length=60),
    created_from: Optional[datetime] = None,
    sort_by: str = Query("created_at", pattern="^(created_at|next_run_at|priority|attempts)$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
) -> Page:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise forbidden("Only managers and admins can browse the automation queue")
    if status_filter and (unknown := set(status_filter) - set(JOB_STATUS_VALUES)):
        raise bad_request(f"Unknown status filter: {', '.join(sorted(unknown))}")

    stmt = select(RpaJob).options(selectinload(RpaJob.process)).join(RpaProcess, RpaJob.process_id == RpaProcess.id)
    if status_filter:
        stmt = stmt.where(RpaJob.status.in_([RpaJobStatus(s) for s in status_filter]))
    if process_id:
        stmt = stmt.where(RpaJob.process_id == process_id)
    if request_id:
        stmt = stmt.where(RpaJob.request_id == request_id)
    if worker_id:
        stmt = stmt.where(RpaJob.worker_id == worker_id)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(RpaJob.job_ref.ilike(like), RpaProcess.code.ilike(like), RpaProcess.name.ilike(like)))
    if created_from:
        stmt = stmt.where(RpaJob.created_at >= created_from)

    column = {"created_at": RpaJob.created_at, "next_run_at": RpaJob.next_run_at,
              "priority": RpaJob.priority, "attempts": RpaJob.attempts}[sort_by]
    stmt = stmt.order_by(column.desc() if sort_dir == "desc" else column.asc(), RpaJob.id.desc())
    return paginate(db, stmt, page, serializer=rpa_job_read)


@router.get("/queue/stats", response_model=RpaQueueStats, summary="Queue depth, success rate, avg duration")
def queue_stats(actor: CurrentUser, db: DbSession) -> dict:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise forbidden()
    return rpa_queue.queue_stats(db)


@router.post("/queue/sweep", summary="Requeue jobs whose worker died (stale lease sweeper)")
def sweep(actor: AdminUser, db: DbSession, request: HttpRequest) -> dict:
    count = rpa_queue.requeue_stale_leases(db)
    audit.record(
        db, entity_type="rpa_queue", action="rpa.stale_leases_swept", actor=actor,
        new_values={"requeued": count}, http_request=request,
    )
    db.commit()
    return {"requeued": count}


@router.get("/jobs/{job_id}", response_model=RpaJobRead)
def get_job(job_id: int, actor: CurrentUser, db: DbSession) -> RpaJobRead:
    job = _job_with_process(db, job_id)
    if actor.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise forbidden()
    return rpa_job_read(job)


@router.get("/jobs/{job_id}/logs", response_model=list[RpaJobLogRead], summary="Execution log of a job")
def job_logs(
    job_id: int, actor: CurrentUser, db: DbSession,
    level: Optional[str] = Query(None, pattern="^(INFO|WARN|ERROR|DEBUG)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[RpaJobLog]:
    if actor.role not in (UserRole.ADMIN, UserRole.SUPER_ADMIN, UserRole.MANAGER):
        raise forbidden()
    _job_with_process(db, job_id)
    stmt = select(RpaJobLog).where(RpaJobLog.job_id == job_id)
    if level:
        stmt = stmt.where(RpaJobLog.level == level)
    return list(db.scalars(stmt.order_by(RpaJobLog.created_at.desc(), RpaJobLog.id.desc()).limit(limit)).all())[::-1]


@router.post("/jobs/{job_id}/cancel", response_model=RpaJobRead, summary="Cancel a queued or running job")
def cancel_job(
    job_id: int, actor: CurrentUser, db: DbSession, request: HttpRequest,
    reason: Optional[str] = Query(None, max_length=300),
) -> RpaJobRead:
    job = _job_with_process(db, job_id)
    rpa_queue.cancel_job(db, job=job, actor=actor, reason=reason, http_request=request)
    return rpa_job_read(job)


@router.post("/jobs/{job_id}/retry", response_model=RpaJobRead, summary="Force-retry a failed / dead-letter job")
def retry_job(job_id: int, actor: AdminUser, db: DbSession, request: HttpRequest) -> RpaJobRead:
    job = _job_with_process(db, job_id)
    rpa_queue.retry_job(db, job=job, actor=actor, http_request=request)
    return rpa_job_read(job)


# --------------------------------------------------------------------------- #
# worker endpoints (machine-to-machine: bearer token with type=worker or admin JWT)
# --------------------------------------------------------------------------- #
def _worker_auth(authorization: Optional[str], db) -> User | None:
    """Workers authenticate with a normal admin JWT or with WORKER_TOKEN."""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        if token == settings.JWT_SECRET_KEY and settings.ENVIRONMENT != "production":
            return None  # shared dev worker token -> system actor
        try:
            payload = decode_token(token, expected_type="access")
            user = db.get(User, int(payload["sub"]))
            if user and user.is_active and user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
                return user
        except (JWTError, ValueError, TypeError):
            pass
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Worker not authorised")


@router.post("/worker/claim", response_model=Optional[RpaJobRead], summary="Worker claims the next queued job")
def worker_claim(
    db: DbSession,
    request: HttpRequest,
    x_worker_id: str = Header(default="worker-1", max_length=64),
    process_ids: Optional[str] = Query(None, description="Comma-separated process ids this worker handles"),
) -> Optional[RpaJobRead]:
    _worker_auth(request.headers.get("authorization"), db)
    ids = [int(i) for i in process_ids.split(",") if i.strip().isdigit()] if process_ids else None
    job = rpa_queue.claim_next_job(db, worker_id=x_worker_id, process_ids=ids or None)
    if job is None:
        return None
    return rpa_job_read(_job_with_process(db, job.id))


@router.post("/worker/jobs/{job_id}/complete", response_model=RpaJobRead, summary="Worker reports the attempt result")
def worker_complete(
    job_id: int, body: RpaJobComplete, db: DbSession, request: HttpRequest,
    x_worker_id: str = Header(default="worker-1", max_length=64),
) -> RpaJobRead:
    actor = _worker_auth(request.headers.get("authorization"), db)
    job = _job_with_process(db, job_id)
    if job.status != RpaJobStatus.RUNNING:
        raise conflict(f"Job is '{job.status.value}', not running")
    if job.worker_id and job.worker_id != x_worker_id:
        raise forbidden(f"Job is owned by worker '{job.worker_id}'")
    rpa_queue.complete_job(
        db, job=job, worker_id=x_worker_id, success=body.success, result=body.result,
        error=body.error, retryable=body.retryable, actor=actor, http_request=request,
    )
    return rpa_job_read(_job_with_process(db, job_id))


@router.post("/worker/jobs/{job_id}/log", status_code=status.HTTP_201_CREATED, summary="Worker appends an execution log line")
def worker_log(
    job_id: int, body: WorkerLogCreate, db: DbSession, request: HttpRequest,
    x_worker_id: str = Header(default="worker-1", max_length=64),
) -> dict:
    _worker_auth(request.headers.get("authorization"), db)
    job = _job_with_process(db, job_id)
    entry = rpa_queue.log(db, job, attempt=job.attempts, level=body.level, step=body.step,
                          message=body.message, worker_id=x_worker_id, meta=body.meta)
    db.commit()
    return {"id": entry.id, "job_id": job.id, "level": entry.level, "message": entry.message}


@router.get("/worker/heartbeat", summary="Worker heartbeat + stale lease sweep")
def worker_heartbeat(
    db: DbSession, request: HttpRequest, x_worker_id: str = Header(default="worker-1", max_length=64)
) -> dict:
    _worker_auth(request.headers.get("authorization"), db)
    swept = rpa_queue.requeue_stale_leases(db)
    live = db.scalar(
        select(func.count()).select_from(RpaJob).where(
            RpaJob.status == RpaJobStatus.RUNNING, RpaJob.worker_id == x_worker_id
        )
    )
    return {"worker_id": x_worker_id, "running_jobs": int(live or 0), "stale_leases_requeued": int(swept or 0)}
