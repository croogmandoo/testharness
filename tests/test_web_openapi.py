"""Tier-3: OpenAPI/Swagger UI accessible even pre-setup (no users)."""
import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app


@pytest.fixture
def empty_db(tmp_path):
    d = Database(str(tmp_path / "empty.db"))
    d.init_schema()
    return d


def _client(db, apps_dir):
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    return TestClient(app)


def test_openapi_json_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body["openapi"].startswith("3.")
    assert "/api/runs" in body["paths"]


def test_swagger_ui_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/docs")
    assert r.status_code == 200
    assert b"swagger" in r.content.lower() or b"Swagger" in r.content


def test_redoc_reachable_pre_setup(empty_db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    with _client(empty_db, apps_dir) as c:
        r = c.get("/redoc")
    assert r.status_code == 200
