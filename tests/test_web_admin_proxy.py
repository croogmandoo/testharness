"""Unit tests for /admin/proxy route and diagnostics."""
import pytest
from unittest.mock import AsyncMock
from fastapi.testclient import TestClient

from harness.db import Database
from web.auth import make_session_token
from web.csrf import verify_csrf
from web.main import create_app, get_secrets_store


@pytest.fixture
def proxy_app(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()

    import bcrypt
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    # Insert admin user
    db.insert_user({
        "id": "admin-1",
        "username": "admin",
        "display_name": "Admin User",
        "email": "admin@example.com",
        "password_hash": bcrypt.hashpw(b"Pass123!", bcrypt.gensalt()).decode(),
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": now,
        "last_login_at": None,
        "oauth_provider_id": None,
    })
    # Insert viewer user
    db.insert_user({
        "id": "viewer-1",
        "username": "viewer",
        "display_name": "Viewer User",
        "email": "viewer@example.com",
        "password_hash": bcrypt.hashpw(b"Pass123!", bcrypt.gensalt()).decode(),
        "role": "viewer",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": now,
        "last_login_at": None,
        "oauth_provider_id": None,
    })

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()

    cfg = {
        "proxy": {
            "behind_reverse_proxy": True,
            "trusted_proxies": ["127.0.0.1", "10.0.0.0/8"],
            "outbound": {
                "enabled": True,
                "https_proxy": "http://proxy.corp.example.com:8080",
                "no_proxy": "localhost,127.0.0.1",
            },
        },
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=cfg, apps_dir=str(apps_dir))
    return app, db


def test_admin_proxy_view_requires_auth(proxy_app):
    app, _ = proxy_app
    client = TestClient(app)

    # Unauthenticated -> redirect to /auth/login
    r = client.get("/admin/proxy", follow_redirects=False)
    assert r.status_code == 307
    assert "/auth/login" in r.headers.get("location", "")


def test_admin_proxy_view_requires_admin_role(proxy_app):
    app, db = proxy_app
    client = TestClient(app)

    # Log in as viewer -> 403 Forbidden
    from starlette.responses import Response
    from web.auth import make_session_token
    token = make_session_token("viewer-1", get_secrets_store().session_signing_key, 8, 0)
    client.cookies.set("session", token)

    r = client.get("/admin/proxy")
    assert r.status_code == 403


def test_admin_proxy_view_renders_for_admin(proxy_app):
    app, db = proxy_app
    client = TestClient(app)

    token = make_session_token("admin-1", get_secrets_store().session_signing_key, 8, 0)
    client.cookies.set("session", token)

    r = client.get("/admin/proxy", headers={
        "X-Forwarded-For": "198.51.100.42",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": "testharness.internal.company.com",
    })
    assert r.status_code == 200
    html = r.text
    assert "Web Proxy &amp; Gateway" in html or "Web Proxy & Gateway" in html
    assert "198.51.100.42" in html
    assert "HTTPS" in html
    assert "Caddy" in html
    assert "Traefik" in html
    assert "Nginx" in html
    assert "proxy.corp.example.com:8080" in html


def test_admin_proxy_test_connection_success(proxy_app, monkeypatch):
    app, db = proxy_app
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    token = make_session_token("admin-1", get_secrets_store().session_signing_key, 8, 0)
    client.cookies.set("session", token)

    class FakeResponse:
        status_code = 200

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            return FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    r = client.post("/admin/proxy/test", data={
        "target_url": "https://httpbin.org/status/200",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["status_code"] == 200
    assert "proxy.corp.example.com:8080" in data["proxy_used"]


def test_admin_proxy_test_connection_error(proxy_app, monkeypatch):
    app, db = proxy_app
    app.dependency_overrides[verify_csrf] = lambda: None
    client = TestClient(app)

    token = make_session_token("admin-1", get_secrets_store().session_signing_key, 8, 0)
    client.cookies.set("session", token)

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            raise Exception("Connection timed out")

    monkeypatch.setattr("httpx.AsyncClient", FakeAsyncClient)

    r = client.post("/admin/proxy/test", data={
        "target_url": "https://httpbin.org/delay/99",
    })
    assert r.status_code == 502
    data = r.json()
    assert data["ok"] is False
    assert "Connection timed out" in data["error"]
