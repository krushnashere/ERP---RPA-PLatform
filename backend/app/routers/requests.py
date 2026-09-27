"""Business request endpoints: CRUD, joins, filtering, validation, workflow."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import selectinload

from app.auth import AdminUser, CurrentUser, DbSession, HttpRequest
from app.config import settings
from app.core import audit
from app.core.helpers import Page, PageParams, bad_request, conflict, forbidden, not_found, paginate
from app.enums import OPEN_STATUSES, Priority, RequestStatus, StepStatus, UserRole
from app.models import (
    ApprovalRequest,
    ApprovalStep,
    Attachment,
    AuditLog,
    Department,
    RequestType,
    User,
)
from app.models.base import utcnow
from app.schemas.common import AuditLogRead
from app.schemas.request import (
    ApprovalAction,
    CommentCreate,
    CommentRead,
    RejectAction,
    RequestCreate,
    RequestListRead,
    RequestRead,
    RequestUpdate,
)
from app.serializers import audit_read, comment_read, dumps, loads, request_list_read, request_read
from app.services import workflow

router = APIRouter(prefix="/requests", tags=["requests"])

SORTABLE = {
    "created_at": ApprovalRequest.created_at,
    "updated_at": ApprovalRequest.updated_at,
    "amount": ApprovalRequest.amount,
    "status": ApprovalRequest.status,
    "reference_no": ApprovalRequest.reference_no,
    "sla_due_at": ApprovalRequest.sla_due_at,
}

STATUS_VALUES = [s.value for s in RequestStatus]
PRIORITY_VALUES = [p.value for p in Priority]


# --------------------------------------------------------------------------- #
# query building (joins + filtering)
# --------------------------------------------------------------------------- #
def base_select(*columns) -> Select:
    """SELECT <columns> FROM approval_requests JOIN request_types JOIN users
    LEFT JOIN departments - the join used by every list/aggregate endpoint."""
    cols = columns or (ApprovalRequest,)
    return (
        select(*cols)
        .select_from(ApprovalRequest)
        .join(RequestType, ApprovalRequest.request_type_id == RequestType.id)
        .join(User, ApprovalRequest.requester_id == User.id)
        .outerjoin(Department, ApprovalRequest.department_id == Department.id)
        .where(ApprovalRequest.is_deleted.is_(False))
    )


def eager_request_select() -> Select:
    """Avoids N+1: the list serializer needs type / requester / department."""
    return base_select().options(
        selectinload(ApprovalRequest.request_type),
        selectinload(ApprovalRequest.requester),
        selectinload(ApprovalRequest.department),
    )


def apply_row_level_security(stmt: Select, actor: User) -> Select:
    """Row-level security:
    employee    -> own requests only
    manager     -> own + own department + anything they are an approver on
    admin/super -> everything
    """
    if actor.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN):
        return stmt
    conditions: list[ColumnElement] = [ApprovalRequest.requester_id == actor.id]
    if actor.role == UserRole.MANAGER:
        if actor.department_id:
            conditions.append(ApprovalRequest.department_id == actor.department_id)
        conditions.append(
            ApprovalRequest.id.in_(select(ApprovalStep.request_id).where(ApprovalStep.approver_id == actor.id))
        )
    return stmt.where(or_(*conditions))


def build_filters(
    *,
    statuses: Optional[list[str]],
    priorities: Optional[list[str]],
    request_type_id: Optional[int],
    department_id: Optional[int],
    requester_id: Optional[int],
    mine: Optional[str],
    search: Optional[str],
    min_amount: Optional[float],
    max_amount: Optional[float],
    created_from: Optional[datetime],
    created_to: Optional[datetime],
    sla_breached_only: bool,
    actor: User,
) -> list[ColumnElement]:
    """Turn validated query params into SQL predicates."""
    clauses: list[ColumnElement] = []

    if statuses:
        unknown = set(statuses) - set(STATUS_VALUES)
        if unknown:
            raise bad_request(f"Unknown status filter: {', '.join(sorted(unknown))}")
        clauses.append(ApprovalRequest.status.in_([RequestStatus(s) for s in statuses]))
    if priorities:
        unknown = set(priorities) - set(PRIORITY_VALUES)
        if unknown:
            raise bad_request(f"Unknown priority filter: {', '.join(sorted(unknown))}")
        clauses.append(ApprovalRequest.priority.in_([Priority(p) for p in priorities]))
    if request_type_id:
        clauses.append(ApprovalRequest.request_type_id == request_type_id)
    if department_id:
        clauses.append(ApprovalRequest.department_id == department_id)
    if requester_id:
        clauses.append(ApprovalRequest.requester_id == requester_id)
    if mine == "created":
        clauses.append(ApprovalRequest.requester_id == actor.id)
    elif mine == "approvals":
        clauses.append(
            ApprovalRequest.id.in_(
                select(ApprovalStep.request_id).where(
                    ApprovalStep.approver_id == actor.id, ApprovalStep.status == "pending"
                )
            )
        )
    if search:
        like = f"%{search.strip()}%"
        clauses.append(
            or_(
                ApprovalRequest.reference_no.ilike(like),
                ApprovalRequest.title.ilike(like),
                User.full_name.ilike(like),
                User.email.ilike(like),
            )
        )
    if min_amount is not None:
        clauses.append(ApprovalRequest.amount >= min_amount)
    if max_amount is not None:
        clauses.append(ApprovalRequest.amount <= max_amount)
    if min_amount is not None and max_amount is not None and min_amount > max_amount:
        raise bad_request("min_amount cannot be greater than max_amount")
    if created_from:
        clauses.append(ApprovalRequest.created_at >= created_from)
    if created_to:
        clauses.append(ApprovalRequest.created_at <= created_to)
    if sla_breached_only:
        now_naive = utcnow()
        clauses.append(
            and_(
                ApprovalRequest.sla_due_at.is_not(None),
                ApprovalRequest.sla_due_at < now_naive,
                ApprovalRequest.status.in_([RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL]),
            )
        )
    return clauses


# --------------------------------------------------------------------------- #
# collection endpoints (declared BEFORE /{request_id} so they are not shadowed)
# --------------------------------------------------------------------------- #
@router.get("", response_model=Page[RequestListRead], summary="List requests: filtering, search, sort, pagination")
def list_requests(
    actor: CurrentUser,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    status_filter: Annotated[Optional[list[str]], Query(alias="status")] = None,
    priority_filter: Annotated[Optional[list[str]], Query(alias="priority")] = None,
    request_type_id: Optional[int] = Query(None, ge=1),
    department_id: Optional[int] = Query(None, ge=1),
    requester_id: Optional[int] = Query(None, ge=1),
    mine: Optional[str] = Query(None, pattern="^(created|approvals|all)$"),
    search: Optional[str] = Query(None, max_length=80, description="reference no / title / requester"),
    min_amount: Optional[float] = Query(None, ge=0),
    max_amount: Optional[float] = Query(None, ge=0),
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    sla_breached_only: bool = False,
    sort_by: str = Query("created_at", pattern=f"^({'|'.join(SORTABLE)})$"),
    sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
) -> Page:
    clauses = build_filters(
        statuses=status_filter, priorities=priority_filter, request_type_id=request_type_id,
        department_id=department_id, requester_id=requester_id, mine=mine, search=search,
        min_amount=min_amount, max_amount=max_amount, created_from=created_from,
        created_to=created_to, sla_breached_only=sla_breached_only, actor=actor,
    )
    stmt = apply_row_level_security(eager_request_select(), actor)
    for clause in clauses:
        stmt = stmt.where(clause)

    column = SORTABLE[sort_by]
    stmt = stmt.order_by(
        column.desc() if sort_dir == "desc" else column.asc(), ApprovalRequest.id.desc()
    )
    return paginate(db, stmt, page, serializer=request_list_read)


@router.post("", response_model=RequestRead, status_code=status.HTTP_201_CREATED, summary="Create a request")
def create_request(body: RequestCreate, actor: CurrentUser, db: DbSession, request: HttpRequest) -> RequestRead:
    rt = db.scalar(
        select(RequestType).where(RequestType.id == body.request_type_id, RequestType.is_active.is_(True))
    )
    if rt is None:
        raise not_found("Active request type")

    req = workflow.create_request(
        db, actor=actor, request_type=rt, title=body.title, description=body.description,
        amount=body.amount, currency=body.currency, priority=body.priority,
        department_id=body.department_id, payload=body.payload, http_request=request,
    )
    if body.submit_immediately:
        req = workflow.submit_request(db, req=req, actor=actor, http_request=request)
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.get("/inbox", response_model=list[RequestListRead], summary="Requests waiting on MY approval")
def my_inbox(actor: CurrentUser, db: DbSession) -> list[RequestListRead]:
    rows = (
        db.scalars(
            eager_request_select()
            .join(ApprovalStep, ApprovalStep.request_id == ApprovalRequest.id)
            .where(
                ApprovalStep.approver_id == actor.id,
                ApprovalStep.status == "pending",
                ApprovalRequest.status.in_([RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL]),
            )
            .order_by(ApprovalStep.level.asc(), ApprovalRequest.created_at.asc())
        )
        .unique()
        .all()
    )
    return [request_list_read(r) for r in rows]


@router.get("/stats/summary", summary="Dashboard KPIs: counts by status, SLA breaches, ageing, value")
def summary_stats(actor: CurrentUser, db: DbSession) -> dict:
    # One scoped FROM clause shared by every aggregate, so the dashboard numbers
    # always agree with what the list endpoint returns for the same caller.
    scope = apply_row_level_security(
        base_select(ApprovalRequest.id, ApprovalRequest.status, ApprovalRequest.amount, ApprovalRequest.sla_due_at),
        actor,
    ).subquery()
    now = utcnow()
    open_statuses = [RequestStatus.SUBMITTED.value, RequestStatus.PENDING_APPROVAL.value]

    by_status = {s.value: 0 for s in RequestStatus}
    for status_value, count in db.execute(
        select(scope.c.status, func.count()).select_from(scope).group_by(scope.c.status)
    ).all():
        key = status_value.value if hasattr(status_value, "value") else str(status_value)
        by_status[key] = count

    breached = db.scalar(
        select(func.count())
        .select_from(scope)
        .where(
            scope.c.sla_due_at.is_not(None),
            scope.c.sla_due_at < now,
            scope.c.status.in_(open_statuses),
        )
    )
    open_value = db.scalar(
        select(func.coalesce(func.sum(scope.c.amount), 0.0))
        .select_from(scope)
        .where(scope.c.status.in_(open_statuses))
    )
    pending_for_me = db.scalar(
        select(func.count())
        .select_from(ApprovalStep)
        .where(ApprovalStep.approver_id == actor.id, ApprovalStep.status == StepStatus.PENDING)
    )

    by_department = db.execute(
        select(func.coalesce(Department.name, "Unassigned"), func.count(), func.coalesce(func.sum(ApprovalRequest.amount), 0.0))
        .select_from(ApprovalRequest)
        .outerjoin(Department, ApprovalRequest.department_id == Department.id)
        .where(ApprovalRequest.is_deleted.is_(False))
        .group_by(func.coalesce(Department.name, "Unassigned"))
        .order_by(func.count().desc())
        .limit(8)
    ).all()

    by_type = db.execute(
        select(RequestType.code, RequestType.name, func.count())
        .select_from(ApprovalRequest)
        .join(RequestType, ApprovalRequest.request_type_id == RequestType.id)
        .where(ApprovalRequest.is_deleted.is_(False))
        .group_by(RequestType.code, RequestType.name)
        .order_by(func.count().desc())
        .limit(8)
    ).all()

    trend = db.execute(
        select(func.date(ApprovalRequest.created_at), func.count())
        .select_from(ApprovalRequest)
        .where(
            ApprovalRequest.is_deleted.is_(False),
            ApprovalRequest.created_at >= now - timedelta(days=30),
        )
        .group_by(func.date(ApprovalRequest.created_at))
        .order_by(func.date(ApprovalRequest.created_at))
    ).all()

    return {
        "by_status": by_status,
        "total": sum(by_status.values()),
        "open": sum(by_status[s.value] for s in OPEN_STATUSES),
        "sla_breached": int(breached or 0),
        "open_value": round(float(open_value or 0), 2),
        "pending_for_me": int(pending_for_me or 0),
        "by_department": [
            {"department": name, "count": count, "amount": round(float(amount or 0), 2)}
            for name, count, amount in by_department
        ],
        "by_request_type": [{"code": code, "name": name, "count": count} for code, count, name in by_type],
        "trend_30d": [{"date": str(day), "count": count} for day, count in trend],
        "generated_at": utcnow().isoformat(),
    }


# --------------------------------------------------------------------------- #
# item endpoints
# --------------------------------------------------------------------------- #
@router.get("/{request_id}", response_model=RequestRead, summary="Full request: steps, comments, attachments, jobs")
def get_request(request_id: int, actor: CurrentUser, db: DbSession) -> RequestRead:
    req = workflow.load_request(db, request_id)
    if not workflow.can_view(db, req, actor):
        raise forbidden("You do not have access to this request")
    return request_read(req)


@router.patch("/{request_id}", response_model=RequestRead, summary="Edit a DRAFT request (optimistic locking)")
def update_request(
    request_id: int, body: RequestUpdate, actor: CurrentUser, db: DbSession, request: HttpRequest
) -> RequestRead:
    req = workflow.load_request(db, request_id)
    if req.requester_id != actor.id and not actor.is_admin:
        raise forbidden("Only the requester (or an admin) can edit this request")
    if req.status != RequestStatus.DRAFT:
        raise conflict(f"Only DRAFT requests can be edited (this one is '{req.status.value}')")
    workflow._check_version(req, body.expected_version)

    old = {
        "title": req.title, "amount": req.amount,
        "priority": req.priority.value, "description": req.description,
    }
    data = body.model_dump(exclude_unset=True, exclude={"expected_version"})
    if data.get("amount") and req.request_type.amount_limit and data["amount"] > req.request_type.amount_limit:
        raise bad_request(f"Amount exceeds the limit for {req.request_type.code}")
    payload = data.pop("payload", None)
    for field, value in data.items():
        setattr(req, field, value)
    if payload is not None:
        req.payload_json = dumps(payload)
    req.version += 1

    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.updated", actor=actor,
        request_id=req.id, old_values=old,
        new_values={k: v for k, v in {**data, **({"payload": payload} if payload is not None else {})}.items()},
        http_request=request,
    )
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.delete("/{request_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete a request (admin)")
def delete_request(request_id: int, actor: AdminUser, db: DbSession, request: HttpRequest) -> None:
    req = workflow.load_request(db, request_id)
    if req.status in OPEN_STATUSES and actor.role != UserRole.SUPER_ADMIN:
        raise conflict("Open requests can only be soft-deleted by a super admin")
    req.is_deleted = True
    req.deleted_at = utcnow()
    req.deleted_by = actor.id
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="request.deleted", actor=actor,
        request_id=req.id, old_values={"status": req.status.value}, http_request=request,
    )
    db.commit()


@router.post("/{request_id}/submit", response_model=RequestRead, summary="Submit a draft for approval")
def submit(request_id: int, actor: CurrentUser, db: DbSession, request: HttpRequest) -> RequestRead:
    req = workflow.load_request(db, request_id)
    if req.requester_id != actor.id and not actor.is_admin:
        raise forbidden("Only the requester can submit this request")
    workflow.submit_request(db, req=req, actor=actor, http_request=request)
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.post("/{request_id}/approve", response_model=RequestRead, summary="Approve the current step")
def approve(
    request_id: int, body: ApprovalAction, actor: CurrentUser, db: DbSession, request: HttpRequest
) -> RequestRead:
    req = workflow.load_request(db, request_id)
    workflow.approve_request(
        db, req=req, actor=actor, remarks=body.remarks,
        expected_version=body.expected_version, http_request=request,
    )
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.post("/{request_id}/reject", response_model=RequestRead, summary="Reject (reason is mandatory)")
def reject(
    request_id: int, body: RejectAction, actor: CurrentUser, db: DbSession, request: HttpRequest
) -> RequestRead:
    req = workflow.load_request(db, request_id)
    workflow.reject_request(
        db, req=req, actor=actor, reason=body.reason,
        expected_version=body.expected_version, http_request=request,
    )
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.post("/{request_id}/cancel", response_model=RequestRead, summary="Withdraw / cancel a request")
def cancel(
    request_id: int, body: ApprovalAction, actor: CurrentUser, db: DbSession, request: HttpRequest
) -> RequestRead:
    req = workflow.load_request(db, request_id)
    workflow.cancel_request(db, req=req, actor=actor, reason=body.remarks, http_request=request)
    db.commit()
    return request_read(workflow.reload_request(db, req.id))


@router.post(
    "/{request_id}/comments", response_model=CommentRead,
    status_code=status.HTTP_201_CREATED, summary="Add a comment to a request",
)
def add_comment(
    request_id: int, body: CommentCreate, actor: CurrentUser, db: DbSession, request: HttpRequest
) -> CommentRead:
    req = workflow.load_request(db, request_id)
    if not workflow.can_view(db, req, actor):
        raise forbidden()
    comment = workflow.add_comment(db, req=req, actor=actor, body=body.body, http_request=request)
    db.commit()
    db.refresh(comment)
    return comment_read(comment)


ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf", "image/png", "image/jpeg", "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post(
    "/{request_id}/attachments", status_code=status.HTTP_201_CREATED,
    summary="Upload a supporting document (multipart/form-data)",
)
async def upload_attachment(
    request_id: int,
    actor: CurrentUser,
    db: DbSession,
    request: HttpRequest,
    file: UploadFile = File(...),
) -> dict:
    """Stores bytes on local disk (swap for S3/GCS in production) and records
    metadata + SHA-256 checksum so tampering is detectable later."""
    req = workflow.load_request(db, request_id)
    if req.requester_id != actor.id and not workflow.can_decide(req, actor) and not actor.is_admin:
        raise forbidden("You cannot attach files to this request")
    if req.status in (RequestStatus.CANCELLED, RequestStatus.REJECTED, RequestStatus.COMPLETED):
        raise conflict(f"Cannot attach documents to a '{req.status.value}' request")
    if file.content_type not in ALLOWED_ATTACHMENT_TYPES:
        raise bad_request(
            f"Unsupported file type '{file.content_type}'. Allowed: {', '.join(sorted(ALLOWED_ATTACHMENT_TYPES))}"
        )

    contents = await file.read()
    if not contents:
        raise bad_request("Uploaded file is empty")
    if len(contents) > MAX_ATTACHMENT_BYTES:
        raise bad_request(f"File exceeds the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit")

    checksum = hashlib.sha256(contents).hexdigest()
    safe_name = os.path.basename(file.filename or "document.bin")[:200]
    storage_key = f"{req.reference_no}/{checksum[:12]}_{safe_name}"
    destination = os.path.join(settings.UPLOAD_DIR, storage_key)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    with open(destination, "wb") as handle:
        handle.write(contents)

    attachment = Attachment(
        request_id=req.id, uploaded_by=actor.id, filename=safe_name, storage_key=storage_key,
        content_type=file.content_type, size_bytes=len(contents), checksum_sha256=checksum,
    )
    db.add(attachment)
    audit.record(
        db, entity_type="approval_request", entity_id=req.id, action="attachment.uploaded", actor=actor,
        request_id=req.id, new_values={"filename": safe_name, "size_bytes": len(contents), "sha256": checksum},
        http_request=request,
    )
    db.commit()
    db.refresh(attachment)
    return {"id": attachment.id, "filename": attachment.filename, "size_bytes": attachment.size_bytes,
            "content_type": attachment.content_type, "sha256": checksum}


@router.get("/{request_id}/audit", response_model=list[AuditLogRead], summary="Audit trail for one request")
def request_audit(request_id: int, actor: CurrentUser, db: DbSession) -> list[AuditLogRead]:
    req = workflow.load_request(db, request_id)
    if not workflow.can_view(db, req, actor):
        raise forbidden()
    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.request_id == request_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    ).all()
    return [audit_read(entry) for entry in logs]


@router.get("/{request_id}/timeline", summary="Merged audit + comment + step timeline (UI history tab)")
def timeline(request_id: int, actor: CurrentUser, db: DbSession) -> dict:
    req = workflow.load_request(db, request_id)
    if not workflow.can_view(db, req, actor):
        raise forbidden()

    events: list[dict] = []
    for entry in req.audit_logs:
        events.append(
            {
                "type": "audit",
                "at": entry.created_at.isoformat() if entry.created_at else None,
                "action": entry.action,
                "actor": entry.actor.full_name if entry.actor else "system",
                "notes": entry.notes,
                "new_values": loads(entry.new_values_json, None),
            }
        )
    for comment in req.comments:
        events.append(
            {
                "type": "comment",
                "at": comment.created_at.isoformat() if comment.created_at else None,
                "actor": comment.user.full_name if comment.user else "unknown",
                "body": comment.body,
            }
        )
    for step in req.steps:
        events.append(
            {
                "type": "step",
                "at": (step.decided_at or step.notified_at or step.created_at).isoformat(),
                "actor": step.approver.full_name if step.approver else step.approver_role,
                "level": step.level,
                "status": step.status.value,
                "remarks": step.remarks,
            }
        )
    events.sort(key=lambda e: e.get("at") or "")
    return {"request_id": request_id, "reference_no": req.reference_no, "events": events}
