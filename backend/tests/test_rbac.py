"""RBAC: role-based access control on every sensitive route."""
from __future__ import annotations

from tests.conftest import API, request_type_id


def test_employee_cannot_list_users(client, tokens):
    response = client.get(f"{API}/users", headers={"Authorization": f"Bearer {tokens['employee']}"})
    assert response.status_code == 403


def test_manager_and_admin_can_list_users(client, tokens):
    for role in ("manager", "admin", "super"):
        response = client.get(f"{API}/users", headers={"Authorization": f"Bearer {tokens[role]}"})
        assert response.status_code == 200, role
        assert response.json()["total"] >= 6


def test_employee_cannot_create_users(client, tokens):
    payload = {
        "full_name": "Sneaky User", "email": "sneaky@test-erp.com",
        "employee_id": "ERP-88001", "role": "employee", "password": "Passw0rd!",
    }
    response = client.post(f"{API}/users", json=payload, headers={"Authorization": f"Bearer {tokens['employee']}"})
    assert response.status_code == 403


def test_admin_cannot_grant_admin_role(client, tokens):
    payload = {
        "full_name": "Would Be Admin", "email": "wannabe@test-erp.com",
        "employee_id": "ERP-88002", "role": "admin", "password": "Passw0rd!",
    }
    denied = client.post(f"{API}/users", json=payload, headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert denied.status_code == 403

    allowed = client.post(f"{API}/users", json=payload, headers={"Authorization": f"Bearer {tokens['super']}"})
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["role"] == "admin"


def test_row_level_security_employees_see_only_their_own_requests(client, tokens):
    employee = {"Authorization": f"Bearer {tokens['employee']}"}
    other = {"Authorization": f"Bearer {tokens['employee2']}"}

    created = client.post(
        f"{API}/requests",
        json={"request_type_id": request_type_id("LEAVE_REQUEST"), "title": "My private leave", "amount": 0},
        headers=employee,
    )
    assert created.status_code == 201, created.text
    mine = created.json()["id"]

    own_list = client.get(f"{API}/requests?size=100", headers=employee).json()
    assert mine in [r["id"] for r in own_list["items"]]

    other_list = client.get(f"{API}/requests?size=100", headers=other).json()
    assert mine not in [r["id"] for r in other_list["items"]]

    # ...and a direct GET is blocked too
    assert client.get(f"{API}/requests/{mine}", headers=other).status_code == 403

    # an admin sees it
    admin_list = client.get(f"{API}/requests?size=100", headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    assert mine in [r["id"] for r in admin_list["items"]]


def test_only_current_approver_can_decide(client, tokens):
    employee = {"Authorization": f"Bearer {tokens['employee']}"}
    manager = {"Authorization": f"Bearer {tokens['manager']}"}
    other_manager = {"Authorization": f"Bearer {tokens['manager2']}"}

    created = client.post(
        f"{API}/requests",
        json={
            "request_type_id": request_type_id("PURCHASE_ORDER"),
            "title": "Laptop for new joiner", "amount": 85_000, "submit_immediately": True,
        },
        headers=employee,
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["status"] == "pending_approval"

    # manager2 is not in this chain -> 403
    denied = client.post(f"{API}/requests/{request_id}/approve", json={"remarks": "let me in"}, headers=other_manager)
    assert denied.status_code == 403

    # the requester cannot approve their own request
    assert client.post(f"{API}/requests/{request_id}/approve", json={}, headers=employee).status_code == 403

    approved = client.post(f"{API}/requests/{request_id}/approve", json={"remarks": "ok"}, headers=manager)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"  # 85k < 100k -> single level


def test_audit_trail_is_admin_only(client, tokens):
    assert client.get(f"{API}/audit", headers={"Authorization": f"Bearer {tokens['employee']}"}).status_code == 403
    assert client.get(f"{API}/audit", headers={"Authorization": f"Bearer {tokens['manager']}"}).status_code == 403
    assert client.get(f"{API}/audit", headers={"Authorization": f"Bearer {tokens['admin']}"}).status_code == 200


def test_rpa_process_management_is_admin_only(client, tokens):
    payload = {
        "code": "test_only_process", "name": "Test process",
        "handler": "app.worker.handlers.sync_leave_balance",
    }
    assert client.post(f"{API}/rpa/processes", json=payload, headers={"Authorization": f"Bearer {tokens['manager']}"}).status_code == 403
    created = client.post(f"{API}/rpa/processes", json=payload, headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert created.status_code == 201, created.text


def test_admin_cannot_delete_own_account(client, tokens):
    me = client.get(f"{API}/users/me", headers={"Authorization": f"Bearer {tokens['admin']}"}).json()
    response = client.delete(f"{API}/users/{me['id']}", headers={"Authorization": f"Bearer {tokens['admin']}"})
    assert response.status_code == 400


def test_duplicate_employee_id_and_email_are_rejected(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    payload = {
        "full_name": "Copy Cat", "email": "employee@test-erp.com",
        "employee_id": "ERP-99001", "role": "employee", "password": "Passw0rd!",
    }
    response = client.post(f"{API}/users", json=payload, headers=admin)
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]
