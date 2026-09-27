from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.enums import Priority, RequestStatus, StepStatus


# --------------------------------------------------------------------------- #
# departments / request types
# --------------------------------------------------------------------------- #
class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Z0-9_]+$")
    cost_center: Optional[str] = Field(default=None, max_length=30)
    budget_limit: float = Field(default=0.0, ge=0)


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    cost_center: Optional[str] = Field(default=None, max_length=30)
    budget_limit: Optional[float] = Field(default=None, ge=0)


class ApprovalChainLevel(BaseModel):
    level: int = Field(ge=1)
    role: str = Field(pattern=r"^(manager|admin|super_admin|finance)$")
    min_amount: float = Field(default=0.0, ge=0)
    description: Optional[str] = None


class RequestTypeCreate(BaseModel):
    code: str = Field(min_length=2, max_length=30, pattern=r"^[A-Z0-9_]+$")
    name: str = Field(min_length=3, max_length=120)
    description: Optional[str] = None
    amount_limit: float = Field(default=0.0, ge=0)
    sla_hours: int = Field(default=48, ge=0, le=24 * 365)
    requires_attachment: bool = False
    auto_approve_below: Optional[float] = Field(default=None, ge=0)
    approval_chain: list[ApprovalChainLevel] = Field(default_factory=list)
    rpa_process_id: Optional[int] = None

    @field_validator("approval_chain")
    @classmethod
    def _levels_unique_and_ordered(cls, chain: list[ApprovalChainLevel]) -> list[ApprovalChainLevel]:
        levels = [c.level for c in chain]
        if len(set(levels)) != len(levels):
            raise ValueError("approval chain levels must be unique")
        return sorted(chain, key=lambda c: c.level)


class RequestTypeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: Optional[str] = None
    amount_limit: float
    sla_hours: int
    requires_attachment: bool
    auto_approve_below: Optional[float] = None
    approval_chain: list[ApprovalChainLevel] = []
    rpa_process_id: Optional[int] = None
    is_active: bool
    created_at: datetime


# --------------------------------------------------------------------------- #
# requests
# --------------------------------------------------------------------------- #
class RequestCreate(BaseModel):
    request_type_id: int
    title: str = Field(min_length=3, max_length=180)
    description: Optional[str] = Field(default=None, max_length=4000)
    amount: float = Field(ge=0, le=10_000_000_000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    priority: Priority = Priority.NORMAL
    department_id: Optional[int] = None
    payload: dict[str, Any] = Field(default_factory=dict, description="Structured line items / vendor data")
    submit_immediately: bool = Field(default=False, description="Create as DRAFT or straight to SUBMITTED")

    @field_validator("title")
    @classmethod
    def _no_placeholder_titles(cls, v: str) -> str:
        v = " ".join(v.split())
        if v.lower() in {"test", "n/a", "none", "-"}:
            raise ValueError("please provide a meaningful title")
        return v

    @model_validator(mode="after")
    def _currency_matches_amount(self) -> "RequestCreate":
        if self.amount > 0 and self.currency not in {"INR", "USD", "EUR", "GBP"}:
            raise ValueError("unsupported currency")
        return self


class RequestUpdate(BaseModel):
    """Only allowed while the request is still a DRAFT."""
    title: Optional[str] = Field(default=None, min_length=3, max_length=180)
    description: Optional[str] = Field(default=None, max_length=4000)
    amount: Optional[float] = Field(default=None, ge=0)
    priority: Optional[Priority] = None
    payload: Optional[dict[str, Any]] = None
    expected_version: Optional[int] = Field(
        default=None, description="Optimistic lock: must match the version you read"
    )


class ApprovalAction(BaseModel):
    remarks: Optional[str] = Field(default=None, max_length=1000)
    expected_version: Optional[int] = None

    @model_validator(mode="after")
    def _remarks_required_on_reject(self) -> "ApprovalAction":
        return self


class RejectAction(ApprovalAction):
    reason: str = Field(min_length=3, max_length=1000)


class ApprovalStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    level: int
    approver_role: str
    approver_id: Optional[int] = None
    approver_name: Optional[str] = None
    status: StepStatus
    remarks: Optional[str] = None
    decided_at: Optional[datetime] = None


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    body: str
    created_at: datetime
    user_id: int
    user_name: Optional[str] = None


class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class AttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    storage_key: str
    content_type: Optional[str] = None
    size_bytes: int
    created_at: datetime


class RequestListRead(BaseModel):
    """Lightweight projection used by the list/table view (fewer joins)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    reference_no: str
    title: str
    amount: float
    currency: str
    priority: Priority
    status: RequestStatus
    current_step_level: int
    sla_due_at: Optional[datetime] = None
    created_at: datetime
    submitted_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    request_type_code: Optional[str] = None
    request_type_name: Optional[str] = None
    requester_id: int
    requester_name: Optional[str] = None
    department_name: Optional[str] = None


class RequestRead(RequestListRead):
    description: Optional[str] = None
    payload: dict[str, Any] = {}
    rejection_reason: Optional[str] = None
    is_sla_breached: bool = False
    version: int = 1
    steps: list[ApprovalStepRead] = []
    comments: list[CommentRead] = []
    attachments: list[AttachmentRead] = []
    rpa_job_ids: list[int] = []


class RequestFilters(BaseModel):
    """Validated filter object - the service turns this into SQL WHERE clauses."""
    status: Optional[list[RequestStatus]] = None
    priority: Optional[list[Priority]] = None
    request_type_id: Optional[int] = None
    department_id: Optional[int] = None
    requester_id: Optional[int] = None
    search: Optional[str] = None
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    created_from: Optional[datetime] = None
    created_to: Optional[datetime] = None
    sla_breached_only: bool = False
    sort_by: str = "created_at"
    sort_dir: str = "desc"

    @field_validator("sort_by")
    @classmethod
    def _whitelist_sort(cls, v: str) -> str:
        allowed = {"created_at", "amount", "status", "sla_due_at", "reference_no", "updated_at"}
        if v not in allowed:
            raise ValueError(f"sort_by must be one of {sorted(allowed)}")
        return v

    @field_validator("sort_dir")
    @classmethod
    def _whitelist_dir(cls, v: str) -> str:
        return v.lower() if v.lower() in {"asc", "desc"} else "desc"
