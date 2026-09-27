"""RPA worker process.

    # default: talks straight to the database (same host as the API)
    python -m app.worker --workers 2

    # or: a fully decoupled worker that only uses the REST API (deployable
    #       anywhere, e.g. a separate VM / container with no DB access)
    python -m app.worker --mode http --api-url http://localhost:8000 \
        --worker-token <admin JWT> --workers 1

Loop per worker:
    claim job (atomic) -> run handler with cancellation checkpoints ->
    report success / retry (exponential backoff) / dead-letter -> repeat.

Graceful shutdown on SIGINT/SIGTERM: the current job is finished (or released),
no half-written state, no orphaned leases.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from typing import Any, Optional

from app.config import settings
from app.database import SessionLocal
from app.enums import RpaJobStatus
from app.models import RpaJob, RpaProcess
from app.services import rpa_queue
from app.worker.handlers import CancelledError_, NonRetryableError, WorkerContext, resolve

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
)
logger = logging.getLogger("rpa.worker")

_shutdown = threading.Event()


def install_signal_handlers() -> None:
    def _handler(signum, _frame):
        logger.warning("Signal %s received - finishing current job then exiting", signum)
        _shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handler)
        except ValueError:  # not in main thread
            pass


def default_worker_id() -> str:
    return f"worker-{socket.gethostname()}-{os.getpid()}"


# --------------------------------------------------------------------------- #
# DB-backed worker (default)
# --------------------------------------------------------------------------- #
def process_one_db(worker_id: str) -> bool:
    """Claim and execute a single job. Returns False when the queue was empty."""
    db = SessionLocal()
    try:
        job = rpa_queue.claim_next_job(db, worker_id=worker_id)
        if job is None:
            return False

        process: RpaProcess = job.process
        payload = json.loads(job.payload_json or "{}")
        logger.info("[%s] picked up %s (%s) attempt %s/%s",
                    worker_id, job.job_ref, process.code, job.attempts, job.max_attempts)

        def _log(message: str, step: Optional[str] = None, level: str = "INFO") -> None:
            rpa_queue.log(db, job, attempt=job.attempts, level=level, step=step,
                          message=message, worker_id=worker_id)
            db.commit()

        def _cancelled() -> bool:
            db.expire_all()
            abort, reason = rpa_queue.should_abort(db, job.id)
            if abort:
                logger.warning("[%s] %s -> %s", worker_id, job.job_ref, reason)
            return abort

        ctx = WorkerContext(
            job_id=job.id, job_ref=job.job_ref, attempt=job.attempts, worker_id=worker_id,
            config=json.loads(process.config_json or "{}"), log=_log, check_cancelled=_cancelled,
        )

        try:
            handler = resolve(process.handler)
            result = handler(payload, ctx)
            rpa_queue.complete_job(db, job=job, worker_id=worker_id, success=True, result=result)
            logger.info("[%s] %s SUCCEEDED", worker_id, job.job_ref)
        except CancelledError_ as exc:
            rpa_queue.complete_job(db, job=job, worker_id=worker_id, success=False,
                                   error=str(exc), retryable=False)
            logger.warning("[%s] %s CANCELLED", worker_id, job.job_ref)
        except NonRetryableError as exc:
            rpa_queue.complete_job(db, job=job, worker_id=worker_id, success=False,
                                   error=str(exc), retryable=False)
            logger.error("[%s] %s FAILED (non-retryable): %s", worker_id, job.job_ref, exc)
        except Exception as exc:  # transient -> retry with backoff
            rpa_queue.complete_job(db, job=job, worker_id=worker_id, success=False,
                                   error=f"{type(exc).__name__}: {exc}", retryable=True)
            logger.error("[%s] %s attempt failed, retry scheduled: %s", worker_id, job.job_ref, exc)
        return True
    finally:
        db.close()


def run_db_workers(concurrency: int, once: bool = False) -> None:
    install_signal_handlers()
    stop = threading.Event()

    def _loop(name: str) -> None:
        logger.info("worker '%s' started (db mode)", name)
        empty_polls = 0
        while not _shutdown.is_set() and not stop.is_set():
            try:
                with SessionLocal() as sweep_db:
                    rpa_queue.requeue_stale_leases(sweep_db)   # crash recovery
                busy = process_one_db(name)
            except Exception:
                logger.exception("worker '%s' crashed on an iteration", name)
                busy = False

            if busy:
                empty_polls = 0
                continue
            if once:
                # Another worker may have just won the race for the last job, so
                # require a few consecutive empty polls before declaring "drained".
                empty_polls += 1
                if empty_polls >= 4:
                    stop.set()
                    break
                time.sleep(0.15)
                continue
            _shutdown.wait(settings.RPA_POLL_INTERVAL_SECONDS)
        logger.info("worker '%s' stopped", name)

    threads = [threading.Thread(target=_loop, args=(f"{default_worker_id()}-{i}",), daemon=True)
               for i in range(concurrency)]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except KeyboardInterrupt:
        _shutdown.set()


# --------------------------------------------------------------------------- #
# HTTP-backed worker (decoupled deployment)
# --------------------------------------------------------------------------- #
def process_one_http(api_url: str, token: str, worker_id: str, once: bool = False) -> bool:
    import httpx

    headers = {"Authorization": f"Bearer {token}", "X-Worker-Id": worker_id}
    base = api_url.rstrip("/") + settings.API_V1_PREFIX + "/rpa"
    try:
        with httpx.Client(timeout=30) as client:
            claimed = client.post(f"{base}/worker/claim", headers=headers)
            claimed.raise_for_status()
            job = claimed.json()
            if not job:
                return False

            job_id = job["id"]
            process = None
            with SessionLocal() as db:  # handler metadata only; execution is remote
                process = db.get(RpaProcess, job["process_id"])
            handler_path = process.handler if process else job.get("handler", "")
            payload = job.get("payload") or {}

            def _log(message: str, step: Optional[str] = None, level: str = "INFO") -> None:
                client.post(f"{base}/worker/jobs/{job_id}/log", headers=headers,
                            json={"level": level, "step": step, "message": message})

            def _cancelled() -> bool:
                current = client.get(f"{base}/jobs/{job_id}", headers=headers).json()
                return current.get("status") == RpaJobStatus.CANCELLED.value or bool(
                    (current.get("payload") or {}).get(rpa_queue.CANCELLED_FLAG_KEY)
                )

            ctx = WorkerContext(job_id=job_id, job_ref=job["job_ref"], attempt=job["attempts"],
                                worker_id=worker_id, config={}, log=_log, check_cancelled=_cancelled)
            try:
                result = resolve(handler_path)(payload, ctx)
                client.post(f"{base}/worker/jobs/{job_id}/complete", headers=headers,
                            json={"success": True, "result": result}).raise_for_status()
            except NonRetryableError as exc:
                client.post(f"{base}/worker/jobs/{job_id}/complete", headers=headers,
                            json={"success": False, "error": str(exc), "retryable": False})
            except CancelledError_ as exc:
                client.post(f"{base}/worker/jobs/{job_id}/complete", headers=headers,
                            json={"success": False, "error": str(exc), "retryable": False})
            except Exception as exc:
                client.post(f"{base}/worker/jobs/{job_id}/complete", headers=headers,
                            json={"success": False, "error": f"{type(exc).__name__}: {exc}", "retryable": True})
            return True
    except Exception:
        logger.exception("http worker iteration failed")
        return False


def run_http_workers(api_url: str, token: str, concurrency: int, once: bool = False) -> None:
    install_signal_handlers()

    def _loop(index: int) -> None:
        name = f"{default_worker_id()}-http-{index}"
        logger.info("worker '%s' started (http mode -> %s)", name, api_url)
        while not _shutdown.is_set():
            busy = process_one_http(api_url, token, name, once)
            if once and not busy:
                break
            if not busy:
                _shutdown.wait(settings.RPA_POLL_INTERVAL_SECONDS)

    threads = [threading.Thread(target=_loop, args=(i,), daemon=True) for i in range(concurrency)]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads) and not _shutdown.is_set():
            time.sleep(0.3)
    except KeyboardInterrupt:
        _shutdown.set()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="ERP RPA worker")
    parser.add_argument("--mode", choices=["db", "http"], default="db")
    parser.add_argument("--workers", type=int, default=settings.RPA_WORKER_CONCURRENCY)
    parser.add_argument("--api-url", default=os.getenv("API_URL", "http://localhost:8000"))
    parser.add_argument("--worker-token", default=os.getenv("WORKER_TOKEN", ""))
    parser.add_argument("--once", action="store_true", help="drain the queue once and exit (used by tests/CI)")
    args = parser.parse_args(argv)

    if args.mode == "http":
        if not args.worker_token:
            print("--worker-token is required in http mode", file=sys.stderr)
            return 2
        run_http_workers(args.api_url, args.worker_token, args.workers, once=args.once)
    else:
        run_db_workers(args.workers, once=args.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
