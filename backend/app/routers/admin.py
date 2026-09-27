"""Admin configuration: departments and request types (approval workflow setup)."""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select

from app.auth import AdminUser, CurrentUser, DbSession, HttpRequest
from app.core import audit
from app.core.helpers import bad_request, conflict, not_found
from app.enums import UserRole
from app.models import ApprovalRequest, Department, RequestType, RpaProcess, User
from app.models.base import utcnow
from app.schemas.request import RequestTypeCreate, RequestTypeRead
from app.schemas.user import DepartmentCreate, DepartmentRead, DepartmentUpdate
from app.serializers import request_type_read

router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------- #
# departments
# --------------------------------------------------------------------------- #
@router.get("/departments", response_model=list[DepartmentRead], summary="List departments")
def list_departments(actor: CurrentUser, db: DbSession, include_deleted: bool = False) -> list[Department]:
    stmt = select(Department)
    if not include_deleted:
        stmt = stmt.where(Department.is_deleted.is_(False))
    return list(db.scalars(stmt.order_by(Department.name)).all())


@router.post("/departments", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
def create_department(body: DepartmentCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> Department:
    exists = db.scalar(select(Department).where(func.lower(Department.name) == body.name.lower()))
    if exists:
        raise conflict(f"Department '{body.name}' already exists")
    if db.scalar(select(Department).where(Department.code == body.code.upper())):
        raise conflict(f"Department code '{body.code}' already exists")

    dept = Department(
        name=body.name.strip(), code=body.code.upper(),
        cost_center=body.cost_center, budget_limit=body.budget_limit,
    )
    db.add(dept)
    db.flush()
    audit.record(
        db, entity_type="department", entity_id=dept.id, action="department.created", actor=actor,
        new_values=body.model_dump(), http_request=request,
    )
    db.commit()
    db.refresh(dept)
    return dept


@router.patch("/departments/{department_id}", response_model=DepartmentRead)
def update_department(
    department_id: int, body: DepartmentUpdate, actor: AdminUser, db: DbSession, request: HttpRequest
) -> Department:
    dept = db.get(Department, department_id)
    if dept is None or dept.is_deleted:
        raise not_found("Department")
    old = {"name": dept.name, "budget_limit": dept.budget_limit, "cost_center": dept.cost_center}
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(dept, field, value)
    audit.record(
        db, entity_type="department", entity_id=dept.id, action="department.updated", actor=actor,
        old_values=old, new_values=body.model_dump(exclude_unset=True), http_request=request,
    )
    db.commit()
    db.refresh(dept)
    return dept


@router.delete("/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_department(department_id: int, actor: AdminUser, db: DbSession, request: HttpRequest) -> None:
    dept = db.get(Department, department_id)
    if dept is None or dept.is_deleted:
        raise not_found("Department")
    in_use = db.scalar(select(func.count()).select_from(User).where(User.department_id == department_id))
    if in_use:
        raise conflict(f"{in_use} user(s) still belong to this department")
    dept.is_deleted = True
    dept.deleted_at = utcnow()
    audit.record(
        db, entity_type="department", entity_id=dept.id, action="department.deleted", actor=actor,
        old_values={"name": dept.name}, http_request=request,
    )
    db.commit()


# --------------------------------------------------------------------------- #
# request types (workflow definitions)
# --------------------------------------------------------------------------- #
@router.get("/request-types", response_model=list[RequestTypeRead], summary="List workflow definitions")
def list_request_types(actor: CurrentUser, db: DbSession, include_inactive: bool = False) -> list[RequestTypeRead]:
    stmt = select(RequestType)
    if not include_inactive:
        stmt = stmt.where(RequestType.is_active.is_(True))
    rows = db.scalars(stmt.order_by(RequestType.code)).all()
    return [request_type_read(rt) for rt in rows]


@router.post("/request-types", response_model=RequestTypeRead, status_code=status.HTTP_201_CREATED)
def create_request_type(body: RequestTypeCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> RequestTypeRead:
    if db.scalar(select(RequestType).where(RequestType.code == body.code.upper())):
        raise conflict(f"Request type code '{body.code}' already exists")
    if body.auto_approve_below and body.amount_limit and body.auto_approve_below > body.amount_limit:
        raise bad_request("auto_approve_below cannot be higher than amount_limit")
    if body.rpa_process_id and db.get(RpaProcess, body.rpa_process_id) is None:
        raise bad_request("Unknown rpa_process_id")

    rt = RequestType(
        code=body.code.upper(),
        name=body.name.strip(),
        description=body.description,
        amount_limit=body.amount_limit,
        sla_hours=body.sla_hours,
        requires_attachment=body.requires_attachment,
        auto_approve_below=body.auto_approve_below,
        approval_chain_json=json.dumps([c.model_dump() for c in body.approval_chain]),
        rpa_process_id=body.rpa_process_id,
        is_active=True,
    )
    db.add(rt)
    db.flush()
    audit.record(
        db, entity_type="request_type", entity_id=rt.id, action="request_type.created", actor=actor,
        new_values={"code": rt.code, "levels": len(body.approval_chain), "rpa_process_id": body.rpa_process_id},
        http_request=request,
    )
    db.commit()
    return request_type_read(rt)


@router.get("/request-types/{code_or_id}", response_model=RequestTypeRead)
def get_request_type(code_or_id: str, actor: CurrentUser, db: DbSession) -> RequestTypeRead:
    stmt = select(RequestType)
    if code_or_id.isdigit():
        stmt = stmt.where(RequestType.id == int(code_or_id))
    else:
        stmt = stmt.where(RequestType.code == code_or_id.upper())
    rt = db.scalar(stmt)
    if rt is None:
        raise not_found("Request type")
    return request_type_read(rt)


@router.patch("/request-types/{type_id}", response_model=RequestTypeRead, summary="Update a workflow definition")
def update_request_type(type_id: int, body: RequestTypeCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> RequestTypeRead:
    rt = db.get(RequestType, type_id)
    if rt is None:
        raise not_found("Request type")
    old = {"name": rt.name, "amount_limit": rt.amount_limit, "sla_hours": rt.sla_hours, "chain": rt.approval_chain_json}
    rt.name = body.name.strip()
    rt.description = body.description
    rt.amount_limit = body.amount_limit
    rt.sla_hours = body.sla_hours
    rt.requires_attachment = body.requires_attachment
    rt.auto_approve_below = body.auto_approve_below
    rt.rpa_process_id = body.rpa_process_id
    rt.approval_chain_json = json.dumps([c.model_dump() for c in body.approval_chain])
    audit.record(
        db, entity_type="request_type", entity_id=rt.id, action="request_type.updated", actor=actor,
        old_values=old, new_values=body.model_dump(), http_request=request,
    )
    db.commit()
    return request_type_read(rt)


@router.delete("/request-types/{type_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Deactivate a workflow")
def deactivate_request_type(type_id: int, actor: AdminUser, db: DbSession, request: HttpRequest) -> None:
    rt = db.get(RequestType, type_id)
    if rt is None:
        raise not_found("Request type")
    if not actor.role == UserRole.SUPER_ADMIN:
        in_flight = db.scalar(
            select(func.count()).select_from(ApprovalRequest).where(
                ApprovalRequest.request_type_id == type_id,
                ApprovalRequest.status.in_(["draft", "submitted", "pending_approval"]),
            )
        )
        if in_flight:
            raise conflict(f"{in_flight} request(s) are still in flight for this type")
    rt.is_active = False
    audit.record(
        db, entity_type="request_type", entity_id=rt.id, action="request_type.deactivated", actor=actor,
        old_values={"is_active": True}, new_values={"is_active": False}, http_request=request,
    )
    db.commit()
