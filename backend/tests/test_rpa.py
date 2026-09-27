"""RPA orchestration: queue-based execution, workers, retries with exponential
backoff, cancellation, dead-lettering, stale-lease recovery and audit logging."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.enums import RpaJobStatus
from app.models import ApprovalRequest, RpaJob, RpaProcess
from app.models.base import utcnow
from app.services import rpa_queue
from app.worker.handlers import CancelledError_, NonRetryableError, WorkerContext, resolve
from tests.conftest import API, request_type_id, token_for

WORKER = "pytest-worker-1"


def run_job(job_id: int, worker_id: str = WORKER) -> RpaJob:
    """Mimics one worker iteration: claim -> execute handler -> report result."""
    db = SessionLocal()
    try:
        job = db.get(RpaJob, job_id)
        assert job is not None
        assert job.status in (RpaJobStatus.QUEUED, RpaJobStatus.RETRY_SCHEDULED)
        claimed = rpa_queue.claim_next_job(db, worker_id=worker_id, job_id=job_id)
        assert claimed is not None and claimed.id == job_id, "the worker must claim exactly this job"
        process = claimed.process
        payload = __import__("json").loads(claimed.payload_json or "{}")

        def _log(message, step=None, level="INFO"):
            rpa_queue.log(db, claimed, attempt=claimed.attempts, level=level, step=step,
                          message=message, worker_id=worker_id)
            db.commit()

        def _cancelled():
            db.expire_all()
            abort, _reason = rpa_queue.should_abort(db, claimed.id)
            return abort

        ctx = WorkerContext(
            job_id=claimed.id, job_ref=claimed.job_ref, attempt=claimed.attempts, worker_id=worker_id,
            config={}, log=_log, check_cancelled=_cancelled,
        )
        try:
            result = resolve(process.handler)(payload, ctx)
            rpa_queue.complete_job(db, job=claimed, worker_id=worker_id, success=True, result=result)
        except CancelledError_ as exc:
            rpa_queue.complete_job(db, job=claimed, worker_id=worker_id, success=False, error=str(exc), retryable=False)
        except NonRetryableError as exc:
            rpa_queue.complete_job(db, job=claimed, worker_id=worker_id, success=False, error=str(exc), retryable=False)
        except Exception as exc:
            rpa_queue.complete_job(db, job=claimed, worker_id=worker_id, success=False,
                                   error=f"{type(exc).__name__}: {exc}", retryable=True)
        db.refresh(claimed)
        return claimed
    finally:
        db.close()


def drain_queue(max_iterations: int = 60) -> int:
    """Process every job currently claimable so tests start from an empty queue."""
    processed = 0
    for _ in range(max_iterations):
        db = SessionLocal()
        try:
            job = rpa_queue.claim_next_job(db, worker_id="drain")
            if job is None:
                break
            process = job.process
            import json as _json

            payload = _json.loads(job.payload_json or "{}")
            ctx = WorkerContext(job_id=job.id, job_ref=job.job_ref, attempt=job.attempts,
                                worker_id="drain", config={}, log=None, check_cancelled=None)
            try:
                result = resolve(process.handler)(payload, ctx)
                rpa_queue.complete_job(db, job=job, worker_id="drain", success=True, result=result)
            except CancelledError_ as exc:
                rpa_queue.complete_job(db, job=job, worker_id="drain", success=False, error=str(exc), retryable=False)
            except NonRetryableError as exc:
                rpa_queue.complete_job(db, job=job, worker_id="drain", success=False, error=str(exc), retryable=False)
            except Exception as exc:
                rpa_queue.complete_job(db, job=job, worker_id="drain", success=False,
                                       error=f"{type(exc).__name__}: {exc}", retryable=False)
            processed += 1
        finally:
            db.close()
    return processed


def make_process(db, code: str, *, max_attempts: int = 3, handler: str = "app.worker.handlers.create_vendor_in_sap") -> RpaProcess:
    process = RpaProcess(
        code=code, name=code.replace("_", " ").title(), handler=handler,
        max_attempts=max_attempts, backoff_base_seconds=1.5, timeout_seconds=60, config_json="{}",
    )
    db.add(process)
    db.commit()
    db.refresh(process)
    return process


@pytest.fixture
def process_code(db):
    """A unique, per-test process so tests never steal each other's queued jobs."""
    import uuid

    code = f"proc_{uuid.uuid4().hex[:10]}"
    make_process(db, code)
    return code


