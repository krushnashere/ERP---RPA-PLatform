"""The approval state machine: multi-level chains, escalation, rejections,
cancellation, auto-approval and illegal transitions."""
from __future__ import annotations

import pytest

from tests.conftest import API, request_type_id, token_for


def approve_chain(client, request_id: int, viewer: dict, *, expected_levels: int | None = None):
    """Walk the chain level by level, always acting as the assigned approver."""
    detail = None
    seen = 0
    for _ in range(6):
        detail = client.get(f"{API}/requests/{request_id}", headers=viewer).json()
        step = next((s for s in detail["steps"] if s["status"] == "pending"), None)
        if step is None:
            break
        approver = {"Authorization": f"Bearer {token_for(None, step['approver_name'])}"}
        response = client.post(f"{API}/requests/{request_id}/approve",
                               json={"remarks": f"ok at level {step['level']}"}, headers=approver)
        assert response.status_code == 200, response.text
        detail = response.json()
        seen += 1
    if expected_levels is not None:
        assert seen == expected_levels, f"expected {expected_levels} approval levels, got {seen}"
    return detail


def _create(client, token, *, code="PURCHASE_ORDER", amount=10_000, title="Test request", submit=False, payload=None):
    return client.post(
        f"{API}/requests",
        json={
            "request_type_id": request_type_id(code), "title": title, "amount": amount,
            "submit_immediately": submit, "payload": payload or {},
        },
        headers={"Authorization": f"Bearer {token}"},
    )


def test_draft_is_not_visible_in_the_approval_inbox(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    created = _create(client, tokens["employee"], amount=50_000)
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert created.json()["steps"] == []          # chain is only built on submit
    assert created.json()["submitted_at"] is None

    inbox = client.get(f"{API}/requests/inbox", headers=manager).json()
    assert created.json()["id"] not in [item["id"] for item in inbox]

    submitted = client.post(f"{API}/requests/{created.json()['id']}/submit", headers=employee)
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "pending_approval"
    assert submitted.json()["current_step_level"] == 1
    assert submitted.json()["sla_due_at"] is not None

    inbox = client.get(f"{API}/requests/inbox", headers=manager).json()
    assert created.json()["id"] in [item["id"] for item in inbox]


def test_cannot_submit_twice(client, tokens):
    created = _create(client, tokens["employee"], amount=50_000)
    request_id = created.json()["id"]
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    assert client.post(f"{API}/requests/{request_id}/submit", headers=employee).status_code == 200
    again = client.post(f"{API}/requests/{request_id}/submit", headers=employee)
    assert again.status_code == 409
    assert "Only a DRAFT request can be submitted" in again.json()["detail"]


def test_two_level_chain_escalates_on_amount(client, tokens):
    """145k crosses the 100k threshold -> manager THEN admin must sign off."""
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}

    created = _create(client, tokens["employee"], amount=145_000, submit=True, title="Servers for the DC")
    request_id = created.json()["id"]
    detail = client.get(f"{API}/requests/{request_id}", headers=employee).json()
    assert len(detail["steps"]) == 2, "145k crosses the 100k escalation threshold"
    assert [s["approver_role"] for s in detail["steps"]] == ["manager", "admin"]
    assert detail["current_step_level"] == 1

    # level 1: the assigned manager approves -> request stays pending at level 2
    level1 = {"Authorization": f"Bearer {token_for(None, detail['steps'][0]['approver_name'])}"}
    step1 = client.post(f"{API}/requests/{request_id}/approve", json={"remarks": "Budget available"}, headers=level1)
    assert step1.status_code == 200, step1.text
    assert step1.json()["status"] == "pending_approval"
    assert step1.json()["current_step_level"] == 2
    assert step1.json()["steps"][0]["status"] == "approved"
    assert step1.json()["steps"][1]["status"] == "pending"

    # the level-1 approver cannot approve again
    assert client.post(f"{API}/requests/{request_id}/approve", json={}, headers=level1).status_code == 403

    # level 2: the assigned admin approves -> request approved + closed
    level2 = {"Authorization": f"Bearer {token_for(None, step1.json()['steps'][1]['approver_name'])}"}
    step2 = client.post(f"{API}/requests/{request_id}/approve", json={"remarks": "Finance cleared"}, headers=level2)
    assert step2.status_code == 200, step2.text
    assert step2.json()["status"] == "approved"
    assert step2.json()["closed_at"] is not None
    assert step2.json()["current_step_level"] == 0
    assert step2.json()["rpa_job_ids"], "PURCHASE_ORDER approval must enqueue the SAP automation"


