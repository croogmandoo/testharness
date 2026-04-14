import uuid
import pytest
import yaml
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


def _seed_admin_user(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_test_admin",
        "display_name": "Test Admin",
        "email": None,
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    _seed_admin_user(d)
    return d


@pytest.fixture
def apps_dir(tmp_path):
    d = tmp_path / "apps"
    d.mkdir()
    return d


@pytest.fixture
def client(db, apps_dir):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    _admin = db.get_user_by_username("_test_admin")
    app.dependency_overrides[get_current_user] = lambda: _admin
    return TestClient(app)


@pytest.fixture
def client_with_app(db, apps_dir):
    """Client with one pre-existing app."""
    app_def = {
        "app": "my-api",
        "url": "https://example.com",
        "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
    }
    (apps_dir / "my-api.yaml").write_text(yaml.dump(app_def))
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    _admin = db.get_user_by_username("_test_admin")
    app.dependency_overrides[get_current_user] = lambda: _admin
    return TestClient(app), apps_dir


# --- Mutation API endpoint tests ---

def test_post_api_apps_creates_file_and_returns_201(client, apps_dir):
    app_def = {
        "app": "new-app",
        "url": "https://new.example.com",
        "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
    }
    resp = client.post("/api/apps", json={"app_def": app_def})
    assert resp.status_code == 201
    data = resp.json()
    assert data["app"] == "new-app"
    assert "file" in data
    assert (apps_dir / "new-app.yaml").exists()


def test_post_api_apps_returns_409_on_duplicate(client, apps_dir):
    app_def = {"app": "dup-app", "url": "https://dup.example.com", "tests": []}
    client.post("/api/apps", json={"app_def": app_def})
    resp = client.post("/api/apps", json={"app_def": app_def})
    assert resp.status_code == 409


def test_put_api_apps_updates_file_and_returns_200(client_with_app):
    client, apps_dir = client_with_app
    updated = {"app": "my-api", "url": "https://updated.example.com", "tests": []}
    resp = client.put("/api/apps/my-api", json={"app_def": updated})
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"] == "my-api"
    saved = yaml.safe_load((apps_dir / "my-api.yaml").read_text())
    assert saved["url"] == "https://updated.example.com"


def test_put_api_apps_returns_404_if_not_found(client):
    resp = client.put(
        "/api/apps/nonexistent",
        json={"app_def": {"app": "nonexistent", "url": "https://x.com", "tests": []}},
    )
    assert resp.status_code == 404


def test_delete_api_apps_archives_app_and_returns_200(client_with_app):
    client, apps_dir = client_with_app
    resp = client.delete("/api/apps/my-api")
    assert resp.status_code == 200
    assert "archived" in resp.json()
    assert not (apps_dir / "my-api.yaml").exists()
    assert (apps_dir / "archived" / "my-api.yaml").exists()


def test_delete_api_apps_returns_404_if_not_found(client):
    resp = client.delete("/api/apps/nonexistent")
    assert resp.status_code == 404


def test_post_restore_returns_200_and_restores_file(client_with_app):
    client, apps_dir = client_with_app
    client.delete("/api/apps/my-api")
    resp = client.post("/api/apps/my-api/restore")
    assert resp.status_code == 200
    assert (apps_dir / "my-api.yaml").exists()


def test_post_restore_returns_404_if_not_archived(client):
    resp = client.post("/api/apps/nonexistent/restore")
    assert resp.status_code == 404


def test_delete_permanent_removes_file_and_returns_204(client_with_app):
    client, apps_dir = client_with_app
    client.delete("/api/apps/my-api")  # archive first
    resp = client.delete("/api/apps/my-api/permanent")
    assert resp.status_code == 204
    assert not (apps_dir / "archived" / "my-api.yaml").exists()


def test_delete_permanent_returns_404_if_not_archived(client):
    resp = client.delete("/api/apps/nonexistent/permanent")
    assert resp.status_code == 404


def test_put_api_apps_returns_422_if_app_name_mismatch(client_with_app):
    client, apps_dir = client_with_app
    resp = client.put(
        "/api/apps/my-api",
        json={"app_def": {"app": "different-name", "url": "https://x.com", "tests": []}},
    )
    assert resp.status_code == 422


# --- HTML route tests ---

def test_get_apps_returns_200_html(client):
    resp = client.get("/apps")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_new_returns_200_html(client):
    resp = client.get("/apps/new")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_edit_returns_200_html(client_with_app):
    client, apps_dir = client_with_app
    resp = client.get("/apps/my-api/edit")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_get_apps_edit_returns_404_for_unknown_app(client):
    resp = client.get("/apps/nonexistent/edit")
    assert resp.status_code == 404


# --- API /vars endpoint tests ---


def test_get_vars_returns_list(client_with_app):
    """GET /api/vars returns sorted list of $VAR names found in app YAMLs."""
    client, apps_dir = client_with_app
    # Add another app with a different var
    app_def_2 = {
        "app": "myapp",
        "url": "https://example.com",
        "tests": [
            {
                "name": "login",
                "type": "browser",
                "steps": [{"fill": {"field": "#p", "value": "$SECRET_PASS"}}],
            }
        ],
    }
    (apps_dir / "myapp.yaml").write_text(yaml.dump(app_def_2))
    resp = client.get("/api/vars")
    assert resp.status_code == 200
    data = resp.json()
    assert "vars" in data
    assert "$SECRET_PASS" in data["vars"]
    assert isinstance(data["vars"], list)
    assert data["vars"] == sorted(data["vars"])


def test_get_vars_returns_empty_when_no_apps(client):
    """GET /api/vars returns empty list when no app YAMLs exist."""
    resp = client.get("/api/vars")
    assert resp.status_code == 200
    assert resp.json() == {"vars": []}


def test_get_secrets_returns_200_html(client_with_app):
    """GET /secrets returns 200 HTML (admin-only secrets management page)."""
    client, apps_dir = client_with_app
    resp = client.get("/secrets")
    assert resp.status_code == 200
    assert b"text/html" in resp.headers["content-type"].encode()


def test_get_secrets_does_not_expose_values(client_with_app, monkeypatch):
    """GET /secrets never exposes actual env var values."""
    client, apps_dir = client_with_app
    monkeypatch.setenv("MY_SECRET", "super-secret-value")
    resp = client.get("/secrets")
    assert resp.status_code == 200
    assert b"super-secret-value" not in resp.content


def test_detail_page_shows_run_history_strip(tmp_path):
    """GET /app/{app}/{env} shows a list of recent runs as a strip."""
    from harness.db import Database
    from harness.models import Run
    from web.main import create_app
    from fastapi.testclient import TestClient

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    _seed_admin_user(db)
    for i in range(2):
        run = Run(app="myapp", environment="prod", triggered_by="test")
        db.insert_run(run)
        db.update_run_status(run.id, "complete",
                             started_at=f"2026-01-0{i+1}T00:00:00",
                             finished_at=f"2026-01-0{i+1}T00:01:00")

    config = {"default_environment": "prod", "environments": {"prod": {"label": "Prod"}}}
    app = create_app(db=db, config=config, apps_dir=str(tmp_path / "apps"))
    from web.auth import get_current_user
    _admin = db.get_user_by_username("_test_admin")
    app.dependency_overrides[get_current_user] = lambda: _admin
    client = TestClient(app)
    resp = client.get("/app/myapp/prod")
    assert resp.status_code == 200
    assert b"run-history-strip" in resp.content


def test_detail_page_shows_pending_cards_when_run_is_active(tmp_path):
    """Detail page shows pending test placeholders while a run is in progress."""
    import yaml
    from harness.db import Database
    from harness.models import Run
    from web.main import create_app
    from fastapi.testclient import TestClient

    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    _seed_admin_user(db)
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "running", started_at="2026-01-01T00:00:00")

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app_def = {
        "app": "myapp", "url": "https://x.com",
        "tests": [{"name": "health", "type": "availability"},
                  {"name": "login", "type": "browser", "steps": []}]
    }
    (apps_dir / "myapp.yaml").write_text(yaml.dump(app_def))

    config = {"default_environment": "prod", "environments": {"prod": {"label": "Prod"}}}
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    _admin = db.get_user_by_username("_test_admin")
    app.dependency_overrides[get_current_user] = lambda: _admin
    client = TestClient(app)
    resp = client.get("/app/myapp/prod")
    assert resp.status_code == 200
    assert b"Pending" in resp.content
    assert b"progress-bar" in resp.content
