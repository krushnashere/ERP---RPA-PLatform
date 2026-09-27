from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.enums import UserRole

EMPLOYEE_ID_RE = r"^[A-Z]{2,4}-\d{3,6}$"


class UserBase(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    employee_id: str = Field(pattern=EMPLOYEE_ID_RE, description="e.g. ERP-10234")
    role: UserRole = UserRole.EMPLOYEE
    designation: Optional[str] = Field(default=None, max_length=80)
    department_id: Optional[int] = None
    manager_id: Optional[int] = None
    is_active: bool = True

    @field_validator("full_name", "employee_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_strength(cls, v: str) -> str:
        """Basic corporate policy: 8+ chars with upper, lower and digit."""
        if not any(c.isupper() for c in v):
            raise ValueError("password must contain an uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("password must contain a lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("password must contain a digit")
        return v


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    designation: Optional[str] = Field(default=None, max_length=80)
    department_id: Optional[int] = None
    manager_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class DepartmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    cost_center: Optional[str] = None
    budget_limit: float
    created_at: datetime


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    last_login_at: Optional[datetime] = None
    department: Optional[DepartmentRead] = None
    manager: Optional["UserBriefLite"] = None


class UserBriefLite(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    email: EmailStr
    role: str


UserRead.model_rebuild()


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    code: str = Field(min_length=2, max_length=20, pattern=r"^[A-Z0-9_]+$")
    cost_center: Optional[str] = Field(default=None, max_length=30)
    budget_limit: float = Field(default=0.0, ge=0)


class DepartmentUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=80)
    cost_center: Optional[str] = Field(default=None, max_length=30)
    budget_limit: Optional[float] = Field(default=None, ge=0)
