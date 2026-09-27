"""Pluggable automation handlers.

Each handler is a plain function:  (payload: dict, ctx: WorkerContext) -> dict
* return a dict  -> job marked SUCCEEDED with that dict as the result
* raise          -> job retried with exponential backoff (or dead-lettered)
* raise NonRetryableError -> job fails immediately, no retry

`ctx` gives the handler a cancellation check and a logger so long automations can
be stopped politely between steps instead of being killed mid-write.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable


class NonRetryableError(Exception):
    """Business-rule failure: retrying will never help (bad data, rejected vendor)."""


class CancelledError_(Exception):
    """Raised internally when a cancellation checkpoint trips."""


@dataclass
class WorkerContext:
    job_id: int
    job_ref: str
    attempt: int
    worker_id: str
    config: dict[str, Any]

    log: Callable[[str, str | None, str], None] | None = None   # (message, step, level)
    check_cancelled: Callable[[], bool] | None = None            # True -> stop now

    def info(self, message: str, step: str | None = None) -> None:
        if self.log:
            self.log(message, step, "INFO")

    def warn(self, message: str, step: str | None = None) -> None:
        if self.log:
            self.log(message, step, "WARN")

    def checkpoint(self, step: str) -> None:
        """Call between long steps: logs progress and honours cancellation."""
        self.info(f"checkpoint: {step}", step)
        if self.check_cancelled and self.check_cancelled():
            raise CancelledError_(f"cancelled at step '{step}'")


# --------------------------------------------------------------------------- #
# handlers
# --------------------------------------------------------------------------- #
def create_vendor_in_sap(payload: dict, ctx: WorkerContext) -> dict:
    """Simulates creating a vendor master record in SAP after PO approval."""
    ctx.checkpoint("validate_payload")
    vendor = payload.get("vendor") or {}
    name = vendor.get("name") or payload.get("vendor_name")
    gstin = vendor.get("gstin") or payload.get("gstin")
    if not name:
        raise NonRetryableError("Payload has no vendor name - cannot create vendor master")
    if gstin and len(str(gstin)) != 15:
        raise NonRetryableError(f"Invalid GSTIN '{gstin}' (expected 15 characters)")

    ctx.checkpoint("duplicate_check")
    duplicate = bool(payload.get("__simulate_duplicate__"))
    if duplicate:
        raise NonRetryableError("Vendor already exists in SAP (duplicate GSTIN)")

    ctx.checkpoint("call_sap_api")
    # A flaky downstream system is the classic reason retries exist.
    if payload.get("__simulate_failure__"):
        raise RuntimeError("SAP API returned 503 Service Unavailable")
    time.sleep(random.uniform(0.05, 0.2))

    ctx.checkpoint("write_back_reference")
    vendor_code = f"V{random.randint(100000, 999999)}"
    ctx.info(f"Vendor created with code {vendor_code}", "done")
    return {
        "vendor_code": vendor_code,
        "vendor_name": name,
        "gstin": gstin,
        "system": "SAP",
        "reference_no": payload.get("reference_no"),
        "attempts_used": ctx.attempt,
    }


def generate_monthly_report(payload: dict, ctx: WorkerContext) -> dict:
    """Simulates an RPA bot that pulls data, builds a report and mails it."""
    ctx.checkpoint("fetch_source_data")
    rows = int(payload.get("row_count") or random.randint(120, 900))
    if payload.get("__simulate_failure__"):
        raise RuntimeError("Source database connection timed out")

    ctx.checkpoint("render_report")
    time.sleep(random.uniform(0.05, 0.15))

    ctx.checkpoint("email_distribution")
    recipients = payload.get("recipients") or ["finance@erp.local"]
    ctx.info(f"Report mailed to {len(recipients)} recipient(s)", "email")
    return {"report": payload.get("report") or "monthly_finance.xlsx", "rows": rows, "recipients": recipients}


def sync_leave_balance(payload: dict, ctx: WorkerContext) -> dict:
    """Simulates pushing an approved leave request into the HRMS."""
    ctx.checkpoint("validate_leave")
    days = float(payload.get("days") or 0)
    if days <= 0:
        raise NonRetryableError("Leave days must be greater than zero")
    if days > 30:
        raise NonRetryableError("Leave longer than 30 days needs HR intervention")

    ctx.checkpoint("post_to_hrms")
    if payload.get("__simulate_failure__"):
        raise RuntimeError("HRMS rejected the session token (transient)")
    return {"employee_email": payload.get("requester_email"), "days_posted": days, "system": "HRMS"}


def archive_request_documents(payload: dict, ctx: WorkerContext) -> dict:
    ctx.checkpoint("collect_documents")
    ctx.checkpoint("upload_to_archive")
    if payload.get("__simulate_failure__"):
        raise RuntimeError("Object storage unreachable")
    return {"archived": True, "reference_no": payload.get("reference_no"), "bucket": ctx.config.get("bucket", "erp-archive")}


HANDLERS: dict[str, Callable[[dict, WorkerContext], dict]] = {
    "app.worker.handlers.create_vendor_in_sap": create_vendor_in_sap,
    "app.worker.handlers.generate_monthly_report": generate_monthly_report,
    "app.worker.handlers.sync_leave_balance": sync_leave_balance,
    "app.worker.handlers.archive_request_documents": archive_request_documents,
}


def resolve(handler_path: str) -> Callable[[dict, WorkerContext], dict]:
    """Look a handler up by the dotted path stored on RpaProcess.handler."""
    if handler_path in HANDLERS:
        return HANDLERS[handler_path]
    # fall back to a real dynamic import (so new handlers need no code change here)
    module_path, _, func_name = handler_path.rpartition(".")
    if not module_path:
        raise NonRetryableError(f"Malformed handler path '{handler_path}'")
    import importlib

    try:
        module = importlib.import_module(module_path)
        return getattr(module, func_name)
    except (ImportError, AttributeError) as exc:
        raise NonRetryableError(f"Handler '{handler_path}' could not be loaded: {exc}") from exc