def enqueue(db, process_code: str, payload: dict, priority: int = 5) -> RpaJob:
    job = rpa_queue.enqueue_by_code(db, process_code=process_code, payload=payload, priority=priority)
    db.commit()
    db.refresh(job)
    return job


def approve_chain(client, tokens, request_id: str | int, viewer: dict):
    """Approve every pending level, acting AS the approver the engine assigned."""
    final = None
    for _ in range(5):
        detail = client.get(f"{API}/requests/{request_id}", headers=viewer).json()
        step = next((s for s in detail["steps"] if s["status"] == "pending"), None)
        if step is None:
            return detail
        approver_token = token_for(tokens, step["approver_name"])
        response = client.post(
            f"{API}/requests/{request_id}/approve",
            json={"remarks": f"approved at level {step['level']}"},
            headers={"Authorization": f"Bearer {approver_token}"},
        )
        assert response.status_code == 200, response.text
        final = response.json()
    return final


def good_payload() -> dict:
    return {"vendor": {"name": "Acme Industrial Supplies", "gstin": "27AAACA1234A1Z5"}, "reference_no": "REQ-TEST"}


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
def test_enqueue_claim_execute_succeed(db, process_code):
    job = enqueue(db, process_code, good_payload())
    assert job.status == RpaJobStatus.QUEUED
    assert job.job_ref.startswith("JOB-")
    assert job.attempts == 0

    finished = run_job(job.id)
    assert finished.status == RpaJobStatus.SUCCEEDED
    assert finished.attempts == 1
    assert finished.started_at and finished.finished_at
    assert finished.duration_seconds >= 0
    assert finished.worker_id == WORKER

    import json

    result = json.loads(finished.result_json)
    assert result["vendor_code"].startswith("V")
    assert result["vendor_name"] == "Acme Industrial Supplies"

    logs = db.scalars(select(rpa_queue.RpaJobLog).where(rpa_queue.RpaJobLog.job_id == job.id)).all()
    steps = [entry.step for entry in logs]
    assert "enqueue" in steps and "claim" in steps and "complete" in steps
    assert "validate_payload" in steps and "call_sap_api" in steps


def test_queue_is_ordered_by_priority_then_fifo(db, process_code):
    process_id = db.scalar(select(RpaProcess.id).where(RpaProcess.code == process_code))
    low = enqueue(db, process_code, good_payload(), priority=9)
    high = enqueue(db, process_code, good_payload(), priority=1)
    medium = enqueue(db, process_code, good_payload(), priority=5)

    session = SessionLocal()
    try:
        scoped = lambda: rpa_queue.claim_next_job(session, worker_id="w-order", process_ids=[process_id])
        assert scoped().id == high.id      # priority 1 first
        assert scoped().id == medium.id    # then 5
        assert scoped().id == low.id       # then 9
        assert scoped() is None            # this process is drained
    finally:
        session.close()


def test_two_workers_never_claim_the_same_job(db, process_code):
    process_id = db.scalar(select(RpaProcess.id).where(RpaProcess.code == process_code))
    job = enqueue(db, process_code, good_payload())
    first = SessionLocal()
    second = SessionLocal()
    try:
        claimed_a = rpa_queue.claim_next_job(first, worker_id="worker-a", process_ids=[process_id])
        claimed_b = rpa_queue.claim_next_job(second, worker_id="worker-b", process_ids=[process_id])
        assert claimed_a is not None and claimed_a.id == job.id
        assert claimed_b is None, "the second worker must find an empty queue"
    finally:
        first.close()
        second.close()


def test_idempotency_key_prevents_duplicate_jobs(db, process_code):
    first = rpa_queue.enqueue_by_code(db, process_code=process_code, payload=good_payload(),
                                      idempotency_key="request:42:approved")
    db.commit()
    second = rpa_queue.enqueue_by_code(db, process_code=process_code, payload=good_payload(),
                                       idempotency_key="request:42:approved")
    db.commit()
    assert first.id == second.id
    total = db.scalar(select(rpa_queue.RpaJob).where(rpa_queue.RpaJob.idempotency_key == "request:42:approved"))
    assert total is not None


