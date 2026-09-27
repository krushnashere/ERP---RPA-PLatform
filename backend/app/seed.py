"""Seed script: creates a realistic demo dataset (idempotent).

    python -m app.seed            # create org, users, workflows, processes, requests
    python -m app.seed --reset    # drop everything first (dev only)

Demo logins (password for all): Passw0rd!
    super admin : admin@acme-erp.com        / Admin@123
    admin       : priya.admin@acme-erp.com
    manager     : rahul.manager@acme-erp.com
    employee    : anita.emp@acme-erp.com
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import timedelta

from sqlalchemy import func, select

from app.config import settings
from app.core.security import hash_password
from app.database import Base, SessionLocal, engine, init_db
from app.enums import Priority, RequestStatus, RpaJobStatus, StepStatus, UserRole
from app.models import (
    ApprovalRequest,
    ApprovalStep,
    Attachment,
    AuditLog,
    Department,
    RequestComment,
    RequestType,
    RpaJob,
    RpaJobLog,
    RpaProcess,
    User,
)
from app.models.base import utcnow
from app.services import rpa_queue, workflow

DEPARTMENTS = [
    ("Finance", "FIN", "CC-1001", 5_000_000),
    ("Human Resources", "HR", "CC-1002", 1_500_000),
    ("Information Technology", "IT", "CC-1003", 8_000_000),
    ("Procurement", "PROC", "CC-1004", 12_000_000),
    ("Operations", "OPS", "CC-1005", 3_000_000),
]

USERS = [
    # (employee_id, name, email, password, role, dept_code, designation, manager_employee_id)
    ("ERP-10001", "Aditi Sharma", "admin@acme-erp.com", settings.BOOTSTRAP_ADMIN_PASSWORD, UserRole.SUPER_ADMIN, "IT", "Platform Owner", None),
    ("ERP-10002", "Priya Deshmukh", "priya.admin@acme-erp.com", "Passw0rd!", UserRole.ADMIN, "FIN", "Finance Controller", "ERP-10001"),
    ("ERP-10003", "Rahul Kulkarni", "rahul.manager@acme-erp.com", "Passw0rd!", UserRole.MANAGER, "IT", "Engineering Manager", "ERP-10002"),
    ("ERP-10004", "Sneha Patil", "sneha.manager@acme-erp.com", "Passw0rd!", UserRole.MANAGER, "HR", "HR Manager", "ERP-10002"),
    ("ERP-10005", "Vikram Joshi", "vikram.manager@acme-erp.com", "Passw0rd!", UserRole.MANAGER, "PROC", "Procurement Manager", "ERP-10002"),
    ("ERP-10006", "Anita Fernandes", "anita.emp@acme-erp.com", "Passw0rd!", UserRole.EMPLOYEE, "IT", "Senior Engineer", "ERP-10003"),
    ("ERP-10007", "Karan Mehta", "karan.emp@acme-erp.com", "Passw0rd!", UserRole.EMPLOYEE, "FIN", "Accounts Executive", "ERP-10002"),
    ("ERP-10008", "Neha Rane", "neha.emp@acme-erp.com", "Passw0rd!", UserRole.EMPLOYEE, "HR", "HR Executive", "ERP-10004"),
    ("ERP-10009", "Arjun Nair", "arjun.emp@acme-erp.com", "Passw0rd!", UserRole.EMPLOYEE, "PROC", "Buyer", "ERP-10005"),
    ("ERP-10010", "Meera Iyer", "meera.emp@acme-erp.com", "Passw0rd!", UserRole.EMPLOYEE, "OPS", "Operations Analyst", "ERP-10003"),
]

PROCESSES = [
    ("create_vendor_in_sap", "Create vendor master in SAP",
     "app.worker.handlers.create_vendor_in_sap", 3, 2.0, 120, {"system": "SAP S/4HANA", "company_code": "1000"}),
    ("generate_monthly_report", "Generate & mail monthly finance report",
     "app.worker.handlers.generate_monthly_report", 4, 3.0, 300, {"recipients": ["finance@acme-erp.com", "cfo@acme-erp.com"]}),
    ("sync_leave_balance", "Post approved leave to HRMS",
     "app.worker.handlers.sync_leave_balance", 3, 2.0, 60, {"system": "HRMS"}),
    ("archive_request_documents", "Archive approved request documents",
     "app.worker.handlers.archive_request_documents", 2, 2.0, 90, {"bucket": "erp-archive-prod"}),
]

REQUEST_TYPES = [
    {
        "code": "PURCHASE_ORDER", "name": "Purchase Order Approval",
        "description": "Goods/services purchase above the petty-cash threshold.",
        "amount_limit": 5_000_000, "sla_hours": 48, "requires_attachment": False,
        "auto_approve_below": None, "rpa": "create_vendor_in_sap",
        "chain": [
            {"level": 1, "role": "manager", "min_amount": 0},
            {"level": 2, "role": "admin", "min_amount": 100_000},
            {"level": 3, "role": "super_admin", "min_amount": 1_000_000},
        ],
    },
    {
        "code": "LEAVE_REQUEST", "name": "Leave Application",
        "description": "Annual / sick leave with HRMS sync after approval.",
        "amount_limit": 0, "sla_hours": 24, "requires_attachment": False,
        "auto_approve_below": None, "rpa": "sync_leave_balance",
        "chain": [{"level": 1, "role": "manager", "min_amount": 0}],
    },
    {
        "code": "EXPENSE_CLAIM", "name": "Expense / Travel Reimbursement",
        "description": "Auto-approved below INR 5,000, else manager sign-off.",
        "amount_limit": 200_000, "sla_hours": 72, "requires_attachment": True,
        "auto_approve_below": 5_000, "rpa": None,
        "chain": [{"level": 1, "role": "manager", "min_amount": 0}, {"level": 2, "role": "admin", "min_amount": 50_000}],
    },
    {
        "code": "VENDOR_ONBOARD", "name": "New Vendor Onboarding",
        "description": "Compliance + finance verification, then SAP vendor creation.",
        "amount_limit": 0, "sla_hours": 96, "requires_attachment": True,
        "auto_approve_below": None, "rpa": "create_vendor_in_sap",
        "chain": [{"level": 1, "role": "manager", "min_amount": 0}, {"level": 2, "role": "admin", "min_amount": 0}],
    },
    {
        "code": "IT_ACCESS", "name": "IT Access / License Request",
        "description": "System access, SaaS seats and hardware.",
        "amount_limit": 500_000, "sla_hours": 24, "requires_attachment": False,
        "auto_approve_below": 10_000, "rpa": "archive_request_documents",
        "chain": [{"level": 1, "role": "manager", "min_amount": 0}, {"level": 2, "role": "admin", "min_amount": 50_000}],
    },
]

SAMPLE_REQUESTS = [
    ("PURCHASE_ORDER", "Dell PowerEdge R760 servers for the Pune DC", 1_450_000, "ERP-10006", "IT", Priority.HIGH,
     {"vendor": {"name": "Dell International Services", "gstin": "27AAACD1234F1Z5"},
      "line_items": [{"sku": "R760-2U", "qty": 4, "unit_price": 325_000}, {"sku": "RAID-960GB", "qty": 8, "unit_price": 18_750}],
      "gl_code": "CAPEX-IT-004", "delivery_weeks": 6}),
    ("PURCHASE_ORDER", "Annual Figma + Jira license renewal", 86_400, "ERP-10010", "OPS", Priority.NORMAL,
     {"vendor": {"name": "Atlassian India Pvt Ltd", "gstin": "29AAFCA1234K1ZQ"},
      "line_items": [{"sku": "FIGMA-ORG", "qty": 40, "unit_price": 1_560}, {"sku": "JIRA-PREM", "qty": 40, "unit_price": 600}],
      "gl_code": "OPEX-SW-011"}),
    ("EXPENSE_CLAIM", "Mumbai client visit - travel and stay", 24_800, "ERP-10007", "FIN", Priority.NORMAL,
     {"trip": {"from": "Pune", "to": "Mumbai", "days": 3}, "breakdown": {"flight": 8_400, "hotel": 12_000, "cab": 4_400}}),
    ("EXPENSE_CLAIM", "Team dinner - Q3 release celebration", 4_200, "ERP-10009", "PROC", Priority.LOW,
     {"breakdown": {"food": 4_200}, "head_count": 14}),
    ("LEAVE_REQUEST", "Annual leave - 5 working days", 0, "ERP-10006", "IT", Priority.NORMAL,
     {"days": 5, "from": "2026-10-12", "to": "2026-10-16", "leave_type": "earned", "handover_to": "meera.emp@acme-erp.com"}),
    ("VENDOR_ONBOARD", "Onboard logistics partner BlueDart Express", 0, "ERP-10009", "PROC", Priority.HIGH,
     {"vendor": {"name": "BlueDart Express Ltd", "gstin": "27AAACB1234M1Z9", "pan": "AAACB1234M"},
      "compliance": {"msme_registered": True, "payment_terms_days": 45}}),
    ("IT_ACCESS", "GitHub Enterprise seat for the new SRE", 18_500, "ERP-10010", "OPS", Priority.LOW,
     {"system": "GitHub Enterprise", "seats": 1, "duration_months": 12}),
    ("PURCHASE_ORDER", "Warehouse racking for Bhiwandi hub", 720_000, "ERP-10009", "PROC", Priority.CRITICAL,
     {"vendor": {"name": "Ace Storage Systems", "gstin": "27AABCA5678P1Z2"},
      "line_items": [{"sku": "RACK-4TIER", "qty": 120, "unit_price": 6_000}], "gl_code": "CAPEX-OPS-002"}),
]


def add_demo_attachment(db, req, requester, filename: str, size: int) -> None:
    """Some workflows (expense claims, vendor onboarding) require a document."""
    db.add(
        Attachment(
            request_id=req.id,
            uploaded_by=requester.id,
            filename=filename,
            storage_key=f"requests/{req.reference_no}/{filename}",
            content_type="application/pdf",
            size_bytes=size,
            checksum_sha256="0" * 64,
        )
    )
    db.flush()


def reset() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    print("!! all tables dropped and recreated")


def seed(verbose: bool = True) -> dict:
    init_db()
    db = SessionLocal()
    stats = {"departments": 0, "users": 0, "processes": 0, "request_types": 0, "requests": 0, "jobs": 0}
    try:
        # ---- departments -------------------------------------------------- #
        dept_by_code: dict[str, Department] = {}
        for name, code, cc, budget in DEPARTMENTS:
            dept = db.scalar(select(Department).where(Department.code == code))
            if dept is None:
                dept = Department(name=name, code=code, cost_center=cc, budget_limit=budget)
                db.add(dept)
                db.flush()
                stats["departments"] += 1
            dept_by_code[code] = dept

        # ---- users -------------------------------------------------------- #
        user_by_emp: dict[str, User] = {}
        for emp_id, name, email, password, role, dept_code, designation, manager_emp in USERS:
            user = db.scalar(select(User).where(User.employee_id == emp_id))
            if user is None:
                user = User(
                    employee_id=emp_id, full_name=name, email=email.lower(),
                    hashed_password=hash_password(password), role=role,
                    designation=designation, department_id=dept_by_code[dept_code].id, is_active=True,
                )
                db.add(user)
                db.flush()
                stats["users"] += 1
            user_by_emp[emp_id] = user
        # second pass for manager links (needs all users to exist)
        for emp_id, _n, _e, _p, _r, _d, _des, manager_emp in USERS:
            if manager_emp:
                user_by_emp[emp_id].manager_id = user_by_emp[manager_emp].id
        db.flush()

        # ---- RPA processes ------------------------------------------------ #
        proc_by_code: dict[str, RpaProcess] = {}
        for code, name, handler, attempts, backoff, timeout, config in PROCESSES:
            proc = db.scalar(select(RpaProcess).where(RpaProcess.code == code))
            if proc is None:
                proc = RpaProcess(
                    code=code, name=name, handler=handler, max_attempts=attempts,
                    backoff_base_seconds=backoff, timeout_seconds=timeout,
                    config_json=json.dumps(config), is_active=True,
                )
                db.add(proc)
                db.flush()
                stats["processes"] += 1
            proc_by_code[code] = proc

        # ---- request types (workflow definitions) ------------------------- #
        rt_by_code: dict[str, RequestType] = {}
        for spec in REQUEST_TYPES:
            rt = db.scalar(select(RequestType).where(RequestType.code == spec["code"]))
            chain = json.dumps(spec["chain"])
            rpa_id = proc_by_code[spec["rpa"]].id if spec.get("rpa") else None
            if rt is None:
                rt = RequestType(
                    code=spec["code"], name=spec["name"], description=spec["description"],
                    amount_limit=spec["amount_limit"], sla_hours=spec["sla_hours"],
                    requires_attachment=spec["requires_attachment"],
                    auto_approve_below=spec["auto_approve_below"],
                    approval_chain_json=chain, rpa_process_id=rpa_id, is_active=True,
                )
                db.add(rt)
                db.flush()
                stats["request_types"] += 1
            else:
                rt.approval_chain_json = chain
                rt.rpa_process_id = rpa_id
            rt_by_code[spec["code"]] = rt

        db.commit()

        # ---- demo requests ------------------------------------------------ #
        existing = db.scalar(select(func.count()).select_from(ApprovalRequest))
        if existing:
            stats["requests"] = int(existing)
            if verbose:
                print(f"already {existing} request(s) in the DB - leaving them alone")
        else:
            random.seed(42)
            for index, (code, title, amount, requester_emp, dept_code, priority, payload) in enumerate(SAMPLE_REQUESTS):
                requester = user_by_emp[requester_emp]
                rt = rt_by_code[code]
                req = workflow.create_request(
                    db, actor=requester, request_type=rt, title=title, description=None,
                    amount=amount, currency="INR", priority=priority,
                    department_id=dept_by_code[dept_code].id, payload=payload,
                )
                req.created_at = utcnow() - timedelta(days=len(SAMPLE_REQUESTS) - index, hours=random.randint(0, 8))
                db.flush()
                if rt.requires_attachment:
                    add_demo_attachment(
                        db, req, requester,
                        f"{rt.code.lower()}_supporting_doc.pdf", random.randint(90_000, 480_000),
                    )

                # give every request a spread of lifecycle states so the UI has data
                if index % 4 == 0:                                   # left as draft
                    pass
                elif index % 4 == 1:                                 # pending with manager
                    workflow.submit_request(db, req=req, actor=requester)
                    db.flush()
                    step = req.current_step
                    if step and step.approver_id:
                        db.add(RequestComment(
                            request_id=req.id, user_id=step.approver_id,
                            body="Please share the vendor quotation along with this.",
                        ))
                elif index % 4 == 2:                                 # fully approved (triggers RPA)
                    workflow.submit_request(db, req=req, actor=requester)
                    db.flush()
                    guard = 0
                    while req.status == RequestStatus.PENDING_APPROVAL and guard < 6:
                        step = req.current_step
                        if not step or not step.approver_id:
                            break
                        approver = db.get(User, step.approver_id)
                        workflow.approve_request(db, req=req, actor=approver, remarks="Approved as per policy")
                        db.flush()
                        guard += 1
                else:                                                # rejected
                    workflow.submit_request(db, req=req, actor=requester)
                    db.flush()
                    step = req.current_step
                    if step and step.approver_id:
                        approver = db.get(User, step.approver_id)
                        workflow.reject_request(
                            db, req=req, actor=approver,
                            reason="Budget for this quarter is exhausted; please re-raise next quarter.",
                        )
                    db.flush()
                stats["requests"] += 1
            db.commit()

            # queue a few standalone jobs so the RPA board is not empty
            admin = user_by_emp["ERP-10002"]
            for code, payload, priority in [
                ("generate_monthly_report", {"report": "finance_monthly.xlsx", "row_count": 480, "recipients": ["cfo@acme-erp.com"]}, 3),
                ("create_vendor_in_sap", {"vendor": {"name": "Simulated failure vendor", "gstin": "27AAACS9999Z1Z5"}, "__simulate_failure__": True}, 7),
                ("archive_request_documents", {"reference_no": "REQ-ARCHIVE-DEMO"}, 5),
            ]:
                rpa_queue.enqueue(db, process_id=proc_by_code[code].id, payload=payload, priority=priority, actor=admin)
                stats["jobs"] += 1
            db.commit()

        if verbose:
            print(json.dumps({"seeded": stats}, indent=2))
            print("\nDemo logins:")
            print("  super_admin : admin@acme-erp.com        / Admin@123")
            print("  admin       : priya.admin@acme-erp.com  / Passw0rd!")
            print("  manager     : rahul.manager@acme-erp.com/ Passw0rd!")
            print("  employee    : anita.emp@acme-erp.com    / Passw0rd!")
        return stats
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the ERP + RPA demo dataset")
    parser.add_argument("--reset", action="store_true", help="drop all tables first (DEV ONLY)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    if args.reset:
        reset()
    seed(verbose=not args.quiet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
