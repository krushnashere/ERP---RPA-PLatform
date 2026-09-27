"""Small shared helpers: pagination envelope, idempotent commits, errors."""
from __future__ import annotations

from typing import Any, Generic, Sequence, TypeVar

from fastapi import HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class PageParams:
    """Validated pagination query params (page >= 1, 1 <= size <= 100)."""

    def __init__(self, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
        self.page = page
        self.size = size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class Page(BaseModel, Generic[T]):
    items: Sequence[T]
    total: int
    page: int
    size: int
    pages: int


def paginate(db: Session, stmt, page: PageParams, serializer=None) -> Page:
    """Run a filtered SELECT with COUNT(*) over the same filters."""
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset(page.offset).limit(page.size)).all()
    items = [serializer(r) for r in rows] if serializer else list(rows)
    pages = (total + page.size - 1) // page.size if total else 0
    return Page[Any](items=items, total=total, page=page.page, size=page.size, pages=pages)


def commit_or_rollback(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def conflict(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail)


def not_found(entity: str = "Resource") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{entity} not found")


def bad_request(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail)


def forbidden(detail: str = "You do not have permission to perform this action") -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail)