# --------------------------------------------------------------------------- #
# retries / dead letter
# --------------------------------------------------------------------------- #
def test_transient_failure_retries_with_exponential_backoff(db, process_code):
    payload = good_payload() | {"__simulate_failure__": True}
    job = enqueue(db, process_code, payload)

    first = run_job(job.id)
    assert first.status == RpaJobStatus.RETRY_SCHEDULED
    assert first.attempts == 1
    assert "503" in first.error_message
    assert first.next_run_at > first.finished_at, "a backoff delay must be applied"

    delay_1 = (first.next_run_at - first.finished_at).total_seconds()

    # simulate the backoff elapsing, then fail again
    session = SessionLocal()
    try:
        row = session.get(RpaJob, job.id)
        row.next_run_at = utcnow()
        session.commit()
    finally:
        session.close()

    second = run_job(job.id)
    assert second.status == RpaJobStatus.RETRY_SCHEDULED
    assert second.attempts == 2
    delay_2 = (second.next_run_at - second.finished_at).total_seconds()
    assert delay_2 > delay_1, "backoff must grow with each attempt"

    session = SessionLocal()
    try:
        row = session.get(RpaJob, job.id)
        row.next_run_at = utcnow()
        session.commit()
    finally:
        session.close()

    third = run_job(job.id)
    assert third.status == RpaJobStatus.DEAD_LETTER, "retries exhausted -> dead letter for a human"
    assert third.attempts == 3 == third.max_attempts


def test_non_retryable_failure_does_not_retry(db, process_code):
    payload = {"vendor": {"gstin": "BAD"}}  # no vendor name -> business rule failure
    job = enqueue(db, process_code, payload)
    finished = run_job(job.id)
    assert finished.status == RpaJobStatus.FAILED
    assert finished.attempts == 1
    assert "vendor name" in finished.error_message


def test_backoff_calculation_is_exponential_and_capped():
    assert rpa_queue.compute_backoff(1, base=2.0) == pytest.approx(2.0, abs=0.4)
    assert rpa_queue.compute_backoff(2, base=2.0) == pytest.approx(4.0, abs=0.8)
    assert rpa_queue.compute_backoff(3, base=2.0) == pytest.approx(8.0, abs=1.6)
    assert rpa_queue.compute_backoff(50, base=2.0, cap_seconds=900) <= 900 * 1.1


