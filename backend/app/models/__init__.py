"""ORM model registry - importing this package registers every mapper."""
from app.models.erp import (  # noqa: F401
    ApprovalRequest,
    ApprovalStep,
    Attachment,
    AuditLog,
    Department,
    RequestComment,
    RequestType,
    User,
)
from app.models.rpa import RpaJob, RpaJobLog, RpaProcess  # noqa: F401

__all__ = [
    "ApprovalRequest",
    "ApprovalStep",
    "Attachment",
    "AuditLog",
    "Department",
    "RequestComment",
    "RequestType",
    "User",
    "RpaJob",
    "RpaJobLog",
    "RpaProcess",
]
