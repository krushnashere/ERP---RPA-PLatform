"""FastAPI application factory.

Run locally:
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
Interactive docs:
    http://localhost:8000/docs  (Swagger UI)
"""
from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.config import settings
from app.database import init_db
from app.routers import admin, audit, auth, requests as requests_router, rpa, users

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("erp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Database ready (%s)", settings.DATABASE_URL.split("://")[0])
    yield
    logger.info("Shutting down")


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Full-stack platform to manage business requests, multi-level approvals and workflow status, "
        "plus a queue-based RPA orchestration layer (workers, retries, cancellation, audit logging).\n\n"
        "**Auth:** `POST /api/v1/auth/login` returns a JWT; send it as `Authorization: Bearer <token>`."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Attach a correlation id + duration to every request (production debugging)."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error", "request_id": request_id},
        )
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Process-Time-Ms"] = str(duration_ms)
    logger.info(
        "%s %s -> %s in %sms (request_id=%s)",
        request.method, request.url.path, response.status_code, duration_ms, request_id,
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Return field-level errors the React form can render inline."""
    errors = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p not in ("body", "query")]
        errors.append({"field": ".".join(loc) or "body", "message": err.get("msg", "invalid")})
    return JSONResponse(
        status_code=422,  # Unprocessable Content
        content={"detail": "Validation failed", "errors": errors, "request_id": getattr(request.state, "request_id", None)},
    )


@app.exception_handler(IntegrityError)
async def integrity_exception_handler(request: Request, exc: IntegrityError):
    logger.warning("Integrity error: %s", exc.orig)
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": "Database constraint violation (duplicate or referenced record)",
                 "request_id": getattr(request.state, "request_id", None)},
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_exception_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Database error")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Database error", "request_id": getattr(request.state, "request_id", None)},
    )


@app.get("/", tags=["meta"], summary="Service metadata")
def root() -> dict:
    return {
        "service": settings.APP_NAME,
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health", tags=["meta"], summary="Liveness + database connectivity probe")
def health() -> dict:
    from sqlalchemy import text

    from app.database import SessionLocal

    db_ok = True
    detail = "ok"
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:  # pragma: no cover
        db_ok = False
        detail = str(exc)
    return {
        "status": "healthy" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "detail": detail,
        "environment": settings.ENVIRONMENT,
    }


# --- versioned API routers -------------------------------------------------- #
api_prefix = settings.API_V1_PREFIX
app.include_router(auth.router, prefix=api_prefix)
app.include_router(users.router, prefix=api_prefix)
app.include_router(admin.router, prefix=api_prefix)
app.include_router(requests_router.router, prefix=api_prefix)
app.include_router(rpa.router, prefix=api_prefix)
app.include_router(audit.router, prefix=api_prefix)
