"""RPA queue service: enqueue, claim, complete, retry, cancel, dead-letter.

How the queue works (this is the heart of the orchestration):

1. `enqueue()` inserts a row with status=queued and next_run_at=now. An
   optional idempotency_key makes re-submission a no-op (returns the same job).
2. `claim_next_job()` is what a worker calls in a loop. It picks the highest
   priority / oldest job whose next_run_at has passed and flips it to running
   with a lease (leased_until). The flip is a single conditional UPDATE
   (compare-and-set), so two workers can never claim the same row - on
   PostgreSQL the SELECT uses FOR UPDATE SKIP LOCKED for the same guarantee.
3. `complete_job()` records success, or schedules a retry with exponential
   backoff: next_run_at = now + backoff_base ** attempts (capped). When
   attempts reach max_attempts the job goes to dead_letter for a human.
4. `cancel_job()` removes queued work immediately and sets a cancellation flag
   for running work - the worker checks `should_abort()` between steps and exits
   gracefully instead of being killed mid-write.
5. `requeue_stale_leases()` is the crash-recovery sweeper: if a worker dies,
   its lease expires and the job returns to the queue automatically.
"""
from __future__ import annotations

import json
import random
import uuid
from datetime import timedelta
from typing import Any, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit
from app.core.helpers import bad_request, conflict, forbidden, not_found
from app.enums import RpaJobStatus
from app.models import RpaJob, RpaJobLog, RpaProcess, User
from app.models.base import utcnow
from app.models.datetime_type import as_aware
from app.serializers import dumps, loads

CANCELLED_FLAG_KEY = "__cancel_requested__"


# --------------------------------------------------------------------------- #
# creation
# --------------------------------------------------------------------------- #
def generate_job_ref() -> str:
    return f"JOB-{utcnow():%Y%m%d}-{uuid.uuid4().hex[:8].upper()}"


def enqueue(
    db: Session,
    *,
    process_id: int,
    payload: dict[str, Any],
    priority: int = 5,
    request_id: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    actor: Optional[User] = None,
    http_request=None,
    notes: Optional[str] = None,
    run_at: Optional[Any] = None,
) -> RpaJob:
    process = db.get(RpaProcess, process_id)
    if process is None:
        raise bad_request(f"Unknown RPA process id {process_id}")
    if not process.is_active:
        raise bad_request(f"RPA process '{process.code}' is deactivated")

    if idempotency_key:
        existing = db.scalar(select(RpaJob).where(RpaJob.idempotency_key == idempotency_key))
        if existing:
            return existing  # idempotent: no duplicate work

    job = RpaJob(
        job_ref=generate_job_ref(),
        process_id=process.id,
        request_id=request_id,
        status=RpaJobStatus.QUEUED,
        priority=max(1, min(9, priority)),
        payload_json=dumps(payload),
        attempts=0,
        max_attempts=process.max_attempts or settings.RPA_MAX_ATTEMPTS,
        next_run_at=run_at or utcnow(),
        idempotency_key=idempotency_key,
    )
    db.add(job)
    db.flush()

    log(db, job, attempt=0, level="INFO", step="enqueue",
        message=f"Job queued for process '{process.code}' (priority {job.priority})",
        worker_id=None, meta={"idempotency_key": idempotency_key})

    audit.record(
        db, entity_type="rpa_job", entity_id=job.id, action="rpa.job_queued",
        actor=actor, request_id=request_id,
        new_values={"job_ref": job.job_ref, "process": process.code, "priority": job.priority},
        notes=notes, http_request=http_request,
    )
    return job


def enqueue_by_code(db: Session, *, process_code: str, **kwargs) -> RpaJob:
    process = db.scalar(select(RpaProcess).where(RpaProcess.code == process_code))
    if process is None:
        raise not_found(f"RPA process '{process_code}'")
    return enqueue(db, process_id=process.id, **kwargs)


