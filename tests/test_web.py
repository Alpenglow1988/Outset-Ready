import re

import pytest
from fastapi.testclient import TestClient

from outset_ready.auth import hash_password
from outset_ready.settings import AppSettings
from outset_ready.storage import connect, list_recent_evidence
from outset_ready.web import create_app


OWNER_EMAIL = "ian@example.com"
OWNER_PASSWORD = "a-long-test-password"
OWNER_PASSWORD_HASH = hash_password(OWNER_PASSWORD)


@pytest.fixture
def settings(tmp_path):
    return AppSettings(
        database_target=tmp_path / "ready.sqlite",
        owner_email=OWNER_EMAIL,
        owner_password_hash=OWNER_PASSWORD_HASH,
        session_secret="test-session-secret-that-is-long-enough",
        secure_cookies=False,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings=settings)) as test_client:
        yield test_client


def csrf_from(response) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def sign_in(client: TestClient, *, next_path: str = "/"):
    login_page = client.get(f"/login?next={next_path}")
    return client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "csrf_token": csrf_from(login_page),
            "next": next_path,
        },
        follow_redirects=False,
    )


def test_dashboard_requires_owner_login(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/"
    login_page = client.get(response.headers["location"])
    assert login_page.status_code == 200
    assert "Private owner access" in login_page.text


def test_owner_can_sign_in_and_see_reference_goal_stack(client):
    response = sign_in(client)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Building a picture" in dashboard.text
    assert "Reach 85 kg" in dashboard.text
    assert "Ultra Mirage El Djerid 50 km" in dashboard.text
    assert "never require them" in dashboard.text
    assert OWNER_EMAIL in dashboard.text
    assert client.get("/static/outset-mark.svg").status_code == 200


def test_invalid_login_is_generic_and_does_not_authenticate(client):
    login_page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": "the-wrong-password",
            "csrf_token": csrf_from(login_page),
        },
    )

    assert response.status_code == 401
    assert "not recognised" in response.text
    assert client.get("/", follow_redirects=False).status_code == 303


def test_login_rejects_missing_csrf(client):
    response = client.post(
        "/login",
        data={
            "email": OWNER_EMAIL,
            "password": OWNER_PASSWORD,
            "csrf_token": "wrong",
        },
    )

    assert response.status_code == 403


def test_manual_evidence_is_owner_scoped_and_persisted(client, settings):
    sign_in(client)
    dashboard = client.get("/")
    response = client.post(
        "/evidence",
        data={
            "recorded_on": "2026-09-03",
            "kind": "weight_kg",
            "value": "91.4",
            "note": "",
            "csrf_token": csrf_from(dashboard),
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with connect(settings.database_target) as conn:
        evidence = list_recent_evidence(conn, user_id=settings.owner_id)
    assert len(evidence) == 1
    assert evidence[0].value == 91.4
    assert "91.4 kg" in client.get("/").text


def test_manual_evidence_rejects_invalid_csrf(client):
    sign_in(client)
    response = client.post(
        "/evidence",
        data={
            "recorded_on": "2026-09-03",
            "kind": "weight_kg",
            "value": "91.4",
            "note": "",
            "csrf_token": "wrong",
        },
    )

    assert response.status_code == 403


def test_goals_api_returns_401_until_authenticated(client):
    assert client.get("/api/goals").status_code == 401
    sign_in(client)
    response = client.get("/api/goals")
    assert response.status_code == 200
    assert response.json()[0]["title"] == "Reach 85 kg"


def test_connections_page_is_private_and_reports_current_boundary(client):
    assert client.get("/connections", follow_redirects=False).status_code == 303
    sign_in(client)

    response = client.get("/connections")

    assert response.status_code == 200
    assert "Not connected" in response.text
    assert "Browser connection and MFA arrive in Build #5" in response.text


def test_logout_clears_owner_session(client):
    sign_in(client)
    dashboard = client.get("/")
    response = client.post(
        "/logout",
        data={"csrf_token": csrf_from(dashboard)},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_external_next_url_is_not_followed(client):
    response = sign_in(client, next_path="//attacker.example")

    assert response.headers["location"] == "/"


def test_health_and_database_readiness_are_separate(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "database": "available",
    }


def test_private_responses_are_not_cached(client):
    response = client.get("/login")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_tampered_session_cookie_does_not_authenticate(client):
    sign_in(client)
    cookie = client.cookies.get("outset_ready_session")
    assert cookie
    client.cookies.set("outset_ready_session", cookie[:-1] + "x")

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303


def test_production_session_cookie_is_secure(settings):
    secure_settings = AppSettings(
        database_target=settings.database_target,
        owner_email=settings.owner_email,
        owner_password_hash=settings.owner_password_hash,
        session_secret=settings.session_secret,
        secure_cookies=True,
    )
    with TestClient(
        create_app(settings=secure_settings),
        base_url="https://testserver",
    ) as secure_client:
        response = sign_in(secure_client)

    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "secure" in cookie
