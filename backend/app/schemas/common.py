from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    entity_type: str
    entity_id: Optional[int] = None
    action: str
    actor_id: Optional[int] = None
    actor_role: Optional[str] = None
    request_id: Optional[int] = None
    old_values: Optional[Any] = None
    new_values: Optional[Any] = None
    notes: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime


class PageMeta(BaseModel):
    total: int = Field(description="Total rows matching the filters")
    page: int
    size: int
    pages: int