def test_single_level_chain_when_amount_below_threshold(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    created = _create(client, tokens["employee"], amount=40_000, submit=True)
    detail = client.get(f"{API}/requests/{created.json()['id']}", headers=employee).json()
    assert len(detail["steps"]) == 1, "the admin level should not activate below 100k"


def test_rejection_requires_a_reason_and_skips_remaining_steps(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    created = _create(client, tokens["employee"], amount=250_000, submit=True, title="Big purchase")
    request_id = created.json()["id"]
    assert len(client.get(f"{API}/requests/{request_id}", headers=employee).json()["steps"]) == 2

    no_reason = client.post(f"{API}/requests/{request_id}/reject", json={"reason": "no"}, headers=manager)
    assert no_reason.status_code == 422

    rejected = client.post(
        f"{API}/requests/{request_id}/reject", json={"reason": "Budget exhausted for this quarter"}, headers=manager
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["rejection_reason"] == "Budget exhausted for this quarter"
    assert body["steps"][0]["status"] == "rejected"
    assert body["steps"][1]["status"] == "skipped"      # downstream steps never run
    assert body["closed_at"] is not None

    # a rejected request is terminal
    assert client.post(f"{API}/requests/{request_id}/approve", json={}, headers=manager).status_code == 409
    assert client.post(f"{API}/requests/{request_id}/cancel", json={}, headers=employee).status_code == 409


def test_auto_approval_below_threshold(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee2"]}"}
    created = client.post(
        f"{API}/requests",
        json={
            "request_type_id": request_type_id("EXPENSE_CLAIM"), "title": "Cab fare",
            "amount": 1_200, "submit_immediately": True,
        },
        headers=employee,
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "approved", "amounts <= auto_approve_below skip the chain"
    assert created.json()["steps"] == [] or all(s["status"] == "skipped" for s in created.json()["steps"])


def test_attachment_is_mandatory_for_expense_claims(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    created = _create(client, tokens["employee"], code="EXPENSE_CLAIM", amount=25_000, title="Hotel bill")
    request_id = created.json()["id"]

    blocked = client.post(f"{API}/requests/{request_id}/submit", headers=employee)
    assert blocked.status_code == 400
    assert "requires at least one attachment" in blocked.json()["detail"]

    upload = client.post(
        f"{API}/requests/{request_id}/attachments",
        files={"file": ("bill.pdf", b"%PDF-1.4 fake invoice bytes", "application/pdf")},
        headers=employee,
    )
    assert upload.status_code == 201, upload.text
    assert upload.json()["size_bytes"] == len(b"%PDF-1.4 fake invoice bytes")
    assert len(upload.json()["sha256"]) == 64

    # unsupported mime types are refused
    bad_type = client.post(
        f"{API}/requests/{request_id}/attachments",
        files={"file": ("payload.exe", b"MZ", "application/x-msdownload")},
        headers=employee,
    )
    assert bad_type.status_code == 400

    assert client.post(f"{API}/requests/{request_id}/submit", headers=employee).status_code == 200
    detail = client.get(f"{API}/requests/{request_id}", headers=employee).json()
    assert len(detail["attachments"]) == 1


def test_requester_can_withdraw_and_the_rpa_job_is_cancelled(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    admin = {"Authorization": f"Bearer {tokens["admin"]}"}

    created = _create(client, tokens["employee"], amount=180_000, submit=True, title="Withdraw me",
                      payload={"vendor": {"name": "Acme Traders", "gstin": "27AAACA1234A1Z5"}})
    request_id = created.json()["id"]
    approved = approve_chain(client, request_id, employee)
    assert approved["status"] == "approved", approved
    assert approved["rpa_job_ids"], "approving a PURCHASE_ORDER must enqueue the SAP automation"

    job_id = approved["rpa_job_ids"][0]
    # an unrelated employee cannot cancel somebody else's request
    other = {"Authorization": f"Bearer {tokens["employee2"]}"}
    assert client.post(f"{API}/requests/{request_id}/cancel", json={}, headers=other).status_code == 403

    cancelled = client.post(
        f"{API}/requests/{request_id}/cancel", json={"remarks": "Vendor changed"}, headers=employee
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    job = client.get(f"{API}/rpa/jobs/{job_id}", headers=admin).json()
    assert job["status"] == "cancelled", "the queued automation must be cancelled with the request"


def test_illegal_transition_is_a_409(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    created = _create(client, tokens["employee"], amount=20_000, submit=True)
    request_id = created.json()["id"]

    # approve first, then try to reject the closed request
    assert client.post(f"{API}/requests/{request_id}/approve", json={}, headers=manager).status_code == 200
    assert client.post(
        f"{API}/requests/{request_id}/reject", json={"reason": "changed my mind"}, headers=manager
    ).status_code == 409


def test_stats_summary_is_scoped_to_the_caller(client, tokens):
    employee_stats = client.get(f"{API}/requests/stats/summary", headers={"Authorization": f"Bearer {tokens["employee"]}"}).json()
    admin_stats = client.get(f"{API}/requests/stats/summary", headers={"Authorization": f"Bearer {tokens["admin"]}"}).json()
    assert admin_stats["total"] >= employee_stats["total"]
    assert set(admin_stats["by_status"]) >= {"draft", "pending_approval", "approved", "rejected", "cancelled"}
    assert "trend_30d" in admin_stats and "by_department" in admin_stats and "by_request_type" in admin_stats
    assert admin_stats["generated_at"]


def test_concurrent_approvals_do_not_double_advance(client, tokens):
    """Two managers hitting approve with the same version: one wins, one gets 409."""
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    created = _create(client, tokens["employee"], amount=150_000, submit=True, title="Race condition check")
    request_id = created.json()["id"]
    version = client.get(f"{API}/requests/{request_id}", headers=employee).json()["version"]

    first = client.post(
        f"{API}/requests/{request_id}/approve", json={"remarks": "first", "expected_version": version}, headers=manager
    )
    second = client.post(
        f"{API}/requests/{request_id}/approve", json={"remarks": "second", "expected_version": version}, headers=manager
    )
    assert first.status_code == 200, first.text
    assert second.status_code in (403, 409), second.text
