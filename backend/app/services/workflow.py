"""Approval workflow service - the state machine of the ERP.

    DRAFT --submit--> PENDING_APPROVAL --approve(step n)--> ... --final approve--> APPROVED --rpa--> COMPLETED
                          |                                     |
                          +---reject(any step)---> REJECTED       +---cancel/withdraw---> CANCELLED

Design notes worth mentioning in an interview:
* Every transition validates the *current* status first (no illegal moves),
  bumps `version` (optimistic locking) and writes an audit row in the same
  transaction - so the DB is never in a half-updated state.
* The approval chain is data-driven (RequestType.approval_chain_json), which
  means business can add a new workflow level without a deploy.
* Amount-based escalation: a level with `min_amount` only activates when the
  request crosses that threshold (e.g. >50k needs Finance sign-off).
"""
from __future__ import annotations

import json
import random
from datetime import timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import audit
from app.core.helpers import bad_request, conflict, forbidden, not_found
from app.enums import RequestStatus, StepStatus, UserRole
from app.models import ApprovalRequest, ApprovalStep, Department, RequestType, User
from app.models.base import utcnow
from app.serializers import dumps, loads

# Legal transitions. Anything not listed here is rejected with HTTP 409.
TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.DRAFT: {RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL, RequestStatus.CANCELLED},
    RequestStatus.SUBMITTED: {RequestStatus.PENDING_APPROVAL, RequestStatus.CANCELLED, RequestStatus.REJECTED},
    RequestStatus.PENDING_APPROVAL: {
        RequestStatus.PENDING_APPROVAL,
        RequestStatus.APPROVED,
        RequestStatus.REJECTED,
        RequestStatus.CANCELLED,
    },
    # an approved-but-not-yet-executed request can still be withdrawn
    RequestStatus.APPROVED: {RequestStatus.COMPLETED, RequestStatus.CANCELLED},
    RequestStatus.REJECTED: set(),
    RequestStatus.CANCELLED: set(),
    RequestStatus.COMPLETED: set(),
}

