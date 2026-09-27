# Enterprise ERP & RPA Automation Platform

A full-stack platform for **business request intake → multi-level approval → automated (RPA) execution → audit**, built with **FastAPI + SQLAlchemy 2.0 + React + SQL + Docker**.

Employees raise requests (vendor creation, leave, expense, access). Each request type carries its own approval chain, so routing is data, not code. When the last approver signs off, the platform enqueues an RPA job; a separate worker process claims it, executes the automation against the target system, retries with exponential backoff on transient failure, dead-letters when retries are exhausted, and writes an audit entry at every step.

```
┌──────────┐   REST/JWT   ┌────────────────┐   SQL    ┌──────────────┐
│  React   │ ───────────▶ │  FastAPI app   │ ───────▶ │ SQLite (dev) │
│  (Vite)  │ ◀─────────── │  6 routers     │ ◀─────── │ Postgres(prod)│
└──────────┘              └───────┬────────┘          └──────▲───────┘
                                  │ enqueue                  │ claim + update
                                  ▼                          │
                          ┌───────────────┐          ┌───────┴────────┐
                          │  rpa_jobs     │ ◀─────── │  RPA worker    │
                          │  (durable Q)  │ ───────▶ │  (own process) │
                          └───────────────┘  lease   └────────────────┘
```

The API and the worker are **separate processes** sharing only the database. The API never blocks on an automation: approving a request returns immediately with `202`-style semantics and the job appears in the queue.

---

## Why the queue is a database table

There is no Redis, no Celery, no message broker. `rpa_jobs` *is* the queue, which buys three things that matter in an ERP:

1. **Durability** — a crashed worker loses nothing; the job row is still there with its lease.
2. **Auditability** — every claim, retry and failure is a row you can `SELECT`, and every transition also writes to `audit_logs`.
3. **Exactly-once claiming** — one atomic conditional `UPDATE ... WHERE status IN ('queued','retry_scheduled') AND next_run_at <= now` decides the winner. On PostgreSQL the candidate `SELECT` adds `FOR UPDATE SKIP LOCKED` so N workers never contend on the same row. SQLite (dev/tests) skips that clause — the dialect guard is in `app/services/rpa_queue.py`.

A **lease** (`leased_until`, default 120 s) means a worker that dies mid-job does not strand it: a sweep requeues any `running` job whose lease expired, and dead-letters it instead if its attempts are already exhausted.

---

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI 0.115, Pydantic v2, `pydantic-settings` |
| ORM / DB | SQLAlchemy 2.0 (typed `Mapped[]`), Alembic-ready; SQLite dev, PostgreSQL prod |
| Auth | JWT access + refresh (`python-jose`), **argon2id** password hashing, RBAC across 4 roles |
| Workers | Standalone `python -m app.worker` process, DB-queue or HTTP mode |
| Frontend | React 18 + Vite, plain CSS, no UI framework |
| Tests | pytest + FastAPI `TestClient` (65 tests) |
| Deploy | Multi-stage Dockerfile, GitHub Actions CI, Vercel (frontend) |

> **Password hashing note:** `passlib` is unmaintained and raises `AttributeError: module 'bcrypt' has no attribute '__about__'` against bcrypt ≥ 4.1. This project hashes with `argon2-cffi` directly and keeps `bcrypt==4.2.1` pinned only to *verify* legacy hashes during migration.

---

