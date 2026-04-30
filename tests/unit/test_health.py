"""Health endpoint smoke test."""
from fastapi.testclient import TestClient

from media_processor.api.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # In unit tests Postgres/Redis are not running, so status may be "degraded".
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body


def test_health_includes_dependency_status() -> None:
    client = TestClient(app)
    response = client.get("/health")
    body = response.json()
    assert "dependencies" in body
    assert "postgres" in body["dependencies"]
    assert "redis" in body["dependencies"]