MANAGER_ROLES = {UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def generate_reference(db: Session, prefix: str = "REQ") -> str:
    """Human-friendly unique reference: REQ-2026-000123 (retries on collision)."""
    year = utcnow().year
    for _ in range(10):
        candidate = f"{prefix}-{year}-{random.randint(1, 999999):06d}"
        exists = db.scalar(select(ApprovalRequest.id).where(ApprovalRequest.reference_no == candidate))
        if not exists:
            return candidate
    raise conflict("Could not allocate a unique reference number, please retry")


def reload_request(db: Session, request_id: int) -> ApprovalRequest:
    """Re-read a request AFTER a commit.

    `expire_on_commit=False` keeps identity-map objects usable, but it also means
    a relationship loaded before the transaction (e.g. `rpa_jobs`) would keep
    serving stale data. Expiring the object first guarantees a fresh SELECT with
    eager-loaded collections - this is why an approval response shows the RPA job
    it just created.
    """
    db.expire_all()
    return load_request(db, request_id)


def load_request(db: Session, request_id: int) -> ApprovalRequest:
    req = db.scalar(
        select(ApprovalRequest)
        .options(
            selectinload(ApprovalRequest.request_type),
            selectinload(ApprovalRequest.requester),
            selectinload(ApprovalRequest.department),
            selectinload(ApprovalRequest.steps).selectinload(ApprovalStep.approver),
            selectinload(ApprovalRequest.comments),
            selectinload(ApprovalRequest.attachments),
            selectinload(ApprovalRequest.rpa_jobs),
        )
        .where(ApprovalRequest.id == request_id, ApprovalRequest.is_deleted.is_(False))
    )
    if req is None:
        raise not_found("Request")
    return req


def _assert_transition(req: ApprovalRequest, target: RequestStatus) -> None:
    if target not in TRANSITIONS.get(req.status, set()):
        raise conflict(f"Cannot move a request from '{req.status.value}' to '{target.value}'")


def _check_version(req: ApprovalRequest, expected_version: Optional[int]) -> None:
    if expected_version is not None and expected_version != req.version:
        raise conflict(
            f"Stale update: request is at version {req.version}, you submitted {expected_version}. "
            "Reload the record and try again."
        )


def pick_approver(db: Session, role_name: str, requester: User, amount: float) -> Optional[User]:
    """Resolve a concrete approver for a chain level.

    'manager'      -> the requester's reporting manager (fallback: any manager in the dept)
    'finance'      -> an ADMIN/SUPER_ADMIN (finance controller)
    'admin'        -> any ADMIN
    'super_admin'  -> any SUPER_ADMIN
    """
    if role_name == "manager":
        if requester.manager_id:
            mgr = db.get(User, requester.manager_id)
            if mgr and mgr.is_active and mgr.role in MANAGER_ROLES:
                return mgr
        stmt = (
            select(User)
            .where(
                User.role.in_([UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN]),
                User.is_active.is_(True),
                User.is_deleted.is_(False),
                User.id != requester.id,
            )
            .order_by(User.id)
        )
        if requester.department_id:
            stmt = stmt.where(User.department_id == requester.department_id)
        return db.scalars(stmt).first()

    wanted = {
        "finance": [UserRole.ADMIN, UserRole.SUPER_ADMIN],
        "admin": [UserRole.ADMIN, UserRole.SUPER_ADMIN],
        "super_admin": [UserRole.SUPER_ADMIN],
    }.get(role_name, [UserRole.ADMIN])

    return db.scalars(
        select(User)
        .where(User.role.in_(wanted), User.is_active.is_(True), User.is_deleted.is_(False))
        .order_by(User.id)
    ).first()


def build_approval_steps(db: Session, req: ApprovalRequest, rt: RequestType) -> list[ApprovalStep]:
    chain = loads(rt.approval_chain_json, []) or []
    steps: list[ApprovalStep] = []
    level_counter = 1
    for item in chain:
        min_amount = float(item.get("min_amount") or 0)
        if min_amount and req.amount < min_amount:
            continue  # escalation level not triggered for this amount
        approver = pick_approver(db, item.get("role", "manager"), req.requester, req.amount)
        if approver is None:
            continue
        if any(s.approver_id == approver.id for s in steps):
            continue  # same person should not approve twice
        steps.append(
            ApprovalStep(
                request_id=req.id,
                level=level_counter,
                approver_role=item.get("role", "manager"),
                approver_id=approver.id,
                status=StepStatus.PENDING,
            )
        )
        level_counter += 1
    return steps


def can_view(db: Session, req: ApprovalRequest, user: User) -> bool:
    if user.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return True
    if req.requester_id == user.id:
        return True
    if user.department_id and req.department_id == user.department_id and user.role == UserRole.MANAGER:
        return True
    return any(s.approver_id == user.id for s in req.steps)


def can_decide(req: ApprovalRequest, user: User) -> bool:
    if user.role == UserRole.SUPER_ADMIN:
        return True
    step = req.current_step
    return bool(step and step.approver_id == user.id)


# --------------------------------------------------------------------------- #
# transitions
# --------------------------------------------------------------------------- #
def create_request(
    db: Session,
    *,
    actor: User,
    request_type: RequestType,
    title: str,
    description: Optional[str],
    amount: float,
    currency: str,
    priority,
    department_id: Optional[int],
    payload: dict,
    http_request=None,
) -> ApprovalRequest:
    if not request_type.is_active:
        raise bad_request("This request type is deactivated")
    if request_type.amount_limit and amount > request_type.amount_limit:
        raise bad_request(
            f"Amount {amount} exceeds the limit ({request_type.amount_limit}) for {request_type.code}"
        )
    if department_id:
        dept = db.get(Department, department_id)
        if dept is None or dept.is_deleted:
            raise bad_request("Unknown department")

    req = ApprovalRequest(
        reference_no=generate_reference(db),
        title=title,
        description=description,
        amount=amount,
        currency=currency,
        priority=priority,
        status=RequestStatus.DRAFT,
        payload_json=dumps(payload),
        request_type_id=request_type.id,
        requester_id=actor.id,
        department_id=department_id or actor.department_id,
        version=1,
    )
    db.add(req)
    db.flush()

    audit.record(
        db,
        entity_type="approval_request",
        entity_id=req.id,
        action="request.created",
        actor=actor,
        request_id=req.id,
        new_values={"reference_no": req.reference_no, "title": title, "amount": amount, "status": "draft"},
        http_request=http_request,
    )
    return req


def submit_request(db: Session, *, req: ApprovalRequest, actor: User, http_request=None) -> ApprovalRequest:
    # Only a DRAFT can enter the workflow. (PENDING_APPROVAL -> PENDING_APPROVAL
    # stays legal in TRANSITIONS because intermediate approvals keep that status.)
    if req.status != RequestStatus.DRAFT:
        raise conflict(f"Only a DRAFT request can be submitted (this one is '{req.status.value}')")
    _assert_transition(req, RequestStatus.PENDING_APPROVAL)

    rt = req.request_type
    # Auto-approval is a *policy* decision and short-circuits the document rule:
    # a INR 1,200 cab fare under the auto-approve threshold needs no receipt.
    auto_approve = rt.auto_approve_below is not None and req.amount <= rt.auto_approve_below
    if rt.requires_attachment and not req.attachments and not auto_approve:
        raise bad_request(f"{rt.code} requires at least one attachment before submission")

    old_status = req.status
    steps = build_approval_steps(db, req, rt)

    now = utcnow()
    req.submitted_at = now
    req.sla_due_at = now + timedelta(hours=rt.sla_hours or 0) if rt.sla_hours else None
    req.version += 1

    if auto_approve or not steps:
        # Nothing to approve -> straight to APPROVED (policy: auto-approve small amounts)
        req.status = RequestStatus.APPROVED
        req.current_step_level = 0
        req.closed_at = now
        for s in steps:
            s.status = StepStatus.SKIPPED
        db.add_all(steps)
        audit.record(
            db, entity_type="approval_request", entity_id=req.id, action="request.auto_approved",
            actor=actor, request_id=req.id,
            old_values={"status": old_status.value}, new_values={"status": "approved"},
            notes="Auto-approved: amount below threshold or no approver configured",
            http_request=http_request,
        )
        db.flush()
        _maybe_trigger_rpa(db, req=req, actor=actor, http_request=http_request)
        return req

    db.add_all(steps)
    req.status = RequestStatus.PENDING_APPROVAL
    req.current_step_level = steps[0].level
    for s in steps:
        s.notified_at = now
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.submitted",
        actor=actor, request_id=req.id,
        old_values={"status": old_status.value},
        new_values={"status": "pending_approval", "steps": [s.approver_id for s in steps]},
        http_request=http_request,
    )
    db.flush()
    return req


