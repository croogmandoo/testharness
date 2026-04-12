import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from web.main import create_app
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def client(db, tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {"default_environment": "production", "environments": {"production": {"label": "Production"}}}
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    return TestClient(app)


def test_get_apps_empty(client):
    resp = client.get("/api/apps?environment=production")
    assert resp.status_code == 200
    assert resp.json() == []


def test_trigger_run_returns_run_id(db, tmp_path):
    import yaml
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "myapp.yaml").write_text(yaml.dump({
        "app": "myapp", "url": "https://example.com",
        "tests": [{"name": "health", "type": "api", "endpoint": "/h",
                   "method": "GET", "expect_status": 200}]
    }))
    config = {"default_environment": "production", "environments": {"production": {"label": "Production"}}}
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    client = TestClient(app)
    with patch("web.routes.api.run_app", new=AsyncMock(return_value="run-123")):
        resp = client.post("/api/runs", json={"app": "myapp", "environment": "production"})
    assert resp.status_code == 202
    assert "run_id" in resp.json()


def test_trigger_run_rejects_conflict(client, db):
    from harness.models import Run
    run = Run(app="myapp", environment="production", triggered_by="ui", status="running")
    db.insert_run(run)
    db.update_run_status(run.id, "running")
    resp = client.post("/api/runs", json={"app": "myapp", "environment": "production"})
    assert resp.status_code == 409


def test_get_run_status(client, db):
    from harness.models import Run
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    resp = client.get(f"/api/runs/{run.id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


def test_get_run_not_found(client):
    resp = client.get("/api/runs/nonexistent")
    assert resp.status_code == 404


def test_get_results_for_app(client, db):
    from harness.models import Run, TestResult
    run = Run(app="myapp", environment="production", triggered_by="api")
    db.insert_run(run)
    tr = TestResult(run_id=run.id, app="myapp", environment="production",
                    test_name="health", status="pass", duration_ms=100)
    db.insert_test_result(tr)
    resp = client.get("/api/results/myapp/production")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["test_name"] == "health"
    assert results[0]["status"] == "pass"
