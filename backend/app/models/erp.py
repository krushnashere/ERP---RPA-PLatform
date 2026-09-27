"""ERP domain models: users, org structure, business requests, approvals, audit.

Relationship map (the joins used by the API):

    Department 1---* User
    RequestType 1---* ApprovalRequest *---1 User (requester)
    ApprovalRequest 1---* ApprovalStep *---1 User (approver)
    ApprovalRequest 1---* RequestComment / Attachment / AuditLog
    ApprovalRequest 0..1---* RpaJob (created when an approved request is automated)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.enums import Priority, RequestStatus, StepStatus, UserRole
from app.models.base import UTCDateTime, SoftDeleteMixin, TimestampMixin, VersionedMixin, utcnow
from app.models.datetime_type import as_aware


class Department(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    cost_center: Mapped[Optional[str]] = mapped_column(String(30))
    budget_limit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    users: Mapped[list["User"]] = relationship(back_populates="department")
    requests: Mapped[list["ApprovalRequest"]] = relationship(back_populates="department")


class User(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (Index("ix_users_role_active", "role", "is_active"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        SAEnum(UserRole, native_enum=False, length=20), default=UserRole.EMPLOYEE, nullable=False, index=True
    )
    designation: Mapped[Optional[str]] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), index=True)
    manager_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)

    department: Mapped[Optional["Department"]] = relationship(back_populates="users")
    manager: Mapped[Optional["User"]] = relationship(remote_side="User.id", back_populates="reports")
    reports: Mapped[list["User"]] = relationship(back_populates="manager")

    created_requests: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="requester", foreign_keys="ApprovalRequest.requester_id"
    )
    approval_steps: Mapped[list["ApprovalStep"]] = relationship(back_populates="approver")

    @property
    def is_admin(self) -> bool:
        return self.role in (UserRole.ADMIN, UserRole.SUPER_ADMIN)


class RequestType(Base, TimestampMixin):
    """Definition of a business process, e.g. 'Purchase Order > 50k'.

    The approval chain is stored as ordered JSON so new workflows can be
    configured without a code deploy:
        approval_chain = [{"level": 1, "role": "manager"},
                          {"level": 2, "role": "admin", "min_amount": 50000}]
    """
    __tablename__ = "request_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    amount_limit: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sla_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    requires_attachment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_approve_below: Mapped[Optional[float]] = mapped_column(Float)

    # serialised approval chain (levels + roles) - parsed by workflow_service
    approval_chain_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    # optional RPA process triggered once the request is approved
    rpa_process_id: Mapped[Optional[int]] = mapped_column(ForeignKey("rpa_processes.id"), nullable=True)

    requests: Mapped[list["ApprovalRequest"]] = relationship(back_populates="request_type")
    rpa_process: Mapped[Optional["RpaProcess"]] = relationship(back_populates="request_types")

    __table_args__ = (CheckConstraint("sla_hours >= 0", name="ck_request_type_sla_positive"),)


class ApprovalRequest(Base, TimestampMixin, SoftDeleteMixin, VersionedMixin):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_req_status_dept", "status", "department_id"),
        Index("ix_req_requester_status", "requester_id", "status"),
        Index("ix_req_created_desc", "created_at"),
        CheckConstraint("amount >= 0", name="ck_request_amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="INR", nullable=False)
    priority: Mapped[Priority] = mapped_column(
        SAEnum(Priority, native_enum=False, length=12), default=Priority.NORMAL, nullable=False
    )
    status: Mapped[RequestStatus] = mapped_column(
        SAEnum(RequestStatus, native_enum=False, length=20),
        default=RequestStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # structured business payload (line items, vendor, GL codes ...)
    payload_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text)
    current_step_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    submitted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    sla_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, index=True)

    request_type_id: Mapped[int] = mapped_column(ForeignKey("request_types.id"), nullable=False, index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    department_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id"), index=True)

    request_type: Mapped["RequestType"] = relationship(back_populates="requests")
    requester: Mapped["User"] = relationship(
        back_populates="created_requests", foreign_keys=[requester_id]
    )
    department: Mapped[Optional["Department"]] = relationship(back_populates="requests")

    steps: Mapped[list["ApprovalStep"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="ApprovalStep.level"
    )
    comments: Mapped[list["RequestComment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="RequestComment.created_at"
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="request", cascade="all, delete-orphan"
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="AuditLog.created_at"
    )
    rpa_jobs: Mapped[list["RpaJob"]] = relationship(back_populates="request")

    @property
    def is_sla_breached(self) -> bool:
        if self.status not in (RequestStatus.SUBMITTED, RequestStatus.PENDING_APPROVAL):
            return False
        if not self.sla_due_at:
            return False
        return utcnow() > as_aware(self.sla_due_at)

    @property
    def current_step(self) -> Optional["ApprovalStep"]:
        pending = [s for s in self.steps if s.status == StepStatus.PENDING]
        return min(pending, key=lambda s: s.level) if pending else None


class ApprovalStep(Base, TimestampMixin):
    __tablename__ = "approval_steps"
    __table_args__ = (
        UniqueConstraint("request_id", "level", name="uq_step_per_level"),
        Index("ix_step_approver_status", "approver_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    approver_role: Mapped[str] = mapped_column(String(20), nullable=False)
    approver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[StepStatus] = mapped_column(
        SAEnum(StepStatus, native_enum=False, length=12), default=StepStatus.PENDING, nullable=False, index=True
    )
    remarks: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    notified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)

    request: Mapped["ApprovalRequest"] = relationship(back_populates="steps")
    approver: Mapped[Optional["User"]] = relationship(back_populates="approval_steps")


class RequestComment(Base, TimestampMixin):
    __tablename__ = "request_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    request: Mapped["ApprovalRequest"] = relationship(back_populates="comments")
    user: Mapped["User"] = relationship()


class Attachment(Base, TimestampMixin):
    """Metadata only - the bytes live in object storage / a mounted volume."""
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), nullable=False, index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64))

    request: Mapped["ApprovalRequest"] = relationship(back_populates="attachments")
    user: Mapped["User"] = relationship()


class AuditLog(Base, TimestampMixin):
    """Append-only trail. Never UPDATEd or DELETEd by application code."""
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), index=True)
    actor_role: Mapped[Optional[str]] = mapped_column(String(20))
    request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), index=True)

    old_values_json: Mapped[Optional[str]] = mapped_column(Text)
    new_values_json: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    actor: Mapped[Optional["User"]] = relationship()
    request: Mapped[Optional["ApprovalRequest"]] = relationship(back_populates="audit_logs")
