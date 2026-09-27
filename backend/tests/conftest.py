"""Shared pytest fixtures: an isolated SQLite database per test session."""
from __future__ import annotations

import os
import tempfile

# Configure the environment BEFORE the app modules read the settings.
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="erp-test-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB}"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-used-in-production-000000"
os.environ["ENVIRONMENT"] = "test"
os.environ["DEBUG"] = "false"
os.environ["RPA_BACKOFF_BASE_SECONDS"] = "1.2"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine, get_db, init_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Department, RequestType, RpaProcess, User  # noqa: E402

API = settings.API_V1_PREFIX


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    init_db()
    _seed_reference_data()
    yield
    Base.metadata.drop_all(bind=engine)


def _seed_reference_data() -> None:
    db: Session = SessionLocal()
    try:
        it = Department(name="Information Technology", code="IT", cost_center="CC-1003", budget_limit=1_000_000)
        fin = Department(name="Finance", code="FIN", cost_center="CC-1001", budget_limit=500_000)
        db.add_all([it, fin])
        db.flush()

        boss = User(
            employee_id="ERP-90001", full_name="Super Admin", email="root@test-erp.com",
            hashed_password=hash_password("Super@123"), role=UserRole.SUPER_ADMIN,
            designation="Platform Owner", department_id=it.id,
        )
        db.add(boss)
        db.flush()

        users = {
            "admin": ("ERP-90002", "Admin Person", "admin@test-erp.com", UserRole.ADMIN, fin.id, boss.id),
            "manager": ("ERP-90003", "Rahul Manager", "manager@test-erp.com", UserRole.MANAGER, it.id, boss.id),
            "manager2": ("ERP-90004", "Sneha Manager", "manager2@test-erp.com", UserRole.MANAGER, fin.id, boss.id),
            "employee": ("ERP-90005", "Anita Employee", "employee@test-erp.com", UserRole.EMPLOYEE, it.id, None),
            "employee2": ("ERP-90006", "Karan Employee", "employee2@test-erp.com", UserRole.EMPLOYEE, fin.id, None),
        }
        created: dict[str, User] = {}
        for key, (emp, name, email, role, dept, manager_id) in users.items():
            user = User(
                employee_id=emp, full_name=name, email=email, hashed_password=hash_password("Passw0rd!"),
                role=role, department_id=dept, is_active=True,
            )
            db.add(user)
            db.flush()
            created[key] = user
        # employees report to the IT manager -> drives automatic approver resolution
        created["employee"].manager_id = created["manager"].id
        created["employee2"].manager_id = created["manager2"].id
        db.flush()

        vendor_proc = RpaProcess(
            code="create_vendor_in_sap", name="Create vendor in SAP",
            handler="app.worker.handlers.create_vendor_in_sap",
            max_attempts=3, backoff_base_seconds=1.2, timeout_seconds=60, config_json="{}",
        )
        flaky_proc = RpaProcess(
            code="flaky_report", name="Flaky report job",
            handler="app.worker.handlers.generate_monthly_report",
            max_attempts=2, backoff_base_seconds=1.1, timeout_seconds=60, config_json="{}",
        )
        db.add_all([vendor_proc, flaky_proc])
        db.flush()

        import json

        db.add_all(
            [
                RequestType(
                    code="PURCHASE_ORDER", name="Purchase Order", amount_limit=5_000_000, sla_hours=48,
                    approval_chain_json=json.dumps(
                        [
                            {"level": 1, "role": "manager", "min_amount": 0},
                            {"level": 2, "role": "admin", "min_amount": 100_000},
                        ]
                    ),
                    rpa_process_id=vendor_proc.id,
                ),
                RequestType(
                    code="EXPENSE_CLAIM", name="Expense Claim", amount_limit=200_000, sla_hours=72,
                    requires_attachment=True, auto_approve_below=5_000,
                    approval_chain_json=json.dumps([{"level": 1, "role": "manager", "min_amount": 0}]),
                ),
                RequestType(
                    code="LEAVE_REQUEST", name="Leave Request", amount_limit=0, sla_hours=24,
                    approval_chain_json=json.dumps([{"level": 1, "role": "manager", "min_amount": 0}]),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str = "Passw0rd!") -> str:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture
def tokens() -> dict:
    with TestClient(app) as client:
        out = {role: login(client, f"{role}@test-erp.com") for role in ("admin", "manager", "manager2", "employee", "employee2")}
        out["super"] = login(client, "root@test-erp.com", "Super@123")
        yield out


def request_type_id(code: str) -> int:
    db = SessionLocal()
    try:
        from sqlalchemy import select

        return db.scalar(select(RequestType.id).where(RequestType.code == code))
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# queue hygiene: the test database is shared for the whole session, so a job
# left behind by one test would be claimed by the next one. Draining first keeps
# every queue test deterministic.
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _drain_rpa_queue():
    from tests.test_rpa import drain_queue

    drain_queue()
    yield


def token_for(tokens: dict, full_name: str) -> str:
    """Find the JWT of a seeded user by display name (the workflow engine picks
    approvers at submit time, so tests must not hard-code who they are)."""
    emails = {
        "Super Admin": ("root@test-erp.com", "Super@123"),
        "Admin Person": ("admin@test-erp.com", "Passw0rd!"),
        "Rahul Manager": ("manager@test-erp.com", "Passw0rd!"),
        "Sneha Manager": ("manager2@test-erp.com", "Passw0rd!"),
        "Anita Employee": ("employee@test-erp.com", "Passw0rd!"),
        "Karan Employee": ("employee2@test-erp.com", "Passw0rd!"),
    }
    email, password = emails[full_name]
    with TestClient(app) as client:
        return login(client, email, password)
