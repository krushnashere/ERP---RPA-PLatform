"""Domain enums shared by models, schemas and services.

Kept in one place so the API, the DB and the React client all speak the same
vocabulary (the frontend mirrors these strings).
"""
from enum import Enum


class UserRole(str, Enum):
    EMPLOYEE = "employee"          # can raise requests, see own items
    MANAGER = "manager"            # can approve/reject requests routed to them
    ADMIN = "admin"                # can manage users, request types, processes
    SUPER_ADMIN = "super_admin"    # everything + audit trail + destructive ops


class RequestStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    COMPLETED = "completed"        # post-approval RPA execution finished


class StepStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class RpaJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DEAD_LETTER = "dead_letter"    # retries exhausted -> human intervention


# Request types that are wired to an automated (RPA) process after approval.
RPA_TRIGGER_STATUSES = {RequestStatus.APPROVED}

# Statuses that are still "open" from a business point of view.
OPEN_STATUSES = {
    RequestStatus.DRAFT,
    RequestStatus.SUBMITTED,
    RequestStatus.PENDING_APPROVAL,
}