# --------------------------------------------------------------------------- #
# worker side
# --------------------------------------------------------------------------- #
def claim_next_job(
    db: Session, *, worker_id: str, process_ids: Optional[list[int]] = None,
    job_id: Optional[int] = None,
) -> Optional[RpaJob]:
    """Atomically claim the next eligible job. Returns None when queue is empty.

    `job_id` pins the claim to one specific job (used by targeted re-runs and by
    the tests); the compare-and-set UPDATE below is identical either way, so two
    workers can never end up owning the same row.
    """
    now = utcnow()
    stmt = (
        select(RpaJob)
        .where(
            RpaJob.status.in_([RpaJobStatus.QUEUED, RpaJobStatus.RETRY_SCHEDULED]),
            RpaJob.next_run_at <= now,
        )
        .order_by(RpaJob.priority.asc(), RpaJob.next_run_at.asc(), RpaJob.id.asc())
        .limit(1)
    )
    if not settings.is_sqlite:
        # PostgreSQL: let concurrent workers skip rows already locked by peers.
        stmt = stmt.with_for_update(skip_locked=True)
    if process_ids:
        stmt = stmt.where(RpaJob.process_id.in_(process_ids))
    if job_id is not None:
        stmt = stmt.where(RpaJob.id == job_id)

    job = db.scalar(stmt)
    if job is None:
        return None

    lease_seconds = job.process.timeout_seconds or settings.RPA_LEASE_SECONDS
    # Compare-and-set: only succeed if nobody else grabbed it in the meantime.
    result = db.execute(
        update(RpaJob)
        .where(
            RpaJob.id == job.id,
            RpaJob.status.in_([RpaJobStatus.QUEUED, RpaJobStatus.RETRY_SCHEDULED]),
        )
        .values(
            status=RpaJobStatus.RUNNING,
            worker_id=worker_id,
            started_at=now,
            leased_until=now + timedelta(seconds=lease_seconds),
            attempts=RpaJob.attempts + 1,
            finished_at=None,
        )
    )
    if result.rowcount != 1:
        # Another worker won the compare-and-set. Roll back to a savepoint-free
        # clean state and let the caller poll again - never raise.
        db.rollback()
        return None

    db.commit()
    db.refresh(job)
    log(db, job, attempt=job.attempts, level="INFO", step="claim",
        message=f"Claimed by worker '{worker_id}' (attempt {job.attempts}/{job.max_attempts})",
        worker_id=worker_id)
    db.commit()
    return job


def compute_backoff(attempts: int, base: float | None = None, cap_seconds: int = 900) -> float:
    """Exponential backoff with jitter: base ** attempts, capped, +/- 10%."""
    base = base if base is not None else settings.RPA_BACKOFF_BASE_SECONDS
    delay = min(base ** max(attempts, 1), cap_seconds)
    jitter = delay * 0.1 * (random.random() * 2 - 1)
    return round(max(0.1, delay + jitter), 2)