def approve_request(
    db: Session, *, req: ApprovalRequest, actor: User, remarks: Optional[str],
    expected_version: Optional[int] = None, http_request=None,
) -> ApprovalRequest:
    if req.status not in (RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL):
        raise conflict(f"Request is '{req.status.value}' and can no longer be approved")
    _check_version(req, expected_version)
    if not can_decide(req, actor):
        raise forbidden("You are not the current approver for this request")

    step = req.current_step
    assert step is not None
    now = utcnow()
    step.status = StepStatus.APPROVED
    step.remarks = remarks
    step.decided_at = now

    remaining = [s for s in req.steps if s.status == StepStatus.PENDING and s.level > step.level]
    req.version += 1

    if remaining:
        req.current_step_level = min(s.level for s in remaining)
        audit.record(
            db, entity_type="approval_request", entity_id=req.id, action="approval.step_approved",
            actor=actor, request_id=req.id,
            old_values={"step_level": step.level, "step_status": "pending"},
            new_values={"step_level": step.level, "step_status": "approved", "next_level": req.current_step_level},
            notes=remarks, http_request=http_request,
        )
        db.flush()
        return req

    # final approval
    _assert_transition(req, RequestStatus.APPROVED)
    req.status = RequestStatus.APPROVED
    req.current_step_level = 0
    req.closed_at = now
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.approved",
        actor=actor, request_id=req.id,
        old_values={"status": RequestStatus.PENDING_APPROVAL.value},
        new_values={"status": "approved", "remarks": remarks},
        http_request=http_request,
    )
    db.flush()
    _maybe_trigger_rpa(db, req=req, actor=actor, http_request=http_request)
    return req


def reject_request(
    db: Session, *, req: ApprovalRequest, actor: User, reason: str,
    expected_version: Optional[int] = None, http_request=None,
) -> ApprovalRequest:
    if req.status not in (RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL):
        raise conflict(f"Request is '{req.status.value}' and can no longer be rejected")
    _check_version(req, expected_version)
    if not (can_decide(req, actor) or actor.role == UserRole.SUPER_ADMIN):
        raise forbidden("You are not the current approver for this request")
    if not reason or len(reason.strip()) < 3:
        raise bad_request("A rejection reason (min 3 characters) is mandatory")

    _assert_transition(req, RequestStatus.REJECTED)
    now = utcnow()
    step = req.current_step
    if step:
        step.status = StepStatus.REJECTED
        step.remarks = reason
        step.decided_at = now
    for s in req.steps:
        if s.status == StepStatus.PENDING and (not step or s.level > step.level):
            s.status = StepStatus.SKIPPED

    old_status = req.status
    req.status = RequestStatus.REJECTED
    req.rejection_reason = reason.strip()
    req.current_step_level = 0
    req.closed_at = now
    req.version += 1

    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.rejected",
        actor=actor, request_id=req.id,
        old_values={"status": old_status.value},
        new_values={"status": "rejected", "reason": reason},
        http_request=http_request,
    )
    db.flush()
    return req


