"""Authentication + authorisation dependencies (JWT + RBAC).

`get_current_user`  -> verified access token  -> User row (must be active)
`require_roles(...)`-> dependency factory that enforces a role whitelist
`require_admin`     -> shorthand for admin/super_admin

Everything returns a proper 401/403 with a WWW-Authenticate header so the React
client can distinguish "please log in" from "you are not allowed".
"""
from __future__ import annotations

from typing import Annotated, Iterable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.database import get_db
from app.enums import UserRole
from app.models import User

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if credentials.scheme.lower() != "bearer":
        raise CREDENTIALS_EXCEPTION

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
        user_id = int(payload.get("sub"))
    except (JWTError, ValueError, TypeError):
        raise CREDENTIALS_EXCEPTION

    user = db.scalar(select(User).where(User.id == user_id, User.is_deleted.is_(False)))
    if user is None:
        raise CREDENTIALS_EXCEPTION
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="User account is deactivated")

    # role claim is trusted for fast-path checks but the DB is the source of truth
    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_db)]
def _passthrough_request(request: Request) -> Request:
    """Typed dependency so FastAPI injects the Starlette Request (not a body field)."""
    return request


HttpRequest = Annotated[Request, Depends(_passthrough_request)]


def require_roles(*roles: UserRole | str):
    """Dependency factory enforcing an allow-list of roles (RBAC)."""
    allowed: set[str] = {r.value if isinstance(r, UserRole) else r for r in roles}

    def _checker(user: CurrentUser) -> User:
        if user.role.value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires one of roles: {', '.join(sorted(allowed))}",
            )
        return user

    return _checker


def require_any_role(user: CurrentUser, roles: Iterable[UserRole]) -> None:
    if user.role not in set(roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient role for this action")


require_manager_or_above = require_roles(UserRole.MANAGER, UserRole.ADMIN, UserRole.SUPER_ADMIN)
require_admin = require_roles(UserRole.ADMIN, UserRole.SUPER_ADMIN)
require_super_admin = require_roles(UserRole.SUPER_ADMIN)

ManagerOrAbove = Annotated[User, Depends(require_manager_or_above)]
AdminUser = Annotated[User, Depends(require_admin)]
SuperAdminUser = Annotated[User, Depends(require_super_admin)]
