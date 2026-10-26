"""Tests for multi-environment selector UI and persistent cookie filtering."""

import uuid
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app


def _seed_admin(db: Database) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "admin_env_test",
        "display_name": "Admin Env Test",
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
def multi_env_client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()

    config = {
        "default_environment": "production",
        "environments": {
            "production": {"label": "Production"},
            "staging": {"label": "Staging (QA)"},
            "preview": {"label": "Preview"},
        },
    }

    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(app)


def test_dashboard_defaults_to_default_environment(multi_env_client):
    resp = multi_env_client.get("/")
    assert resp.status_code == 200
    assert 'class="env-chip env-chip--active"' in resp.text
    assert "Production" in resp.text
    assert "Staging (QA)" in resp.text


def test_dashboard_query_param_sets_cookie_and_activates_chip(multi_env_client):
    resp = multi_env_client.get("/?environment=staging")
    assert resp.status_code == 200
    assert "selected_environment" in resp.cookies
    assert resp.cookies["selected_environment"] == "staging"
    # Ensure active chip is staging
    assert 'href="/?environment=staging"\n         class="env-chip env-chip--active"' in resp.text or \
           'class="env-chip env-chip--active">\n        Staging (QA)' in resp.text


def test_dashboard_reads_persisted_cookie_on_subsequent_request(multi_env_client):
    # Pass cookie directly
    resp = multi_env_client.get("/", cookies={"selected_environment": "preview"})
    assert resp.status_code == 200
    # Preview should be the active chip
    assert "Preview" in resp.text
    assert 'class="env-chip env-chip--active">\n        Preview' in resp.text


def test_dashboard_invalid_environment_falls_back(multi_env_client):
    resp = multi_env_client.get("/?environment=nonexistent_zone")
    assert resp.status_code == 200
    # Falls back to default (production)
    assert 'class="env-chip env-chip--active">\n        Production' in resp.text


def test_app_detail_sets_cookie_and_renders_switcher(multi_env_client):
    resp = multi_env_client.get("/app/testapp/staging")
    assert resp.status_code == 200
    assert "selected_environment" in resp.cookies
    assert resp.cookies["selected_environment"] == "staging"
    assert 'aria-label="Switch environment"' in resp.text
    assert 'href="/app/testapp/production"' in resp.text
    assert 'href="/app/testapp/staging"' in resp.text