def cancel_request(
    db: Session, *, req: ApprovalRequest, actor: User, reason: Optional[str] = None, http_request=None
) -> ApprovalRequest:
    """Withdraw (requester) or cancel (admin)."""
    is_owner = req.requester_id == actor.id
    if not (is_owner or actor.is_admin):
        raise forbidden("Only the requester or an admin can cancel this request")
    if req.status in (RequestStatus.REJECTED, RequestStatus.CANCELLED, RequestStatus.COMPLETED):
        raise conflict(f"Request is already '{req.status.value}'")

    _assert_transition(req, RequestStatus.CANCELLED)
    now = utcnow()
    old_status = req.status
    for s in req.steps:
        if s.status == StepStatus.PENDING:
            s.status = StepStatus.SKIPPED
    req.status = RequestStatus.CANCELLED
    req.rejection_reason = reason
    req.closed_at = now
    req.current_step_level = 0
    req.version += 1

    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.cancelled",
        actor=actor, request_id=req.id,
        old_values={"status": old_status.value},
        new_values={"status": "cancelled", "reason": reason},
        http_request=http_request,
    )
    db.flush()

    # Cancel anything still waiting in the RPA queue for this request.
    from app.services import rpa_queue  # local import: avoids a circular dependency

    rpa_queue.cancel_jobs_for_request(db, req.id, actor=actor, reason=reason, http_request=http_request)
    return req


def mark_completed(db: Session, *, req: ApprovalRequest, actor: Optional[User], http_request=None) -> ApprovalRequest:
    """Called by the RPA layer once the automated post-approval job succeeds."""
    if req.status != RequestStatus.APPROVED:
        return req
    _assert_transition(req, RequestStatus.COMPLETED)
    req.status = RequestStatus.COMPLETED
    req.version += 1
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.completed",
        actor=actor, request_id=req.id,
        old_values={"status": RequestStatus.APPROVED.value},
        new_values={"status": "completed"},
        notes="Automated (RPA) execution finished successfully",
        http_request=http_request,
    )
    db.flush()
    return req


def _maybe_trigger_rpa(db: Session, *, req: ApprovalRequest, actor: User, http_request=None) -> None:
    """If the request type is wired to an RPA process, enqueue a job.

    The API returns immediately - the worker picks the job up asynchronously
    (queue-based execution), which is exactly the point of the orchestration.
    """
    from app.services import rpa_queue  # local import: avoids a circular dependency

    rt = req.request_type
    if not rt or not rt.rpa_process_id:
        return
    payload = loads(req.payload_json, {})
    payload.update(
        {
            "request_id": req.id,
            "reference_no": req.reference_no,
            "amount": req.amount,
            "currency": req.currency,
            "requester_email": req.requester.email if req.requester else None,
            "approved_by": actor.email,
        }
    )
    rpa_queue.enqueue(
        db,
        process_id=rt.rpa_process_id,
        payload=payload,
        priority=1 if req.priority.value == "critical" else 5,
        request_id=req.id,
        idempotency_key=f"request:{req.id}:approved",
        actor=actor,
        http_request=http_request,
        notes=f"Auto-enqueued on approval of {req.reference_no}",
    )


def add_comment(db: Session, *, req: ApprovalRequest, actor: User, body: str, http_request=None):
    from app.models import RequestComment

    comment = RequestComment(request_id=req.id, user_id=actor.id, body=body.strip())
    db.add(comment)
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="comment.added",
        actor=actor, request_id=req.id, new_values={"body": body[:200]}, http_request=http_request,
    )
    db.flush()
    return comment


def request_snapshot(req: ApprovalRequest) -> dict:
    return {
        "id": req.id,
        "status": req.status.value,
        "amount": req.amount,
        "version": req.version,
        "steps": [{"level": s.level, "status": s.status.value} for s in req.steps],
    }


__all__ = [
    "TRANSITIONS",
    "reload_request",
    "add_comment",
    "approve_request",
    "build_approval_steps",
    "can_decide",
    "can_view",
    "cancel_request",
    "create_request",
    "generate_reference",
    "load_request",
    "mark_completed",
    "reject_request",
    "request_snapshot",
    "submit_request",
]

# `json` is used by callers through dumps/loads; kept imported for convenience.
_ = json
