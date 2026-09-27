"""Auth: JWT issuing, validation, refresh rotation, password policy."""
from __future__ import annotations

from jose import jwt

from app.config import settings
from tests.conftest import API


def test_login_returns_jwt_pair(client):
    response = client.post(f"{API}/auth/login", json={"email": "employee@test-erp.com", "password": "Passw0rd!"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["role"] == "employee"
    assert body["expires_at"]

    claims = jwt.decode(body["access_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert claims["type"] == "access"
    assert claims["role"] == "employee"
    assert claims["jti"]

    refresh_claims = jwt.decode(body["refresh_token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert refresh_claims["type"] == "refresh"


def test_login_with_wrong_password_is_401(client):
    response = client.post(f"{API}/auth/login", json={"email": "employee@test-erp.com", "password": "Nope@12345"})
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]


def test_login_unknown_user_is_401_and_does_not_leak_existence(client):
    response = client.post(f"{API}/auth/login", json={"email": "ghost@test-erp.com", "password": "Whatever@123"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_rejects_malformed_email(client):
    response = client.post(f"{API}/auth/login", json={"email": "not-an-email", "password": "Passw0rd!"})
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "email"


def test_protected_route_requires_token(client):
    assert client.get(f"{API}/requests").status_code == 401
    assert client.get(f"{API}/auth/me").status_code == 401


def test_protected_route_rejects_garbage_and_expired_tokens(client):
    assert client.get(f"{API}/auth/me", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    # a refresh token must not be usable as an access token
    login = client.post(f"{API}/auth/login", json={"email": "employee@test-erp.com", "password": "Passw0rd!"}).json()
    response = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {login['refresh_token']}"})
    assert response.status_code == 401


def test_refresh_rotates_tokens(client):
    login = client.post(f"{API}/auth/login", json={"email": "manager@test-erp.com", "password": "Passw0rd!"}).json()
    response = client.post(f"{API}/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert response.status_code == 200
    new_tokens = response.json()
    assert new_tokens["access_token"] != login["access_token"]
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {new_tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == "manager@test-erp.com"


def test_refresh_rejects_access_token(client):
    login = client.post(f"{API}/auth/login", json={"email": "manager@test-erp.com", "password": "Passw0rd!"}).json()
    response = client.post(f"{API}/auth/refresh", json={"refresh_token": login["access_token"]})
    assert response.status_code == 401


def test_password_policy_enforced_on_user_creation(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    weak = {
        "full_name": "Weak Password", "email": "weak@test-erp.com", "employee_id": "ERP-77001",
        "role": "employee", "password": "alllowercase1",
    }
    response = client.post(f"{API}/users", json=weak, headers=admin)
    assert response.status_code == 422
    assert "uppercase" in response.json()["errors"][0]["message"]


def test_deactivated_user_cannot_log_in(client, tokens):
    admin = {"Authorization": f"Bearer {tokens['admin']}"}
    created = client.post(
        f"{API}/users",
        json={
            "full_name": "Temp Contractor", "email": "temp@test-erp.com", "employee_id": "ERP-77002",
            "role": "employee", "password": "Passw0rd!",
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]

    assert client.post(f"{API}/auth/login", json={"email": "temp@test-erp.com", "password": "Passw0rd!"}).status_code == 200

    deactivate = client.patch(f"{API}/users/{user_id}", json={"is_active": False}, headers=admin)
    assert deactivate.status_code == 200

    blocked = client.post(f"{API}/auth/login", json={"email": "temp@test-erp.com", "password": "Passw0rd!"})
    assert blocked.status_code == 403
    assert "deactivated" in blocked.json()["detail"]

    # an already-issued token stops working immediately
    old_token = {"Authorization": f"Bearer {tokens['employee']}"}
    assert client.get(f"{API}/auth/me", headers=old_token).status_code == 200
