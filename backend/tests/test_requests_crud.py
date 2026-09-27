"""CRUD, joins, filtering, sorting, pagination and search over business data."""
from __future__ import annotations

import pytest

from tests.conftest import API, request_type_id


@pytest.fixture
def seeded_requests(client, tokens):
    """Creates a small, varied dataset owned by the IT employee."""
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    created = []
    specs = [
        ("PURCHASE_ORDER", "Monitors for the design team", 145_000, "high", True),
        ("PURCHASE_ORDER", "SSD upgrades", 32_000, "normal", True),
        ("LEAVE_REQUEST", "Work from home week", 0, "low", False),
    ]
    for code, title, amount, priority, submit in specs:
        response = client.post(
            f"{API}/requests",
            json={
                "request_type_id": request_type_id(code), "title": title, "amount": amount,
                "priority": priority, "submit_immediately": submit,
                "payload": {"line_items": [{"sku": title[:6].upper(), "qty": 2, "unit_price": amount / 2 or 1}]},
            },
            headers=employee,
        )
        assert response.status_code == 201, response.text
        created.append(response.json())
    return created


def test_create_returns_joined_fields(client, tokens, seeded_requests):
    first = seeded_requests[0]
    assert first["reference_no"].startswith("REQ-")
    assert first["request_type_code"] == "PURCHASE_ORDER"       # join -> request_types
    assert first["requester_name"] == "Anita Employee"          # join -> users
    assert first["department_name"] == "Information Technology"  # join -> departments
    assert first["status"] == "pending_approval"
    assert first["sla_due_at"] is not None