def complete_job(
    db: Session,
    *,
    job: RpaJob,
    worker_id: str,
    success: bool,
    result: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    retryable: bool = True,
    actor: Optional[User] = None,
    http_request=None,
) -> RpaJob:
    now = utcnow()
    job.finished_at = now
    job.leased_until = None
    job.worker_id = worker_id

    if success:
        job.status = RpaJobStatus.SUCCEEDED
        job.result_json = dumps(result or {})
        job.error_message = None
        log(db, job, attempt=job.attempts, level="INFO", step="complete",
            message=f"Succeeded after {job.attempts} attempt(s)", worker_id=worker_id, meta=result)
        audit.record(
            db, entity_type="rpa_job", entity_id=job.id, action="rpa.job_succeeded",
            actor=actor, request_id=job.request_id,
            new_values={"status": "succeeded", "attempts": job.attempts, "result": result},
            http_request=http_request,
        )
        _advance_linked_request(db, job=job, succeeded=True, actor=actor)
        db.commit()
        return job

    # ---- failure path ---------------------------------------------------- #
    job.error_message = (error or "unknown error")[:2000]
    exhausted = job.attempts >= job.max_attempts
    if exhausted or not retryable:
        job.status = RpaJobStatus.DEAD_LETTER if (exhausted and retryable) else RpaJobStatus.FAILED
        log(db, job, attempt=job.attempts, level="ERROR", step="failed",
            message=f"Job {'sent to dead-letter (retries exhausted)' if exhausted else 'failed (non-retryable)'}: {job.error_message}",
            worker_id=worker_id, meta={"error": error})
        audit.record(
            db, entity_type="rpa_job", entity_id=job.id,
            action="rpa.job_dead_letter" if job.status == RpaJobStatus.DEAD_LETTER else "rpa.job_failed",
            actor=actor, request_id=job.request_id,
            new_values={"status": job.status.value, "attempts": job.attempts, "error": job.error_message},
            http_request=http_request,
        )
        _advance_linked_request(db, job=job, succeeded=False, actor=actor)
        db.commit()
        return job

    backoff = compute_backoff(job.attempts, job.process.backoff_base_seconds if job.process else None)
    job.status = RpaJobStatus.RETRY_SCHEDULED
    job.next_run_at = now + timedelta(seconds=backoff)
    log(db, job, attempt=job.attempts, level="WARN", step="retry",
        message=f"Attempt {job.attempts} failed ({job.error_message}); retrying in {backoff}s",
        worker_id=worker_id, meta={"backoff_seconds": backoff})
    audit.record(
        db, entity_type="rpa_job", entity_id=job.id, action="rpa.job_retry_scheduled",
        actor=actor, request_id=job.request_id,
        new_values={"status": "retry_scheduled", "attempt": job.attempts, "retry_in_seconds": backoff, "error": error},
        http_request=http_request,
    )
    db.commit()
    return job


def should_abort(db: Session, job_id: int) -> tuple[bool, str]:
    """Cooperative cancellation: worker asks this between steps."""
    row = db.execute(select(RpaJob.status, RpaJob.payload_json).where(RpaJob.id == job_id)).first()
    if row is None:
        return True, "job vanished"
    status, payload_json = row
    if status == RpaJobStatus.CANCELLED:
        return True, "job was cancelled"
    payload = loads(payload_json, {})
    if isinstance(payload, dict) and payload.get(CANCELLED_FLAG_KEY):
        return True, "cancellation requested"
    return False, ""


def heartbeat(db: Session, job: RpaJob, worker_id: str, seconds: Optional[int] = None) -> None:
    """Extend the lease so long jobs are not reaped as stale."""
    lease = seconds or settings.RPA_LEASE_SECONDS
    db.execute(
        update(RpaJob)
        .where(RpaJob.id == job.id, RpaJob.worker_id == worker_id)
        .values(leased_until=utcnow() + timedelta(seconds=lease))
    )
    db.commit()


def requeue_stale_leases(db: Session) -> int:
    """Crash recovery: running jobs whose lease expired go back to the queue."""
    now = utcnow()
    stale = db.scalars(
        select(RpaJob).where(
            RpaJob.status == RpaJobStatus.RUNNING,
            RpaJob.leased_until.is_not(None),
            RpaJob.leased_until < now,
        )
    ).all()
    for job in stale:
        if job.attempts >= job.max_attempts:
            job.status = RpaJobStatus.DEAD_LETTER
            job.error_message = "Worker died mid-execution and retries are exhausted"
            job.finished_at = now
            log(db, job, attempt=job.attempts, level="ERROR", step="stale_lease",
                message="Lease expired with no heartbeat -> dead-letter", worker_id=job.worker_id)
        else:
            job.status = RpaJobStatus.RETRY_SCHEDULED
            job.next_run_at = now
            job.leased_until = None
            log(db, job, attempt=job.attempts, level="WARN", step="stale_lease",
                message=f"Lease expired (worker '{job.worker_id}' unresponsive) -> requeued",
                worker_id=job.worker_id)
        job.worker_id = None
    if stale:
        db.commit()
    return len(stale)


