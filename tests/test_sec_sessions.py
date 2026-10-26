"""Security hardening WP6: revocable sessions and user-management policy.

Session cookies carry ``users.session_version``; logout, password reset,
role change and deactivation bump it, which revokes every cookie issued
before the bump.
"""
import sqlite3
import uuid
from datetime import datetime, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

from harness.db import Database


def _insert_user(db, username, password="Secret123!", role="read_only", is_active=1):
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid,
        "username": username,
        "display_name": username.title(),
        "email": None,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode(),
        "role": role,
        "auth_provider": "local",
        "role_override": 0,
        "is_active": is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return db.get_user_by_id(uid)


def _cookie_for(user_id, session_version=0):
    from web.auth import make_session_token, get_auth_config
    cfg = get_auth_config()
    return make_session_token(user_id, cfg["signing_key"], cfg["session_hours"],
                              session_version)


@pytest.fixture
def raw_client(app):
    """Real auth (no get_current_user override), CSRF bypassed."""
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def _whoami(app, cookie):
    """Status of an authenticated API request made with ``cookie``."""
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("session", cookie)
    return c.get("/api/apps").status_code


# ── Token format ──────────────────────────────────────────────────────────────

def test_token_round_trips_version():
    from web.auth import make_session_token, load_session_claims, load_session_token
    key = b"k" * 32
    tok = make_session_token("u1", key, 8, session_version=3)
    assert load_session_claims(tok, key, 8) == ("u1", 3)
    assert load_session_token(tok, key, 8) == "u1"


def test_legacy_string_token_reads_as_version_zero():
    from web.auth import load_session_claims
    key = b"k" * 32
    legacy = URLSafeTimedSerializer(key).dumps("u1")
    assert load_session_claims(legacy, key, 8) == ("u1", 0)


@pytest.mark.parametrize("payload", [["u1"], ["u1", "2"], ["u1", True], [1, 0], {"u": 1}, ""])
def test_malformed_payload_rejected(payload):
    from web.auth import load_session_claims
    key = b"k" * 32
    tok = URLSafeTimedSerializer(key).dumps(payload)
    assert load_session_claims(tok, key, 8) is None


def test_legacy_cookie_accepted_until_first_bump(app, db):
    from web.auth import get_auth_config
    user = _insert_user(db, "bob")
    legacy = URLSafeTimedSerializer(get_auth_config()["signing_key"]).dumps(user["id"])
    assert _whoami(app, legacy) == 200
    db.bump_session_version(user["id"])
    assert _whoami(app, legacy) == 401


def test_issue_session_cookie_sets_flags_and_version(app, db):
    from fastapi.responses import Response
    from web.auth import issue_session_cookie, load_session_claims, get_auth_config
    user = _insert_user(db, "bob")
    db.bump_session_version(user["id"])
    user = db.get_user_by_id(user["id"])
    resp = Response()
    issue_session_cookie(resp, user)
    header = resp.headers["set-cookie"]
    assert "HttpOnly" in header and "SameSite=lax" in header
    assert f"Max-Age={8 * 3600}" in header
    token = header.split("session=", 1)[1].split(";", 1)[0]
    cfg = get_auth_config()
    assert load_session_claims(token, cfg["signing_key"], 8) == (user["id"], 1)


# ── Revocation ────────────────────────────────────────────────────────────────

def test_login_cookie_has_max_age_and_current_version(raw_client, db):
    user = _insert_user(db, "bob")
    db.bump_session_version(user["id"])
    resp = raw_client.post("/auth/login", data={"username": "bob", "password": "Secret123!"})
    assert resp.status_code == 303
    assert "Max-Age=" in resp.headers["set-cookie"]
    assert _whoami(raw_client.app, resp.cookies["session"]) == 200


