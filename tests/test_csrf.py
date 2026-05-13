"""Tier 3 contract: CSRF synchronizer tokens guard state-mutating HTML forms.

Bearer-token API routes under /api/* are exempt. Tokens bind to the session
cookie value so they auto-invalidate when the session rotates.
"""
import uuid
from datetime import datetime, timezone

import pytest
import bcrypt
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app
from web.csrf import generate_csrf_token, validate_csrf_token, verify_csrf
from web.auth import get_auth_config, make_session_token


CONFIG = {
    "default_environment": "production",
    "environments": {"production": {"label": "Production"}},
    "auth": {"session_hours": 8, "secure_cookie": False},
}


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def app(db, tmp_path):
    (tmp_path / "apps").mkdir()
    return create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))


@pytest.fixture
def client(app):
    return TestClient(app, follow_redirects=False)


def _make_admin(db, username="alice", password="Secret123!"):
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid,
        "username": username,
        "display_name": username.capitalize(),
        "email": None,
        "password_hash": pw_hash,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return uid


def _login_and_get_session(client, username="alice", password="Secret123!"):
    """Login via the form (with a freshly-generated CSRF token for the empty
    pre-session) and return the resulting session cookie value."""
    # Pre-login the session cookie is empty — token signs the empty string.
    cfg = get_auth_config()
    token = generate_csrf_token("", cfg["signing_key"])
    resp = client.post(
        "/auth/login",
        data={"username": username, "password": password, "csrf_token": token},
    )
    assert resp.status_code in (302, 303), f"login failed: {resp.status_code} {resp.text[:200]}"
    return resp.cookies["session"]


# ── Unit tests for the token helpers ────────────────────────────────────────

def test_generate_and_validate_roundtrip():
    key = b"\x01" * 32
    token = generate_csrf_token("session-abc", key)
    assert validate_csrf_token(token, "session-abc", key) is True


def test_validate_rejects_wrong_session():
    key = b"\x01" * 32
    token = generate_csrf_token("session-A", key)
    assert validate_csrf_token(token, "session-B", key) is False


def test_validate_rejects_wrong_key():
    token = generate_csrf_token("s", b"\x01" * 32)
    assert validate_csrf_token(token, "s", b"\x02" * 32) is False


def test_validate_rejects_empty_token():
    assert validate_csrf_token("", "s", b"\x01" * 32) is False
    assert validate_csrf_token("garbage.value", "s", b"\x01" * 32) is False


# ── Integration: form POSTs ─────────────────────────────────────────────────

def test_missing_csrf_token_returns_403_on_login(client, db):
    """POST /auth/login without csrf_token must be rejected."""
    _make_admin(db)
    resp = client.post("/auth/login", data={"username": "alice", "password": "Secret123!"})
    assert resp.status_code == 403


def test_wrong_session_csrf_token_returns_403_on_logout(client, db):
    """A token signed for one session value cannot be reused with a different
    session cookie."""
    _make_admin(db)
    session = _login_and_get_session(client)
    # Generate a token for a DIFFERENT session value
    cfg = get_auth_config()
    bad_token = generate_csrf_token("not-our-session", cfg["signing_key"])
    client.cookies.set("session", session)
    resp = client.post("/auth/logout", data={"csrf_token": bad_token})
    assert resp.status_code == 403


def test_valid_csrf_token_allows_logout(client, db):
    _make_admin(db)
    session = _login_and_get_session(client)
    cfg = get_auth_config()
    token = generate_csrf_token(session, cfg["signing_key"])
    client.cookies.set("session", session)
    resp = client.post("/auth/logout", data={"csrf_token": token})
    assert resp.status_code in (302, 303)


def test_api_routes_bypass_csrf(db, tmp_path):
    """POST /api/runs with Bearer auth must NOT require csrf_token."""
    import hashlib
    import secrets
    # Seed a user + an API key
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": "u", "display_name": "U", "email": None,
        "password_hash": None, "role": "admin", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    plaintext = "hth_" + secrets.token_urlsafe(30)
    db.insert_api_key({
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "name": "test",
        "key_prefix": plaintext[4:12],
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
    })
    (tmp_path / "apps").mkdir()
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    c = TestClient(app, follow_redirects=False)
    # No csrf_token in body — Bearer auth should still work
    resp = c.post(
        "/api/runs",
        json={"environment": "production"},
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    # 202 (accepted with no apps to run) or 404 (no apps) — anything but 403
    assert resp.status_code != 403


def test_dependency_override_disables_csrf_for_tests(db, tmp_path):
    """Existing test files override verify_csrf with a no-op so they don't
    need to thread tokens through every POST. Verify the override path works."""
    (tmp_path / "apps").mkdir()
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    app.dependency_overrides[verify_csrf] = lambda: None
    _make_admin(db)
    c = TestClient(app, follow_redirects=False)
    # POST without csrf_token would normally 403; with override, login should succeed
    resp = c.post("/auth/login", data={"username": "alice", "password": "Secret123!"})
    assert resp.status_code in (302, 303), (
        f"override failed: {resp.status_code} {resp.text[:200]}"
    )
