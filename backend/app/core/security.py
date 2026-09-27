"""Password hashing + JWT issuing/verification.

Hashing
-------
Primary scheme: **argon2id** (memory-hard, the current OWASP first choice) via
`argon2-cffi`, with automatic rehash detection so parameters can be tightened
later without a password reset. Legacy bcrypt hashes are still *verified* so an
existing user base can be migrated transparently on next login
(`needs_rehash()` -> rehash with argon2id).

Tokens
------
HS256 signed JWTs. Two token types:
  * access  - short lived (default 60 min), sent as `Authorization: Bearer ...`
  * refresh - long lived (default 7 days), only accepted by POST /auth/refresh
Claims: sub (user id), role, type, iat, exp, jti.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import argon2
import bcrypt
from jose import JWTError, jwt

from app.config import settings

TokenType = Literal["access", "refresh"]

# argon2id: 3 passes, 64 MiB, 4 lanes -> ~256-bit tag (OWASP recommended profile)
_hasher = argon2.PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)

MAX_PASSWORD_BYTES = 72  # bcrypt's hard limit; we enforce it for legacy compatibility


class PasswordTooLongError(ValueError):
    """Raised when the supplied password exceeds the supported length."""


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #
def hash_password(plain: str) -> str:
    if len(plain.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise PasswordTooLongError(f"password must be at most {MAX_PASSWORD_BYTES} bytes")
    return _hasher.hash(plain)


def hash_password_bcrypt(plain: str, rounds: int | None = None) -> str:
    """Legacy path kept for migrating old rows / interop with existing systems."""
    pw = plain.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=rounds or min(settings.BCRYPT_ROUNDS, 12))).decode()


def verify_password(plain: str, hashed: str | None) -> bool:
    """Verify against argon2id or a legacy bcrypt hash. Never raises."""
    if not hashed:
        return False
    try:
        if hashed.startswith("$argon2"):
            return _hasher.verify(hashed, plain)
        if hashed.startswith(("$2a$", "$2b$", "$2y$")):
            return bcrypt.checkpw(plain.encode("utf-8")[:MAX_PASSWORD_BYTES], hashed.encode("utf-8"))
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.VerificationError,
            argon2.exceptions.InvalidHashError, ValueError, TypeError):
        return False
    return False


def needs_rehash(hashed: str | None) -> bool:
    """True when the stored hash is legacy/weak and should be upgraded on login."""
    if not hashed:
        return True
    if not hashed.startswith("$argon2"):
        return True
    try:
        return _hasher.check_needs_rehash(hashed)
    except Exception:
        return True


# --------------------------------------------------------------------------- #
# jwt
# --------------------------------------------------------------------------- #
def create_token(
    subject: str | int,
    token_type: TokenType = "access",
    expires_delta: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        expires_delta = (
            timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            if token_type == "access"
            else timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        )
    expire = now + expires_delta
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "jti": uuid.uuid4().hex,  # unique id -> enables revocation lists later
        **(extra_claims or {}),
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire


def decode_token(token: str, expected_type: TokenType | None = "access") -> dict[str, Any]:
    """Verify signature, expiry and (optionally) token type. Raises JWTError."""
    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    if expected_type and payload.get("type") != expected_type:
        raise JWTError(f"expected a {expected_type} token")
    return payload


def create_access_token(user_id: int, role: str) -> tuple[str, datetime]:
    return create_token(user_id, "access", extra_claims={"role": role})


def create_refresh_token(user_id: int, role: str) -> tuple[str, datetime]:
    return create_token(user_id, "refresh", extra_claims={"role": role})
