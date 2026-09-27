# ── Enterprise ERP & RPA Automation Platform — backend ────────────────────────
# Multi-stage: dependencies are resolved in a builder so the runtime image carries
# no compiler toolchain. Runs as a non-root user.
#
#   docker build -t erp-backend .
#   docker run --rm -p 8000:8000 --env-file .env erp-backend            # API
#   docker run --rm --env-file .env erp-backend python -m app.worker     # worker

FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# psycopg2-binary wheels exist for slim, but keep build-essential available in
# case a pin ever needs to compile from source.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# libpq5 is the only runtime native dependency (psycopg2)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

COPY app ./app
COPY pytest.ini .

# uploads + the SQLite file need a writable home when DATABASE_URL is sqlite
RUN mkdir -p /app/uploads && chown -R app:app /app
USER app

EXPOSE 8000

# /health is a real endpoint on the app (used by compose + orchestrators)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
