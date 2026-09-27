"""ORM -> Pydantic serialisation.

Kept in one module so the routers stay thin and the joins are explicit
(selectinload instead of N+1 lazy loads).
"""
from __future__ import annotations

import json
from typing import Any

from app.enums import RequestStatus
from app.models import (
    ApprovalRequest,
    ApprovalStep,
    AuditLog,
    RequestComment,
    RequestType,
    RpaJob,
    RpaProcess,
    User,
)
from app.schemas.common import AuditLogRead
from app.schemas.request import (
    ApprovalChainLevel,
    ApprovalStepRead,
    CommentRead,
    RequestListRead,
    RequestRead,
    RequestTypeRead,
)
from app.schemas.rpa import RpaJobRead, RpaProcessRead


def loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default if default is not None else {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default if default is not None else {}


def dumps(value: Any) -> str:
    return json.dumps(value or {}, default=str, ensure_ascii=False)


# --------------------------------------------------------------------------- #
def request_type_read(rt: RequestType) -> RequestTypeRead:
    chain = [ApprovalChainLevel(**item) for item in loads(rt.approval_chain_json, [])]
    return RequestTypeRead(
        id=rt.id,
        code=rt.code,
        name=rt.name,
        description=rt.description,
        amount_limit=rt.amount_limit,
        sla_hours=rt.sla_hours,
        requires_attachment=rt.requires_attachment,
        auto_approve_below=rt.auto_approve_below,
        approval_chain=chain,
        rpa_process_id=rt.rpa_process_id,
        is_active=rt.is_active,
        created_at=rt.created_at,
    )


def step_read(step: ApprovalStep) -> ApprovalStepRead:
    return ApprovalStepRead(
        id=step.id,
        level=step.level,
        approver_role=step.approver_role,
        approver_id=step.approver_id,
        approver_name=step.approver.full_name if step.approver else None,
        status=step.status,
        remarks=step.remarks,
        decided_at=step.decided_at,
    )


def comment_read(c: RequestComment) -> CommentRead:
    return CommentRead(
        id=c.id, body=c.body, created_at=c.created_at, user_id=c.user_id,
        user_name=c.user.full_name if c.user else None,
    )


def request_list_read(req: ApprovalRequest) -> RequestListRead:
    return RequestListRead(
        id=req.id,
        reference_no=req.reference_no,
        title=req.title,
        amount=req.amount,
        currency=req.currency,
        priority=req.priority,
        status=req.status,
        current_step_level=req.current_step_level,
        sla_due_at=req.sla_due_at,
        created_at=req.created_at,
        submitted_at=req.submitted_at,
        closed_at=req.closed_at,
        request_type_code=req.request_type.code if req.request_type else None,
        request_type_name=req.request_type.name if req.request_type else None,
        requester_id=req.requester_id,
        requester_name=req.requester.full_name if req.requester else None,
        department_name=req.department.name if req.department else None,
    )


def request_read(req: ApprovalRequest) -> RequestRead:
    base = request_list_read(req).model_dump()
    return RequestRead(
        **base,
        description=req.description,
        payload=loads(req.payload_json, {}),
        rejection_reason=req.rejection_reason,
        is_sla_breached=req.is_sla_breached,
        version=req.version,
        steps=[step_read(s) for s in sorted(req.steps, key=lambda s: s.level)],
        comments=[comment_read(c) for c in req.comments],
        attachments=list(req.attachments),
        rpa_job_ids=[j.id for j in req.rpa_jobs],
    )


def audit_read(log: AuditLog) -> AuditLogRead:
    return AuditLogRead(
        id=log.id,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        action=log.action,
        actor_id=log.actor_id,
        actor_role=log.actor_role,
        request_id=log.request_id,
        old_values=loads(log.old_values_json, None),
        new_values=loads(log.new_values_json, None),
        notes=log.notes,
        ip_address=log.ip_address,
        created_at=log.created_at,
    )


def rpa_process_read(p: RpaProcess, jobs_total: int = 0, jobs_failed: int = 0) -> RpaProcessRead:
    return RpaProcessRead(
        id=p.id,
        code=p.code,
        name=p.name,
        description=p.description,
        handler=p.handler,
        max_attempts=p.max_attempts,
        backoff_base_seconds=p.backoff_base_seconds,
        timeout_seconds=p.timeout_seconds,
        config=loads(p.config_json, {}),
        is_active=p.is_active,
        created_at=p.created_at,
        jobs_total=jobs_total,
        jobs_failed=jobs_failed,
    )


def rpa_job_read(job: RpaJob) -> RpaJobRead:
    return RpaJobRead(
        id=job.id,
        job_ref=job.job_ref,
        process_id=job.process_id,
        process_code=job.process.code if job.process else None,
        process_name=job.process.name if job.process else None,
        request_id=job.request_id,
        request_reference=job.request.reference_no if job.request else None,
        status=job.status,
        priority=job.priority,
        payload=loads(job.payload_json, {}),
        result=loads(job.result_json, None),
        error_message=job.error_message,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        next_run_at=job.next_run_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_seconds=job.duration_seconds,
        worker_id=job.worker_id,
        created_at=job.created_at,
    )


def user_brief(u: User | None) -> dict[str, Any]:
    if not u:
        return {}
    return {"id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role.value}


CLOSED_STATUSES = {
    RequestStatus.APPROVED,
    RequestStatus.REJECTED,
    RequestStatus.CANCELLED,
    RequestStatus.COMPLETED,
}
