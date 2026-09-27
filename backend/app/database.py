"""Database engine / session management (SQLAlchemy 2.0 style).

One engine + one sessionmaker for the whole app. FastAPI dependencies hand out a
session per request and always close it, even when the handler raises.
"""
from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,          # drop stale connections before use
    future=True,
    connect_args=_connect_args,
)

if settings.is_sqlite:
    # SQLite stores datetimes without timezone info and needs FKs switched on.
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for every ORM model."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: one transactional session per request."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create all tables. In production this is done by Alembic migrations."""
    from app import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=engine)
