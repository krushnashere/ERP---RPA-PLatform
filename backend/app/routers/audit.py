"""Audit trail endpoints (admin / super admin only)."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.auth import AdminUser, DbSession
from app.core.helpers import Page, PageParams, bad_request, paginate
from app.models import AuditLog, User
from app.schemas.common import AuditLogRead
from app.serializers import audit_read

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogRead], summary="Search the append-only audit trail")
def list_audit(
    actor: AdminUser,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    entity_type: Optional[str] = Query(None, max_length=40),
    entity_id: Optional[int] = Query(None, ge=1),
    action: Optional[str] = Query(None, max_length=60),
    actor_id: Optional[int] = Query(None, ge=1),
    request_id: Optional[int] = Query(None, ge=1),
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    search: Optional[str] = Query(None, max_length=80),
) -> Page:
    stmt = select(AuditLog).options(selectinload(AuditLog.actor))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(AuditLog.action.ilike(f"%{action}%"))
    if actor_id:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if request_id:
        stmt = stmt.where(AuditLog.request_id == request_id)
    if created_from:
        stmt = stmt.where(AuditLog.created_at >= created_from)
    if created_to:
        stmt = stmt.where(AuditLog.created_at <= created_to)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(AuditLog.action.ilike(like), AuditLog.notes.ilike(like), AuditLog.entity_type.ilike(like)))

    stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    return paginate(db, stmt, page, serializer=audit_read)


@router.get("/actions", summary="Distinct actions recorded so far (for filter dropdowns)")
def distinct_actions(actor: AdminUser, db: DbSession) -> list[str]:
    rows = db.execute(select(AuditLog.action).distinct().order_by(AuditLog.action)).all()
    return [r[0] for r in rows]


@router.get("/actors", summary="Distinct actors in the audit trail")
def distinct_actors(actor: AdminUser, db: DbSession) -> list[dict]:
    rows = db.execute(
        select(AuditLog.actor_id, User.full_name, User.email, AuditLog.actor_role)
        .outerjoin(User, User.id == AuditLog.actor_id)
        .where(AuditLog.actor_id.is_not(None))
        .distinct()
        .order_by(User.full_name)
    ).all()
    if not rows:
        raise bad_request("No actor activity recorded yet")
    return [{"id": aid, "full_name": name, "email": email, "role": role} for aid, name, email, role in rows]
