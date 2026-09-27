"""Central audit helper.

Every state-changing operation goes through `record()` so the trail is
consistent: who did what, to which entity, before/after values, from where.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def _safe_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"repr": repr(value)})


def client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def record(
    db: Session,
    *,
    entity_type: str,
    action: str,
    entity_id: Optional[int] = None,
    actor: Optional[User] = None,
    request_id: Optional[int] = None,
    old_values: Optional[dict] = None,
    new_values: Optional[dict] = None,
    notes: Optional[str] = None,
    http_request: Optional[Request] = None,
    flush: bool = True,
) -> AuditLog:
    log = AuditLog(
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        actor_id=actor.id if actor else None,
        actor_role=actor.role.value if actor else None,
        request_id=request_id,
        old_values_json=_safe_json(old_values),
        new_values_json=_safe_json(new_values),
        ip_address=client_ip(http_request),
        user_agent=(http_request.headers.get("user-agent")[:255] if http_request else None),
        notes=notes,
    )
    db.add(log)
    if flush:
        db.flush()
    return log