# --------------------------------------------------------------------------- #
# API side
# --------------------------------------------------------------------------- #
def cancel_job(
    db: Session, *, job: RpaJob, actor: User, reason: Optional[str] = None, http_request=None
) -> RpaJob:
    """Public API entry point: authorisation + cancellation."""
    if not (actor.is_admin or actor.role.value == "manager"):
        raise forbidden("Only a manager or admin can cancel an RPA job")
    _apply_cancellation(db, job=job, actor=actor, reason=reason, http_request=http_request)
    db.commit()
    return job


def _apply_cancellation(
    db: Session, *, job: RpaJob, actor: User, reason: Optional[str] = None,
    http_request=None, as_request_owner: bool = False,
) -> RpaJob:
    """Mutates the job to CANCELLED. Does NOT commit - the caller owns the
    transaction boundary (a SAVEPOINT when called from a request withdrawal)."""
    if job.status in (RpaJobStatus.SUCCEEDED, RpaJobStatus.CANCELLED, RpaJobStatus.FAILED, RpaJobStatus.DEAD_LETTER):
        raise conflict(f"Job is '{job.status.value}' and cannot be cancelled")

    now = utcnow()
    old_status = job.status.value
    if job.status == RpaJobStatus.RUNNING:
        # Ask the worker nicely: it checks should_abort() at every checkpoint and
        # exits without writing, instead of being killed mid-operation.
        payload = loads(job.payload_json, {})
        payload[CANCELLED_FLAG_KEY] = {
            "by": actor.email, "reason": reason, "at": now.isoformat(),
            "as_request_owner": as_request_owner,
        }
        job.payload_json = dumps(payload)
        job.status = RpaJobStatus.CANCELLED
        job.finished_at = now
        job.leased_until = None
        log(db, job, attempt=job.attempts, level="WARN", step="cancel",
            message=f"Cancellation requested for running job by {actor.email}: {reason or 'no reason given'}",
            worker_id=job.worker_id)
    else:
        job.status = RpaJobStatus.CANCELLED
        job.finished_at = now
        job.next_run_at = now
        log(db, job, attempt=job.attempts, level="WARN", step="cancel",
            message=f"Cancelled while '{old_status}' by {actor.email}: {reason or 'no reason given'}")

    job.cancelled_by = actor.id
    audit.record(
        db, entity_type="rpa_job", entity_id=job.id, action="rpa.job_cancelled",
        actor=actor, request_id=job.request_id,
        old_values={"status": old_status}, new_values={"status": "cancelled", "reason": reason},
        http_request=http_request,
    )
    db.flush()
    return job


def retry_job(db: Session, *, job: RpaJob, actor: User, reset_attempts: bool = True, http_request=None) -> RpaJob:
    if job.status not in (RpaJobStatus.FAILED, RpaJobStatus.DEAD_LETTER, RpaJobStatus.CANCELLED):
        raise conflict(f"Only failed / dead-letter / cancelled jobs can be retried (got '{job.status.value}')")
    if not actor.is_admin:
        raise forbidden("Only an admin can force a retry")

    old_status = job.status.value
    job.status = RpaJobStatus.QUEUED
    job.next_run_at = utcnow()
    job.error_message = None
    job.result_json = None
    job.finished_at = None
    job.leased_until = None
    job.worker_id = None
    if reset_attempts:
        job.attempts = 0
    payload = loads(job.payload_json, {})
    payload.pop(CANCELLED_FLAG_KEY, None)
    job.payload_json = dumps(payload)

    log(db, job, attempt=job.attempts, level="INFO", step="manual_retry",
        message=f"Manually requeued by {actor.email} (was '{old_status}')")
    audit.record(
        db, entity_type="rpa_job", entity_id=job.id, action="rpa.job_retried",
        actor=actor, request_id=job.request_id,
        old_values={"status": old_status}, new_values={"status": "queued", "attempts": job.attempts},
        http_request=http_request,
    )
    db.commit()
    return job