def test_get_detail_includes_steps_payload_and_version(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    detail = client.get(f"{API}/requests/{seeded_requests[0]['id']}", headers=employee).json()
    assert detail["payload"]["line_items"][0]["qty"] == 2
    assert detail["version"] == 2  # created (1) + submitted (2)
    assert detail["steps"], "the approval chain should have been materialised"
    assert detail["steps"][0]["approver_role"] == "manager"
    assert detail["steps"][0]["approver_name"] == "Rahul Manager"


def test_filter_by_status(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    pending = client.get(f"{API}/requests?status=pending_approval&size=100", headers=employee).json()
    assert pending["total"] >= 2
    assert all(item["status"] == "pending_approval" for item in pending["items"])

    drafts = client.get(f"{API}/requests?status=draft&size=100", headers=employee).json()
    assert all(item["status"] == "draft" for item in drafts["items"])
    assert seeded_requests[2]["id"] in [item["id"] for item in drafts["items"]]


def test_filter_by_multiple_statuses(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    response = client.get(f"{API}/requests?status=draft&status=pending_approval&size=100", headers=employee)
    assert response.status_code == 200
    assert {item["status"] for item in response.json()["items"]} <= {"draft", "pending_approval"}


def test_filter_by_amount_range_and_request_type(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    cheap = client.get(f"{API}/requests?max_amount=50000&size=100", headers=employee).json()
    assert all(item["amount"] <= 50_000 for item in cheap["items"])

    expensive = client.get(f"{API}/requests?min_amount=100000&size=100", headers=employee).json()
    assert all(item["amount"] >= 100_000 for item in expensive["items"])

    typed = client.get(
        f"{API}/requests?request_type_id={request_type_id('LEAVE_REQUEST')}&size=100", headers=employee
    ).json()
    assert all(item["request_type_code"] == "LEAVE_REQUEST" for item in typed["items"])


def test_search_matches_reference_title_and_requester(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    by_title = client.get(f"{API}/requests?search=monitors&size=100", headers=employee).json()
    assert by_title["total"] >= 1

    by_reference = client.get(
        f"{API}/requests?search={seeded_requests[1]['reference_no']}", headers=employee
    ).json()
    assert by_reference["total"] == 1
    assert by_reference["items"][0]["id"] == seeded_requests[1]["id"]

    by_requester = client.get(f"{API}/requests?search=anita&size=100", headers=employee).json()
    assert by_requester["total"] >= 3


def test_sorting(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    ascending = client.get(f"{API}/requests?sort_by=amount&sort_dir=asc&size=100", headers=employee).json()
    amounts = [item["amount"] for item in ascending["items"]]
    assert amounts == sorted(amounts)

    descending = client.get(f"{API}/requests?sort_by=amount&sort_dir=desc&size=100", headers=employee).json()
    amounts = [item["amount"] for item in descending["items"]]
    assert amounts == sorted(amounts, reverse=True)


def test_pagination_metadata(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    page1 = client.get(f"{API}/requests?page=1&size=2&sort_by=amount&sort_dir=asc", headers=employee).json()
    page2 = client.get(f"{API}/requests?page=2&size=2&sort_by=amount&sort_dir=asc", headers=employee).json()
    assert page1["size"] == 2 and page1["page"] == 1
    assert page1["total"] >= 3 and page1["pages"] >= 2
    ids1 = {item["id"] for item in page1["items"]}
    ids2 = {item["id"] for item in page2["items"]}
    assert not (ids1 & ids2), "pages must not overlap"


def test_invalid_filter_values_are_rejected(client, tokens):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    assert client.get(f"{API}/requests?status=bogus", headers=employee).status_code == 400
    assert client.get(f"{API}/requests?sort_by=password", headers=employee).status_code == 422
    assert client.get(f"{API}/requests?size=1000", headers=employee).status_code == 422
    assert client.get(f"{API}/requests?min_amount=900&max_amount=10", headers=employee).status_code == 400


def test_update_only_allowed_in_draft_with_optimistic_lock(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    draft = seeded_requests[2]

    ok = client.patch(
        f"{API}/requests/{draft['id']}", json={"title": "WFH week - updated", "expected_version": draft["version"]},
        headers=employee,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["title"] == "WFH week - updated"
    assert ok.json()["version"] == draft["version"] + 1

    # replaying the stale version is a 409, not a silent overwrite
    stale = client.patch(
        f"{API}/requests/{draft['id']}", json={"title": "stale write", "expected_version": draft["version"]},
        headers=employee,
    )
    assert stale.status_code == 409
    assert "Stale update" in stale.json()["detail"]

    # a submitted request can no longer be edited
    submitted = seeded_requests[0]
    locked = client.patch(f"{API}/requests/{submitted['id']}", json={"title": "hack"}, headers=employee)
    assert locked.status_code == 409


def test_comments_and_timeline(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    manager = {"Authorization": f"Bearer {tokens["manager"]}"}
    request_id = seeded_requests[0]["id"]

    comment = client.post(f"{API}/requests/{request_id}/comments", json={"body": "Quote attached in the portal."}, headers=employee)
    assert comment.status_code == 201, comment.text
    assert comment.json()["user_name"] == "Anita Employee"

    assert client.post(f"{API}/requests/{request_id}/comments", json={"body": ""}, headers=employee).status_code == 422

    timeline = client.get(f"{API}/requests/{request_id}/timeline", headers=manager).json()
    kinds = {event["type"] for event in timeline["events"]}
    assert {"audit", "comment", "step"} <= kinds
    assert timeline["events"] == sorted(timeline["events"], key=lambda e: e.get("at") or "")


def test_soft_delete_hides_the_request(client, tokens, seeded_requests):
    employee = {"Authorization": f"Bearer {tokens["employee"]}"}
    admin = {"Authorization": f"Bearer {tokens['super']}"}
    draft_id = seeded_requests[2]["id"]

    # a non-admin cannot delete
    assert client.delete(f"{API}/requests/{draft_id}", headers=employee).status_code == 403

    assert client.delete(f"{API}/requests/{draft_id}", headers=admin).status_code == 204
    assert client.get(f"{API}/requests/{draft_id}", headers=admin).status_code == 404
    remaining = client.get(f"{API}/requests?size=100", headers=admin).json()
    assert draft_id not in [item["id"] for item in remaining["items"]]


def test_department_crud(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    created = client.post(
        f"{API}/admin/departments",
        json={"name": "Legal & Compliance", "code": "LEGAL", "cost_center": "CC-1009", "budget_limit": 900_000},
        headers=admin,
    )
    assert created.status_code == 201, created.text
    dept_id = created.json()["id"]

    assert client.post(
        f"{API}/admin/departments", json={"name": "Legal & Compliance", "code": "LEGAL2"}, headers=admin
    ).status_code == 409

    patched = client.patch(f"{API}/admin/departments/{dept_id}", json={"budget_limit": 1_200_000}, headers=admin)
    assert patched.status_code == 200
    assert patched.json()["budget_limit"] == 1_200_000

    listing = client.get(f"{API}/admin/departments", headers=admin).json()
    assert any(d["code"] == "LEGAL" for d in listing)
    assert client.delete(f"{API}/admin/departments/{dept_id}", headers=admin).status_code == 204


def test_request_type_workflow_definition(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    created = client.post(
        f"{API}/admin/request-types",
        json={
            "code": "CAPEX_APPROVAL", "name": "Capex Approval", "amount_limit": 9_000_000,
            "sla_hours": 120,
            "approval_chain": [
                {"level": 2, "role": "admin", "min_amount": 500_000},
                {"level": 1, "role": "manager", "min_amount": 0},
            ],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    # the chain is stored sorted by level even though it was sent out of order
    assert [level["level"] for level in created.json()["approval_chain"]] == [1, 2]

    fetched = client.get(f"{API}/admin/request-types/CAPEX_APPROVAL", headers=admin)
    assert fetched.status_code == 200
    assert fetched.json()["sla_hours"] == 120

    # duplicate level numbers are rejected
    bad = client.post(
        f"{API}/admin/request-types",
        json={
            "code": "BROKEN_CHAIN", "name": "Broken", "amount_limit": 10,
            "approval_chain": [{"level": 1, "role": "manager"}, {"level": 1, "role": "admin"}],
        },
        headers=admin,
    )
    assert bad.status_code == 422