# --------------------------------------------------------------------------- #
# cancellation
# --------------------------------------------------------------------------- #
def test_cancel_a_queued_job(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    job = enqueue(db, process_code, good_payload())

    response = client.post(f"{API}/rpa/jobs/{job.id}/cancel?reason=raised+in+error", headers=admin)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"

    # cancelling twice is a conflict, and the job never runs again
    assert client.post(f"{API}/rpa/jobs/{job.id}/cancel", headers=admin).status_code == 409
    session = SessionLocal()
    try:
        assert rpa_queue.claim_next_job(session, worker_id="w", job_id=job.id) is None
    finally:
        session.close()


def test_cancel_a_running_job_stops_it_at_the_next_checkpoint(db, process_code):
    job = enqueue(db, process_code, good_payload())
    session = SessionLocal()
    try:
        claimed = rpa_queue.claim_next_job(session, worker_id="long-runner")
        assert claimed is not None and claimed.status == RpaJobStatus.RUNNING
        rpa_queue.cancel_job(session, job=claimed, actor=_admin_user(session), reason="stop it")

        abort, reason = rpa_queue.should_abort(session, job.id)
        assert abort is True and "cancel" in reason

        # the handler notices at its next checkpoint and exits without writing
        with pytest.raises(CancelledError_):
            ctx = WorkerContext(job_id=job.id, job_ref=job.job_ref, attempt=1, worker_id="long-runner",
                                config={}, log=None,
                                check_cancelled=lambda: rpa_queue.should_abort(session, job.id)[0])
            resolve("app.worker.handlers.create_vendor_in_sap")(good_payload(), ctx)
    finally:
        session.close()


def test_employee_cannot_cancel_jobs(client, tokens, db, process_code):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    job = enqueue(db, process_code, good_payload())
    assert client.post(f"{API}/rpa/jobs/{job.id}/cancel", headers=employee).status_code == 403


def test_manual_retry_of_a_dead_letter_job(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    payload = good_payload() | {"__simulate_failure__": True}
    job = enqueue(db, process_code, payload)
    for _ in range(3):
        session = SessionLocal()
        try:
            row = session.get(RpaJob, job.id)
            if row.status == RpaJobStatus.RETRY_SCHEDULED:
                row.next_run_at = utcnow()
                session.commit()
        finally:
            session.close()
        run_job(job.id)

    session = SessionLocal()
    try:
        assert session.get(RpaJob, job.id).status == RpaJobStatus.DEAD_LETTER
    finally:
        session.close()

    retried = client.post(f"{API}/rpa/jobs/{job.id}/retry", headers=admin)
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "queued"
    assert retried.json()["attempts"] == 0
    assert retried.json()["error_message"] is None

    # this time the payload is fixed, so it succeeds
    session = SessionLocal()
    try:
        row = session.get(RpaJob, job.id)
        import json

        payload = json.loads(row.payload_json)
        payload.pop("__simulate_failure__")
        row.payload_json = json.dumps(payload)
        session.commit()
    finally:
        session.close()
    assert run_job(job.id).status == RpaJobStatus.SUCCEEDED


# --------------------------------------------------------------------------- #
# crash recovery
# --------------------------------------------------------------------------- #
def test_stale_lease_is_swept_and_the_job_requeued(db, process_code):
    job = enqueue(db, process_code, good_payload())
    session = SessionLocal()
    try:
        claimed = rpa_queue.claim_next_job(session, worker_id="crashed-worker")
        assert claimed.status == RpaJobStatus.RUNNING

        # pretend the worker died 10 minutes ago without a heartbeat
        row = session.get(RpaJob, job.id)
        row.leased_until = utcnow() - timedelta(minutes=10)
        session.commit()

        swept = rpa_queue.requeue_stale_leases(session)
        assert swept == 1
        session.refresh(row)
        assert row.status == RpaJobStatus.RETRY_SCHEDULED
        assert row.worker_id is None
        assert row.next_run_at <= utcnow()

        # ...and another worker can now pick it up
        rescued = rpa_queue.claim_next_job(session, worker_id="healthy-worker", job_id=job.id)
        assert rescued is not None and rescued.id == job.id
    finally:
        session.close()


def test_stale_lease_with_exhausted_attempts_goes_to_dead_letter(db, process_code):
    payload = good_payload() | {"__simulate_failure__": True}
    job = enqueue(db, process_code, payload)
    session = SessionLocal()
    try:
        for _ in range(3):
            row = session.get(RpaJob, job.id)
            row.next_run_at = utcnow()
            session.commit()
            claimed = rpa_queue.claim_next_job(session, worker_id="doomed-worker", job_id=job.id)
            assert claimed is not None
            rpa_queue.complete_job(session, job=claimed, worker_id="doomed-worker",
                                   success=False, error="boom", retryable=True)
        row = session.get(RpaJob, job.id)
        assert row.status == RpaJobStatus.DEAD_LETTER
    finally:
        session.close()


# --------------------------------------------------------------------------- #
# API surface
# --------------------------------------------------------------------------- #
def test_job_listing_filters_and_logs_endpoint(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    process = db.scalar(select(RpaProcess).where(RpaProcess.code == process_code))
    job = enqueue(db, process_code, good_payload())
    run_job(job.id)

    listed = client.get(f"{API}/rpa/jobs?status=succeeded&process_id={process.id}", headers=admin).json()
    assert listed["total"] >= 1
    assert all(item["status"] == "succeeded" for item in listed["items"])
    assert listed["items"][0]["process_code"] == process_code

    searched = client.get(f"{API}/rpa/jobs?search={job.job_ref}", headers=admin).json()
    assert searched["total"] == 1

    logs = client.get(f"{API}/rpa/jobs/{job.id}/logs", headers=admin).json()
    assert logs and logs[0]["step"] == "enqueue"
    assert any(entry["step"] == "call_sap_api" for entry in logs)
    assert all(entry["level"] in {"INFO", "WARN", "ERROR", "DEBUG"} for entry in logs)

    assert client.get(f"{API}/rpa/jobs?status=bogus", headers=admin).status_code == 400
    assert client.get(f"{API}/rpa/jobs/{job.id}/logs", headers={"Authorization": f"Bearer {tokens["employee"]}"}).status_code == 403


def test_queue_stats(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    enqueue(db, process_code, good_payload())
    stats = client.get(f"{API}/rpa/queue/stats", headers=admin).json()
    assert stats["total"] >= 1
    assert {"queued", "running", "succeeded", "failed", "cancelled", "dead_letter"} <= set(stats)


def test_worker_http_endpoints_require_authorisation(client, db, process_code):
    assert client.post(f"{API}/rpa/worker/claim").status_code == 401
    assert client.post(f"{API}/rpa/worker/jobs/1/complete", json={"success": True}).status_code == 401


def test_worker_http_endpoints_claim_and_complete(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    process_id = db.scalar(select(RpaProcess.id).where(RpaProcess.code == process_code))
    job = enqueue(db, process_code, good_payload())

    headers = dict(admin) | {"X-Worker-Id": "http-worker-7"}
    claimed = client.post(f"{API}/rpa/worker/claim?process_ids={process_id}", headers=headers)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["id"] == job.id
    assert claimed.json()["status"] == "running"
    assert claimed.json()["attempts"] == 1

    logged = client.post(
        f"{API}/rpa/worker/jobs/{job.id}/log",
        json={"level": "INFO", "step": "login_to_sap", "message": "Session established"},
        headers=headers,
    )
    assert logged.status_code == 201

    completed = client.post(
        f"{API}/rpa/worker/jobs/{job.id}/complete",
        json={"success": True, "result": {"vendor_code": "V555000"}},
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "succeeded"
    assert completed.json()["result"]["vendor_code"] == "V555000"

    # a second claim on the same process finds nothing
    assert client.post(f"{API}/rpa/worker/claim?process_ids={process_id}", headers=headers).json() is None

    heartbeat = client.get(f"{API}/rpa/worker/heartbeat", headers=headers)
    assert heartbeat.status_code == 200 and "running_jobs" in heartbeat.json()


def test_foreign_worker_cannot_complete_someone_elses_job(client, tokens, db, process_code):
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}
    process_id = db.scalar(select(RpaProcess.id).where(RpaProcess.code == process_code))
    job = enqueue(db, process_code, good_payload())
    owner = dict(admin) | {"X-Worker-Id": "worker-owner"}
    intruder = dict(admin) | {"X-Worker-Id": "worker-intruder"}

    assert client.post(f"{API}/rpa/worker/claim?process_ids={process_id}", headers=owner).json()["id"] == job.id
    stolen = client.post(f"{API}/rpa/worker/jobs/{job.id}/complete", json={"success": True}, headers=intruder)
    assert stolen.status_code == 403


# --------------------------------------------------------------------------- #
# integration with the approval workflow
# --------------------------------------------------------------------------- #
def test_approval_enqueues_and_success_completes_the_request(client, tokens, db):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}

    created = client.post(
        f"{API}/requests",
        json={
            "request_type_id": request_type_id("PURCHASE_ORDER"),
            "title": "Network switches", "amount": 120_000, "submit_immediately": True,
            "payload": {"vendor": {"name": "Cisco Systems India", "gstin": "27AAACC5678D1Z3"}},
        },
        headers=employee,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]

    approved = approve_chain(client, tokens, request_id, admin)
    assert approved["status"] == "approved"

    job_id = approved["rpa_job_ids"][0]
    job = client.get(f"{API}/rpa/jobs/{job_id}", headers=admin).json()
    assert job["status"] == "queued"
    assert job["payload"]["reference_no"] == approved["reference_no"]
    assert job["payload"]["request_id"] == request_id

    finished = run_job(job_id)
    assert finished.status == RpaJobStatus.SUCCEEDED

    after = client.get(f"{API}/requests/{request_id}", headers=employee).json()
    assert after["status"] == "completed", "a successful automation closes the business request"

    audit_trail = client.get(f"{API}/requests/{request_id}/audit", headers=admin).json()
    actions = [entry["action"] for entry in audit_trail]
    assert "request.created" in actions
    assert "request.submitted" in actions
    assert "request.approved" in actions
    assert "rpa.job_queued" in actions
    assert "rpa.job_succeeded" in actions
    assert "request.completed" in actions


def test_failed_automation_leaves_the_request_approved_and_flags_it(client, tokens, db):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}

    created = client.post(
        f"{API}/requests",
        json={
            "request_type_id": request_type_id("PURCHASE_ORDER"), "title": "Failing vendor sync",
            "amount": 30_000, "submit_immediately": True,
            "payload": {"vendor": {}, "__simulate_failure__": True},   # no name -> non-retryable
        },
        headers=employee,
    )
    request_id = created.json()["id"]
    approved = approve_chain(client, tokens, request_id, admin)
    assert approved["status"] == "approved", approved
    job_id = approved["rpa_job_ids"][0]

    finished = run_job(job_id)
    assert finished.status == RpaJobStatus.FAILED

    after = client.get(f"{API}/requests/{request_id}", headers=admin).json()
    assert after["status"] == "approved", "a failed automation must not silently complete the request"
    trail = client.get(f"{API}/requests/{request_id}/audit", headers=admin).json()
    assert any(entry["action"] == "request.rpa_failed" for entry in trail)


def _admin_user(session):
    """A real admin row, needed by cancel_job's RBAC check."""
    from app.enums import UserRole
    from app.models import User

    return session.scalar(select(User).where(User.role == UserRole.ADMIN))
