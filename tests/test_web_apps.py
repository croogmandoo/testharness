import pytest
import yaml
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
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
    """GET /secrets returns 200 HTML with the secrets dependency table."""
    client, apps_dir = client_with_app
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: t\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#p'\n          value: $MY_SECRET\n"
    )
    resp = client.get("/secrets")
    assert resp.status_code == 200
    assert b"$MY_SECRET" in resp.content


def test_get_secrets_does_not_expose_values(client_with_app, monkeypatch):
    """GET /secrets never exposes actual env var values — only names and set/not-set status."""
    client, apps_dir = client_with_app
    monkeypatch.setenv("MY_SECRET", "super-secret-value")
    (apps_dir / "myapp.yaml").write_text(
        "app: myapp\nurl: https://example.com\ntests:\n"
        "  - name: t\n    type: browser\n    steps:\n"
        "      - fill:\n          field: '#p'\n          value: $MY_SECRET\n"
    )
    resp = client.get("/secrets")
    assert resp.status_code == 200
    assert b"super-secret-value" not in resp.content
    assert b"$MY_SECRET" in resp.content
