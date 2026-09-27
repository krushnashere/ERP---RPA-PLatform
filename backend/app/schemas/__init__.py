"""Pydantic v2 schemas (request/response validation)."""
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserBrief  # noqa: F401
from app.schemas.common import AuditLogRead, PageMeta  # noqa: F401
from app.schemas.request import (  # noqa: F401
    ApprovalAction,
    ApprovalChainLevel,
    ApprovalStepRead,
    AttachmentRead,
    CommentCreate,
    CommentRead,
    RejectAction,
    RequestCreate,
    RequestFilters,
    RequestListRead,
    RequestRead,
    RequestTypeCreate,
    RequestTypeRead,
    RequestUpdate,
)
from app.schemas.rpa import (  # noqa: F401
    RpaJobComplete,
    RpaJobCreate,
    RpaJobLogRead,
    RpaJobRead,
    RpaProcessCreate,
    RpaProcessRead,
    RpaProcessUpdate,
    RpaQueueStats,
    WorkerLogCreate,
)
from app.schemas.user import (  # noqa: F401
    DepartmentCreate,
    DepartmentRead,
    DepartmentUpdate,
    UserCreate,
    UserRead,
    UserUpdate,
)