def test_logout_revokes_all_sessions(raw_client, db):
    _insert_user(db, "bob")
    first = raw_client.post("/auth/login", data={"username": "bob", "password": "Secret123!"})
    other = TestClient(raw_client.app, follow_redirects=False).post(
        "/auth/login", data={"username": "bob", "password": "Secret123!"})
    cookie_a, cookie_b = first.cookies["session"], other.cookies["session"]
    assert _whoami(raw_client.app, cookie_a) == 200

    raw_client.cookies.set("session", cookie_a)
    resp = raw_client.post("/auth/logout")
    assert resp.status_code == 303

    assert _whoami(raw_client.app, cookie_a) == 401
    assert _whoami(raw_client.app, cookie_b) == 401


def test_logout_with_stale_cookie_does_not_bump(raw_client, db):
    user = _insert_user(db, "bob")
    stale = _cookie_for(user["id"], 0)
    db.bump_session_version(user["id"])
    raw_client.cookies.set("session", stale)
    raw_client.post("/auth/logout")
    assert db.get_user_by_id(user["id"])["session_version"] == 1


def test_logout_without_cookie_is_fine(raw_client):
    assert raw_client.post("/auth/logout").status_code == 303


@pytest.fixture
def admin_client(app, db):
    """Admin signed in through a real session cookie, CSRF bypassed."""
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    admin = _insert_user(db, "root", role="admin")
    c = TestClient(app, follow_redirects=False)
    c.cookies.set("session", _cookie_for(admin["id"]))
    c.admin = admin
    return c


def _edit(client, user, **overrides):
    data = {
        "display_name": user["display_name"], "email": "",
        "role": user["role"], "role_override": 0,
        "is_active": user["is_active"], "password": "",
    }
    data.update(overrides)
    return client.post(f"/users/{user['id']}/edit", data=data)


@pytest.mark.parametrize("change", [
    {"password": "NewSecret456!"},
    {"role": "runner"},
    {"is_active": 0},
])
def test_user_update_revokes_target_sessions(admin_client, db, change):
    bob = _insert_user(db, "bob")
    cookie = _cookie_for(bob["id"])
    assert _whoami(admin_client.app, cookie) == 200
    resp = _edit(admin_client, bob, **change)
    assert resp.status_code == 303
    assert _whoami(admin_client.app, cookie) == 401
    if change.get("is_active", 1):
        # A fresh login picks up the new version and works again.
        fresh = _cookie_for(bob["id"], db.get_user_by_id(bob["id"])["session_version"])
        assert _whoami(admin_client.app, fresh) == 200


def test_cosmetic_user_update_keeps_sessions(admin_client, db):
    bob = _insert_user(db, "bob")
    cookie = _cookie_for(bob["id"])
    assert _edit(admin_client, bob, display_name="Robert").status_code == 303
    assert _whoami(admin_client.app, cookie) == 200
    assert db.get_user_by_id(bob["id"])["session_version"] == 0


def test_admin_changing_own_password_stays_signed_in(admin_client, db):
    admin = admin_client.admin
    old_cookie = admin_client.cookies.get("session")
    resp = _edit(admin_client, admin, password="NewSecret456!")
    assert resp.status_code == 303
    assert "session" in resp.cookies
    assert _whoami(admin_client.app, old_cookie) == 401
    assert _whoami(admin_client.app, resp.cookies["session"]) == 200


# ── Migration ─────────────────────────────────────────────────────────────────

def test_session_version_migration_idempotent_on_old_db(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, "
        "display_name TEXT, email TEXT, password_hash TEXT, role TEXT NOT NULL, "
        "auth_provider TEXT NOT NULL DEFAULT 'local', role_override INTEGER DEFAULT 0, "
        "is_active INTEGER DEFAULT 1, created_at TEXT NOT NULL, last_login_at TEXT)"
    )
    conn.execute(
        "INSERT INTO users (id, username, role, created_at) "
        "VALUES ('u1', 'old', 'admin', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    db = Database(path)
    db.init_schema()
    db.init_schema()
    assert db.get_user_by_id("u1")["session_version"] == 0
    db.bump_session_version("u1")
    db.init_schema()
    assert db.get_user_by_id("u1")["session_version"] == 1