def cancel_jobs_for_request(
    db: Session, request_id: int, *, actor: User, reason: Optional[str] = None, http_request=None
) -> int:
    """When a business request is withdrawn, its automations must stop too.

    The requester owns the request, so they are implicitly authorised to cancel
    the jobs it produced (`as_request_owner=True`) even though the standalone
    cancel endpoint is manager/admin only.

    Each cancellation runs inside a SAVEPOINT (`db.begin_nested()`), so a job
    that is already terminal cannot roll back the *request* cancellation that
    triggered this call.
    """
    jobs = db.scalars(
        select(RpaJob).where(
            RpaJob.request_id == request_id,
            RpaJob.status.in_([RpaJobStatus.QUEUED, RpaJobStatus.RETRY_SCHEDULED, RpaJobStatus.RUNNING]),
        )
    ).all()

    cancelled = 0
    for job in jobs:
        try:
            with db.begin_nested():
                _apply_cancellation(db, job=job, actor=actor, reason=reason,
                                    http_request=http_request, as_request_owner=True)
            cancelled += 1
        except Exception:
            continue
    db.flush()
    return cancelled


def queue_stats(db: Session) -> dict[str, Any]:
    rows = db.execute(select(RpaJob.status, func.count()).group_by(RpaJob.status)).all()
    counts = {s.value: 0 for s in RpaJobStatus}
    for status_value, count in rows:
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        counts[key] = count
    total = sum(counts.values())

    durations = db.execute(
        select(RpaJob.started_at, RpaJob.finished_at).where(
            RpaJob.started_at.is_not(None), RpaJob.finished_at.is_not(None)
        )
    ).all()
    deltas = [
        (as_aware(f) - as_aware(s)).total_seconds() for s, f in durations if as_aware(f) and as_aware(s)
    ]
    avg_duration = round(sum(deltas) / len(deltas), 3) if deltas else None

    done = counts["succeeded"] + counts["failed"] + counts["dead_letter"]
    oldest = db.scalar(select(func.min(RpaJob.created_at)).where(RpaJob.status == RpaJobStatus.QUEUED))
    return {
        **counts,
        "total": total,
        "avg_duration_seconds": avg_duration,
        "success_rate": round(counts["succeeded"] / done, 4) if done else None,
        "oldest_queued_seconds": round((utcnow() - as_aware(oldest)).total_seconds(), 1) if oldest else None,
    }


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #
def log(
    db: Session, job: RpaJob, *, attempt: int, level: str, step: Optional[str],
    message: str, worker_id: Optional[str] = None, meta: Optional[dict] = None,
) -> RpaJobLog:
    entry = RpaJobLog(
        job_id=job.id,
        attempt=attempt,
        level=level.upper(),
        step=step,
        message=message[:4000],
        meta_json=json.dumps(meta, default=str) if meta else None,
        worker_id=worker_id,
        created_at=utcnow(),
    )
    db.add(entry)
    db.flush()
    return entry


def _advance_linked_request(db: Session, *, job: RpaJob, succeeded: bool, actor: Optional[User]) -> None:
    """Successful automation moves the business request to COMPLETED."""
    if not job.request_id:
        return
    from app.services import workflow  # local import avoids the circular dependency

    req = db.get(workflow.ApprovalRequest, job.request_id)
    if req is None:
        return
    if succeeded:
        workflow.mark_completed(db, req=req, actor=actor)
    else:
        audit.record(
            db, entity_type="approval_request", entity_id=req.id, action="request.rpa_failed",
            actor=actor, request_id=req.id,
            notes=f"Automation {job.job_ref} did not succeed ({job.status.value}); manual follow-up required",
        )
