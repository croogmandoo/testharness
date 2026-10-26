"""Security regression tests for login and identity-provider hardening.

Covers: LDAP/local account collision, LDAP input escaping, GitHub OAuth
allowlisting and rename takeover, per-IP login throttling, constant-time
user lookup and the first-run setup token.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import bcrypt
import pytest
from fastapi.testclient import TestClient

from web.main import create_app

LDAP_CFG = {
    "enabled": True,
    "server": "ldap://dc.corp.com",
    "port": 389,
    "use_tls": False,
    "base_dn": "DC=corp,DC=com",
    "user_search_filter": "(sAMAccountName={username})",
    "group_attribute": "memberOf",
    "role_map": {},
    "default_role": "read_only",
}


def _local_user(db, name, role="admin", pw="password123", is_active=1):
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": name, "display_name": name, "email": None,
        "password_hash": bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode(),
        "role": role, "auth_provider": "local", "role_override": 0,
        "is_active": is_active,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return uid


def _ldap_client(db, apps_dir):
    config = {
        "environments": {"production": {"label": "Production"}},
        "auth": {"session_hours": 8, "secure_cookie": False, "ldap": LDAP_CFG},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def _fake_ldap(username, password, ldap_cfg):
    return {"username": username, "display_name": "Directory User",
            "email": None, "role": "read_only"}


# ── #2 LDAP / local account collision ────────────────────────────────────────

def test_ldap_upsert_does_not_take_over_local_admin(db):
    """Regression for the PoC: an LDAP identity named 'admin' must not
    resolve to (or modify) the local admin account."""
    admin_id = _local_user(db, "admin", "admin")
    u = db.upsert_ldap_user("admin", "Some AD user", None, "read_only")
    assert u is None
    row = db.get_user_by_id(admin_id)
    assert row["auth_provider"] == "local"
    assert row["role"] == "admin"
    assert row["display_name"] == "admin"


def test_ldap_login_colliding_with_local_admin_is_401(db, apps_dir, monkeypatch):
    admin_id = _local_user(db, "admin", "admin", pw="real-admin-pw")
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "admin", "password": "ad-password"})
    assert r.status_code == 401
    assert "session" not in r.cookies
    assert db.get_user_by_id(admin_id)["role"] == "admin"
    actions = [e["action"] for e in db.list_audit_log()]
    assert "auth.ldap_conflict" in actions


def test_ldap_login_inactive_user_is_401(db, apps_dir, monkeypatch):
    u = db.upsert_ldap_user("bob", "Bob", None, "read_only")
    db.update_user(u["id"], is_active=0)
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "bob", "password": "pw"})
    assert r.status_code == 401
    assert "session" not in r.cookies


def test_ldap_login_active_user_succeeds(db, apps_dir, monkeypatch):
    monkeypatch.setattr("harness.auth_manager.ldap_authenticate", _fake_ldap)
    c = _ldap_client(db, apps_dir)
    r = c.post("/auth/login", data={"username": "carol", "password": "pw"})
    assert r.status_code == 303
    assert "session" in r.cookies
    assert db.get_user_by_username("carol")["auth_provider"] == "ldap"
