from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: "UserBrief"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str


class UserBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employee_id: str
    full_name: str
    email: EmailStr
    role: str
    department_id: Optional[int] = None
    designation: Optional[str] = None


TokenResponse.model_rebuild()