## Quick start

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env            # optional — every value has a working default
python -m app.seed --reset      # creates the org, users, request types, processes
uvicorn app.main:app --reload --port 8000
```

Interactive docs: <http://localhost:8000/docs> · health: <http://localhost:8000/health>

### RPA worker (second terminal)

```bash
cd backend
python -m app.worker                       # polls the DB queue
python -m app.worker --once                # drain once and exit (CI / demos)
python -m app.worker --mode http --api-url http://localhost:8000
```

`--mode http` exercises the *decoupled* path: the worker calls the API over HTTP with a worker token instead of touching the DB, which is how you would run it in a separate container or VPC.

### Frontend

```bash
cd frontend
npm install
npm run dev                # http://localhost:5173
```

Vite proxies `/api` and `/health` to `:8000`, so the browser only ever talks to one origin — the same bundle works in dev and behind a reverse proxy in prod. No backend URL is baked into the client.

### Docker

```bash
cd backend
docker build -t erp-backend .
docker run --rm -p 8000:8000 --env-file .env erp-backend              # API
docker run --rm --env-file .env erp-backend python -m app.worker       # worker
```

---

## Demo logins

Password for every account is `Passw0rd!` except the bootstrap admin.

| Role | Email | Password |
|---|---|---|
| `super_admin` | `admin@acme-erp.com` | `Admin@123` |
| `admin` | `priya.admin@acme-erp.com` | `Passw0rd!` |
| `manager` | `rahul.manager@acme-erp.com` | `Passw0rd!` |
| `manager` | `sneha.manager@acme-erp.com` | `Passw0rd!` |
| `employee` | `anita.emp@acme-erp.com` | `Passw0rd!` |
| `employee` | `karan.emp@acme-erp.com` | `Passw0rd!` |

---

## Data model (11 tables)

| Table | Purpose |
|---|---|
| `departments` | Org structure; users belong to one |
| `users` | Credentials, `role`, `is_active`, manager self-reference for approval routing |
| `request_types` | Per-type config: form schema, approval chain definition, linked RPA process |
| `approval_requests` | The request itself: status, priority, requester, current step, amounts |
| `approval_steps` | Materialised chain — one row per level, with `assignee`, `status`, `decided_at` |
| `request_comments` | Threaded discussion attached to a request |
| `attachments` | Uploaded documents (stored under `UPLOAD_DIR`) |
| `audit_logs` | Every state change: actor, action, old/new values, request id |
| `rpa_processes` | Automation definitions: `handler_path`, config JSON, retry policy |
| `rpa_jobs` | **The durable queue**: status, attempts, lease, `next_run_at`, idempotency key |
| `rpa_job_logs` | Per-attempt log lines with severity and metadata |

31 `relationship()` mappings across the two model modules, so a request can be traversed as `request.steps[0].assignee.department.name` without hand-written joins — while the list endpoints still use explicit `select(...).join(...)` where they need filtering and pagination.

### Status machines

```
RequestStatus   draft → submitted → pending_approval → approved → completed
                                     │                  └→ rejected
                                     └→ cancelled
StepStatus      pending → approved | rejected | skipped
RpaJobStatus    queued → running → succeeded
                          │  └→ retry_scheduled → running …
                          └→ failed | cancelled | dead_letter
```

Retries use **exponential backoff with jitter**: `delay = min(base ** attempts, 900s)` then ±10 %, so a fleet of workers retrying the same upstream outage does not synchronise into a thundering herd.

---

## RBAC

| Capability | employee | manager | admin | super_admin |
|---|---|---|---|---|
| Raise / view own requests | ✅ | ✅ | ✅ | ✅ |
| Approve a step assigned to them | — | ✅ | ✅ | ✅ |
| View any request in their department | — | ✅ | ✅ | ✅ |
| Manage users & request types | — | — | ✅ | ✅ |
| RPA process config, job retry/cancel | — | — | ✅ | ✅ |
| Full audit trail, destructive ops | — | — | — | ✅ |

Enforced in one place (`require_roles(...)` dependency) rather than sprinkled through handlers, so a new endpoint cannot forget the check by accident. An employee querying `/requests` gets their own rows via a **query-level scope**, not a post-filter — the DB never returns data the caller may not see.

---

## API surface (52 endpoints)

| Router | Count | Highlights |
|---|---|---|
| `auth` | 4 | login, refresh, me, roles catalogue |
| `users` | 6 | profile, list, create, patch, deactivate |
| `admin` | 9 | request types, departments, approval-chain config |
| `requests` | 15 | CRUD, submit, approve/reject/cancel, comments, attachments, filters, stats |
| `rpa` | 15 | processes, jobs, queue stats, retry, cancel, sweep stale leases, job logs |
| `audit` | 3 | trail with filters, action catalogue, export |

Every list endpoint supports `page`, `size`, `sort_by`, `sort_dir` and typed filters, and returns `{ items, total, page, size, pages }`. Unknown sort columns are rejected with `400` **before** they reach SQL.

---

## Debugging a production issue

Three layers, in the order to use them:

1. **Request id** — every response carries `X-Request-ID` (or echoes the one you sent). Logs are stamped with it:
   ```bash
   grep "rid=422fe45e9a29" app.log
   ```
2. **Audit trail** — `GET /api/v1/audit?entity_type=approval_request&entity_id=42` returns the full old→new value history with actor and timestamp. This answers "who changed what, when" without touching the DB.
3. **Job logs** — `GET /api/v1/rpa/jobs/{id}/logs` returns per-attempt lines including backoff metadata, so a retry storm is visible as data rather than as log noise.

```bash
# which jobs are stuck, and why
curl -s localhost:8000/api/v1/rpa/jobs?status=dead_letter | jq '.items[] | {ref:.job_ref, process:.process_code, error:.error_message, attempts}'

