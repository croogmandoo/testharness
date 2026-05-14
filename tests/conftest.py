"""Shared fixtures for the test suite.

Most tests need the same FastAPI test setup: a fresh DB, an empty apps/
directory, and a TestClient with auth + CSRF bypassed. These fixtures
consolidate that boilerplate so individual test files don't each rebuild
the wheel.

Fixtures defined here are available to every test by default. Tests that
need a different shape (e.g. real auth flow, real CSRF, a custom config,
or a non-admin role) simply define a fixture with the same name locally
— pytest's fixture lookup prefers the closest definition.
"""
import bcrypt
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app

DEFAULT_CONFIG = {
    "default_environment": "production",
    "environments": {"production": {"label": "Production"}},
    "auth": {"session_hours": 8, "secure_cookie": False},
}


@pytest.fixture
def db(tmp_path):
    """Fresh SQLite DB per test in tmp_path."""
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def apps_dir(tmp_path):
    """Empty apps/ directory per test."""
    p = tmp_path / "apps"
    p.mkdir(exist_ok=True)
    return p


@pytest.fixture
def app(db, apps_dir):
    """Bare FastAPI app instance — no auth overrides, no CSRF bypass.
    Use this when a test needs to assert on real auth/CSRF behavior."""
    return create_app(db=db, config=DEFAULT_CONFIG, apps_dir=str(apps_dir))


@pytest.fixture
def admin_user(db):
    """Insert an admin user into db. Returns the user dict."""
    pw = bcrypt.hashpw(b"Secret123!", bcrypt.gensalt()).decode()
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid,
        "username": "alice",
        "display_name": "Alice",
        "email": None,
        "password_hash": pw,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return db.get_user_by_username("alice")


@pytest.fixture
def authed_app(app, admin_user):
    """App with get_current_user overridden to return admin_user and CSRF bypassed."""
    from web.auth import get_current_user
    from web.csrf import verify_csrf
    app.dependency_overrides[get_current_user] = lambda: admin_user
    app.dependency_overrides[verify_csrf] = lambda: None
    return app


@pytest.fixture
def client(authed_app):
    """TestClient bound to the authenticated app with follow_redirects=False."""
    return TestClient(authed_app, follow_redirects=False)
