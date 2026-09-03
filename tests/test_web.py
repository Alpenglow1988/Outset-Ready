from fastapi.testclient import TestClient

from outset_ready.storage import connect, list_recent_evidence
from outset_ready.web import create_app


def test_dashboard_shows_reference_goal_stack(tmp_path):
    client = TestClient(create_app(tmp_path / "ready.sqlite"))

    response = client.get("/")

    assert response.status_code == 200
    assert "Building a picture" in response.text
    assert "Reach 85 kg" in response.text
    assert "Ultra Mirage El Djerid 50 km" in response.text
    assert "never require them" in response.text
    assert "/static/outset-mark.svg" in response.text
    assert client.get("/static/outset-mark.svg").status_code == 200


def test_manual_evidence_is_persisted_and_rendered(tmp_path):
    db_path = tmp_path / "ready.sqlite"
    client = TestClient(create_app(db_path))

    response = client.post(
        "/evidence",
        data={
            "recorded_on": "2026-09-03",
            "kind": "weight_kg",
            "value": "91.4",
            "note": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with connect(db_path) as conn:
        evidence = list_recent_evidence(conn)
    assert len(evidence) == 1
    assert evidence[0].value == 91.4
    assert "91.4 kg" in client.get("/").text


def test_health_endpoint(tmp_path):
    client = TestClient(create_app(tmp_path / "ready.sqlite"))
    assert client.get("/health").json() == {"status": "ok"}
