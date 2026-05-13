"""Tests for /admin (card grid) + /admin/config (read-only viewer + reload)."""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.auth import get_current_user
from web.csrf import verify_csrf
from web.main import create_app


def _seed_user(db, role="admin"):
    u = {
        "id": str(uuid.uuid4()),
        "username": f"_{role}",
        "display_name": role.capitalize(),
        "email": None, "password_hash": None,
        "role": role, "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(u)
    return u


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "admin.db"))
    d.init_schema()
    return d


def _client(db, user, tmp_path, config=None):
    apps_dir = tmp_path / "apps"
    if not apps_dir.exists():
        apps_dir.mkdir()
    app = create_app(db=db, config=config or {}, apps_dir=str(apps_dir))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


# ── /admin (card grid) ──────────────────────────────────────────────────────

def test_admin_dashboard_renders_for_admin(db, tmp_path):
    admin = _seed_user(db, "admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/admin")
    assert r.status_code == 200
    assert b"Admin" in r.content
    assert b"admin-card" in r.content
    # Each of the expected cards should appear
    for title in (b"Users", b"Workers", b"Audit Log",
                  b"API Keys", b"Secrets", b"CA Certs", b"Config",
                  b"Maintenance", b"Analytics Export"):
        assert title in r.content


def test_admin_dashboard_requires_admin_role(db, tmp_path):
    runner = _seed_user(db, "runner")
    c = _client(db, runner, tmp_path)
    r = c.get("/admin")
    assert r.status_code == 403


def test_admin_dashboard_reflects_real_stats(db, tmp_path):
    admin = _seed_user(db, "admin")
    # Add a second user (will count toward users_active)
    _seed_user(db, "runner")
    db.upsert_runner(name="w1", tags=["eu"], capabilities=["api"])
    db.insert_ca_cert({
        "id": str(uuid.uuid4()),
        "name": "Corp Root",
        "pem_content": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": None,
    })

    c = _client(db, admin, tmp_path)
    r = c.get("/admin")
    assert r.status_code == 200
    # 2 active users seeded; admin + runner
    assert b">2<" in r.content  # users_active rendered as the big number
    # 1 worker registered (online or offline depending on timing; just verify count)
    # 1 CA cert
    assert b"trusted" in r.content


# ── /admin/config (read-only viewer) ────────────────────────────────────────

def test_admin_config_view_renders_yaml(db, tmp_path):
    admin = _seed_user(db, "admin")
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "browser": {"headless": True, "timeout_ms": 30000},
    }
    c = _client(db, admin, tmp_path, config=config)
    r = c.get("/admin/config")
    assert r.status_code == 200
    assert b"default_environment" in r.content
    assert b"production" in r.content
    assert b"timeout_ms" in r.content


def test_admin_config_redacts_sensitive_keys(db, tmp_path):
    admin = _seed_user(db, "admin")
    config = {
        "alerts": {
            "teams": {"webhook_url": "https://teams.example.com/abc123def456ghi789"},
            "email": {
                "smtp_host": "mail.example.com",
                "smtp_port": 587,
                "password": "supersecretpassword12345",
                "username": "alerts@example.com",
            },
            "webhook": {
                "secret": "hmac-signing-secret-here",
                "url": "https://ci.example.com/hook",
            },
        },
        "analytics": {
            "target": {
                "type": "azure_blob",
                "container": "synthetics",
                "connection_string": "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=verysecretkey789;",
            },
        },
    }
    c = _client(db, admin, tmp_path, config=config)
    r = c.get("/admin/config")
    body = r.text
    # Sensitive values must NOT appear in plain
    assert "abc123def456ghi789" not in body  # webhook_url body
    assert "supersecretpassword12345" not in body
    assert "hmac-signing-secret-here" not in body
    assert "verysecretkey789" not in body
    # Redacted placeholder appears
    assert "redacted" in body
    # But non-sensitive keys remain visible
    assert "mail.example.com" in body
    assert "synthetics" in body  # container name


def test_admin_config_redaction_handles_short_strings(db, tmp_path):
    admin = _seed_user(db, "admin")
    config = {"alerts": {"webhook": {"secret": "xx"}}}
    c = _client(db, admin, tmp_path, config=config)
    r = c.get("/admin/config")
    body = r.text
    assert "xx" not in body
    assert "****" in body


def test_admin_config_requires_admin_role(db, tmp_path):
    runner = _seed_user(db, "runner")
    c = _client(db, runner, tmp_path)
    r = c.get("/admin/config")
    assert r.status_code == 403


# ── /admin/config/reload ────────────────────────────────────────────────────

def test_admin_config_reload_logs_audit_entry(db, tmp_path):
    admin = _seed_user(db, "admin")
    # No config.yaml at cwd — reload returns 'skipped' but still audit-logs
    c = _client(db, admin, tmp_path)
    r = c.post("/admin/config/reload")
    assert r.status_code == 303
    entries = db.list_audit_log(action="config.reload")
    assert len(entries) == 1
    assert entries[0]["user_id"] == admin["id"]


def test_admin_config_reload_requires_admin_role(db, tmp_path):
    runner = _seed_user(db, "runner")
    c = _client(db, runner, tmp_path)
    r = c.post("/admin/config/reload")
    assert r.status_code == 403


# ── Nav consolidation ──────────────────────────────────────────────────────

def test_nav_shows_single_admin_link_not_five(db, tmp_path):
    admin = _seed_user(db, "admin")
    c = _client(db, admin, tmp_path)
    r = c.get("/admin")
    # The old links should be gone from the nav (they still work as direct URLs)
    body = r.content
    # The new "Admin" link appears...
    assert b'href="/admin" class="nav-link"' in body
    # ...and the old separate links don't appear in the nav (they do appear
    # as cards within the body, so just check the nav region).
    # Crude check: 'nav-link">Users<' was the old pattern; verify it's absent.
    assert b'nav-link">Users<' not in body
    assert b'nav-link">LDAP<' not in body
    assert b'nav-link">CA Certs<' not in body
