"""Auth endpoints: login, refresh, me."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.core import audit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.database import get_db
from app.enums import UserRole
from app.models import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserBrief
from app.models.base import utcnow
from jose import JWTError

router = APIRouter(prefix="/auth", tags=["auth"])

# a valid-shaped hash used to burn CPU on unknown emails (timing-attack mitigation)
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHRzb21lc2E$WbHRLwvVHn0V0hEH0hEH0hEH0hEH0hEH0hEH0hEH0hE"


def _token_response(user: User) -> TokenResponse:
    access, expires_at = create_access_token(user.id, user.role.value)
    refresh, _ = create_refresh_token(user.id, user.role.value)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        token_type="bearer",
        expires_at=expires_at,
        user=UserBrief.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse, summary="Exchange email + password for a JWT pair")
def login(body: LoginRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    user = db.scalar(
        select(User).where(User.email == body.email.lower(), User.is_deleted.is_(False))
    )
    # Always run a hash comparison to keep the response time constant-ish and to
    # avoid leaking which emails exist.
    if user is not None:
        password_ok = verify_password(body.password, user.hashed_password)
    else:
        # constant-ish work so timing does not reveal whether the email exists
        password_ok = verify_password(body.password, DUMMY_HASH)
    if user is None or not password_ok:
        audit.record(
            db, entity_type="user", entity_id=user.id if user else None, action="auth.login_failed",
            notes=f"Failed login for {body.email}", http_request=request,
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account is deactivated, contact an administrator")

    if needs_rehash(user.hashed_password):
        # transparent migration: legacy bcrypt hash -> argon2id on next login
        user.hashed_password = hash_password(body.password)
    user.last_login_at = utcnow()
    audit.record(
        db, entity_type="user", entity_id=user.id, action="auth.login",
        actor=user, new_values={"role": user.role.value}, http_request=request,
    )
    db.commit()
    return _token_response(user)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate tokens using a refresh token")
def refresh(body: RefreshRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token, expected_type="refresh")
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    user = db.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User no longer valid")
    return _token_response(user)


@router.get("/me", response_model=UserBrief, summary="Profile of the authenticated user")
def me(user: CurrentUser) -> User:
    return user


@router.get("/roles", summary="Roles the RBAC layer understands")
def roles(_: CurrentUser) -> list[dict[str, str]]:
    descriptions = {
        UserRole.EMPLOYEE: "Raises requests, comments, sees own items",
        UserRole.MANAGER: "Approves/rejects requests routed to them, sees department items",
        UserRole.ADMIN: "Manages users, request types, RPA processes and jobs",
        UserRole.SUPER_ADMIN: "Full control incl. audit trail and destructive actions",
    }
    return [{"role": r.value, "description": descriptions[r]} for r in UserRole]
