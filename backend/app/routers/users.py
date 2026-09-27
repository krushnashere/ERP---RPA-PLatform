"""User management (admin-only) + self-service profile update."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth import AdminUser, CurrentUser, DbSession, HttpRequest
from app.core import audit
from app.core.helpers import Page, PageParams, bad_request, conflict, forbidden, not_found, paginate
from app.core.security import hash_password
from app.enums import RequestStatus, UserRole
from app.models import ApprovalRequest, Department, User
from app.models.base import utcnow
from app.schemas.user import DepartmentRead, UserCreate, UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


def _unique_violation(db: Session, *, email: str, employee_id: str, exclude_id: Optional[int] = None) -> Optional[str]:
    stmt = select(User).where(
        or_(User.email == email.lower(), User.employee_id == employee_id.upper()),
        User.is_deleted.is_(False),
    )
    if exclude_id:
        stmt = stmt.where(User.id != exclude_id)
    clash = db.scalars(stmt).first()
    if not clash:
        return None
    if clash.email == email.lower():
        return f"Email '{email}' is already registered"
    return f"Employee id '{employee_id}' is already registered"


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED, summary="Create a user (admin)")
def create_user(body: UserCreate, actor: AdminUser, db: DbSession, request: HttpRequest) -> User:
    dup = _unique_violation(db, email=body.email, employee_id=body.employee_id)
    if dup:
        raise conflict(dup)

    if body.department_id and db.get(Department, body.department_id) is None:
        raise bad_request("Unknown department_id")
    if body.manager_id:
        manager = db.get(User, body.manager_id)
        if manager is None:
            raise bad_request("Unknown manager_id")
        if manager.role == UserRole.EMPLOYEE:
            raise bad_request("Manager must have the manager role or above")
    if body.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN) and actor.role != UserRole.SUPER_ADMIN:
        raise forbidden("Only a super admin can grant admin roles")

    user = User(
        employee_id=body.employee_id.upper(),
        full_name=body.full_name,
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        role=body.role,
        designation=body.designation,
        department_id=body.department_id,
        manager_id=body.manager_id,
        is_active=body.is_active,
    )
    db.add(user)
    db.flush()
    audit.record(
        db, entity_type="user", entity_id=user.id, action="user.created", actor=actor,
        new_values={"email": user.email, "role": user.role.value, "employee_id": user.employee_id},
        http_request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.get("", response_model=Page[UserRead], summary="List / search users")
def list_users(
    actor: CurrentUser,
    db: DbSession,
    page: Annotated[PageParams, Depends()],
    search: Optional[str] = Query(None, max_length=60),
    role: Optional[UserRole] = None,
    department_id: Optional[int] = None,
    is_active: Optional[bool] = None,
) -> Page:
    if actor.role == UserRole.EMPLOYEE:
        raise forbidden("Employees cannot list users")

    stmt = (
        select(User)
        .options(selectinload(User.department), selectinload(User.manager))
        .where(User.is_deleted.is_(False))
    )
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(or_(User.full_name.ilike(like), User.email.ilike(like), User.employee_id.ilike(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if department_id:
        stmt = stmt.where(User.department_id == department_id)
    if is_active is not None:
        stmt = stmt.where(User.is_active.is_(is_active))

    stmt = stmt.order_by(User.full_name.asc())
    return paginate(db, stmt, page, serializer=lambda u: UserRead.model_validate(u))


@router.get("/me", response_model=UserRead, summary="My own profile")
def my_profile(actor: CurrentUser, db: DbSession) -> User:
    db.refresh(actor)
    return actor


@router.get("/{user_id}", response_model=UserRead, summary="Get one user")
def get_user(user_id: int, actor: CurrentUser, db: DbSession) -> User:
    user = db.scalar(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    if user is None:
        raise not_found("User")
    if actor.role == UserRole.EMPLOYEE and actor.id != user.id:
        raise forbidden()
    return user


@router.patch("/{user_id}", response_model=UserRead, summary="Update a user (admin)")
def update_user(user_id: int, body: UserUpdate, actor: AdminUser, db: DbSession, request: HttpRequest) -> User:
    user = db.scalar(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    if user is None:
        raise not_found("User")

    if body.email and body.email.lower() != user.email:
        dup = _unique_violation(db, email=body.email, employee_id=user.employee_id, exclude_id=user.id)
        if dup:
            raise conflict(dup)
    if body.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN) and actor.role != UserRole.SUPER_ADMIN:
        raise forbidden("Only a super admin can grant admin roles")
    if user.id == actor.id and body.is_active is False:
        raise bad_request("You cannot deactivate your own account")
    if user.role == UserRole.SUPER_ADMIN and actor.role != UserRole.SUPER_ADMIN:
        raise forbidden("Only a super admin can modify another super admin")

    old = {"role": user.role.value, "email": user.email, "is_active": user.is_active, "department_id": user.department_id}
    data = body.model_dump(exclude_unset=True, exclude={"password"})
    for field, value in data.items():
        if field == "email" and value:
            value = value.lower()
        setattr(user, field, value)
    if body.password:
        user.hashed_password = hash_password(body.password)

    audit.record(
        db, entity_type="user", entity_id=user.id, action="user.updated", actor=actor,
        old_values=old,
        new_values={k: v for k, v in data.items() if old.get(k) != v},
        notes="password rotated" if body.password else None,
        http_request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Soft-delete a user (admin)")
def delete_user(user_id: int, actor: AdminUser, db: DbSession, request: HttpRequest) -> None:
    user = db.scalar(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    if user is None:
        raise not_found("User")
    if user.id == actor.id:
        raise bad_request("You cannot delete your own account")
    if user.role == UserRole.SUPER_ADMIN and actor.role != UserRole.SUPER_ADMIN:
        raise forbidden("Only a super admin can delete another super admin")

    open_requests = db.scalar(
        select(func.count())
        .select_from(ApprovalRequest)
        .where(
            ApprovalRequest.requester_id == user.id,
            ApprovalRequest.is_deleted.is_(False),
            ApprovalRequest.status.in_(
                [RequestStatus.DRAFT, RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL]
            ),
        )
    )
    if open_requests:
        raise conflict(f"User still has {open_requests} open request(s); reassign or close them first")

    user.is_deleted = True
    user.is_active = False
    user.deleted_at = utcnow()
    user.deleted_by = actor.id
    audit.record(
        db, entity_type="user", entity_id=user.id, action="user.deleted", actor=actor,
        old_values={"email": user.email, "role": user.role.value}, http_request=request,
    )
    db.commit()