# queue health
curl -s localhost:8000/api/v1/rpa/jobs/stats | jq '{queued, running, dead_letter, success_rate}'

# recover jobs whose worker died
curl -s -X POST localhost:8000/api/v1/rpa/jobs/sweep | jq
```

---

## Tests

```bash
cd backend
pytest -q              # 65 tests
pytest tests/test_rpa.py -v
```

The suite builds its own throwaway SQLite database and covers auth, RBAC boundaries, request CRUD + filtering, the approval state machine, and the RPA queue (claiming, retries, backoff, cancellation, dead-lettering, lease recovery).

---

## Frontend pages

`Login` · `Dashboard` · `Requests` · `NewRequest` · `RequestDetail` · `Inbox` (approvals waiting on you) · `RpaProcesses` · `RpaJobs` (live queue monitor) · `AdminUsers` · `AdminRequestTypes` · `AuditLog` · `NotFound`

Auth state lives in one context; the API client transparently exchanges the refresh token on a `401` and replays the original request once, so an expired access token never surfaces as a broken screen.

---

## Deploying

**Frontend → Vercel.** `frontend/vercel.json` is configured for SPA rewrites and an `/api` rewrite to the deployed backend; `npm run build` outputs `dist/`.

**Backend → any container host.** The Dockerfile is multi-stage (wheels built in a builder, slim runtime, non-root user, `/health` healthcheck). Point `DATABASE_URL` at PostgreSQL and set a real `JWT_SECRET_KEY` (`openssl rand -hex 32`). Run the API and at least one worker as separate services.

**CI.** `.github/workflows/ci.yml` runs backend tests, a seed-and-boot smoke test, a one-shot queue drain, a Docker build, and the frontend production build — in parallel jobs.

---

## Project layout

```
erp-rpa-platform/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI app, middleware, /health, router wiring
│   │   ├── config.py          # pydantic-settings; env-driven, no hardcoding
│   │   ├── database.py        # engine/session; SQLite WAL pragmas
│   │   ├── enums.py           # the status machines above
│   │   ├── auth.py            # JWT issue/verify, role dependencies
│   │   ├── models/            # erp.py (8 tables) + rpa.py (3 tables)
│   │   ├── schemas/           # Pydantic request/response contracts
│   │   ├── routers/           # 6 routers, 52 endpoints
│   │   ├── services/          # workflow.py (routing) + rpa_queue.py (the queue)
│   │   ├── worker/            # claim → execute → retry loop, 4 handlers
│   │   └── seed.py            # realistic demo dataset (idempotent, --reset)
│   ├── tests/                 # 65 pytest tests
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/{pages,components}/
│   ├── vite.config.js         # dev proxy to :8000, allowedHosts: true
│   └── vercel.json
└── .github/workflows/ci.yml
```

### RPA handlers

`create_vendor_in_sap` · `generate_monthly_report` · `sync_leave_balance` · `archive_request_documents`

Handlers are resolved by `handler_path` from the process row, so adding an automation is a DB insert plus a function — no router change. Each receives `(payload, ctx)` where `ctx` carries the attempt number and process config, and one of them (`create_vendor_in_sap`) is seeded to fail with a transient `503` so the retry/backoff path is demonstrable rather than theoretical.
