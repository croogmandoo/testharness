"""Integration tests for API key auth flow."""
import hashlib, secrets, uuid
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient
from harness.db import Database
from web.main import create_app


@pytest.fixture
def client(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.init_schema()
    # Create a test user
    db.insert_user({
        "id": "user1", "username": "tester", "display_name": "Tester",
        "email": None, "password_hash": None, "role": "runner",
        "auth_provider": "local", "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    app = create_app(db=db, config={})
    return TestClient(app, raise_server_exceptions=True)


def _make_and_store_key(*, expires_at=None, is_active=1):
    """Helper: insert a key row directly into DB and return the plaintext key."""
    from web.main import get_db
    db = get_db()
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    row = {
        "id": str(uuid.uuid4()),
        "user_id": "user1",
        "name": "test key",
        "key_prefix": prefix,
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": is_active,
    }
    db.insert_api_key(row)
    return plaintext, row["id"]


def test_bearer_auth_succeeds(client):
    plaintext, _ = _make_and_store_key()
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200


def test_x_api_key_header_auth_succeeds(client):
    plaintext, _ = _make_and_store_key()
    resp = client.get("/api/apps",
                      headers={"X-API-Key": plaintext})
    assert resp.status_code == 200


def test_invalid_key_returns_401(client):
    resp = client.get("/api/apps",
                      headers={"Authorization": "Bearer hth_invalid00000000000"})
    assert resp.status_code == 401


def test_revoked_key_returns_401(client):
    plaintext, key_id = _make_and_store_key(is_active=0)
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_expired_key_returns_401(client):
    past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    plaintext, _ = _make_and_store_key(expires_at=past)
    resp = client.get("/api/apps",
                      headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 401


def test_session_cookie_still_works(client):
    """Existing cookie auth must not be broken."""
    from web.auth import make_session_token
    from web.auth import _signing_key, _session_hours
    token = make_session_token("user1", _signing_key, _session_hours)
    resp = client.get("/api/apps", cookies={"session": token})
    assert resp.status_code == 200


def test_x_api_key_without_hth_prefix_ignored(client):
    """X-API-Key header without hth_ prefix is ignored (falls through to cookie check)."""
    resp = client.get("/api/apps",
                      headers={"X-API-Key": "mytoken_without_prefix"})
    assert resp.status_code == 401


# ── Route tests ──────────────────────────────────────────────────────────

def _session_cookie(user_id="user1"):
    from web.auth import make_session_token, _signing_key, _session_hours
    return {"session": make_session_token(user_id, _signing_key, _session_hours)}


def test_get_api_keys_page_requires_auth(client):
    resp = client.get("/api-keys", follow_redirects=False)
    assert resp.status_code in (307, 401)


def test_get_api_keys_page_authenticated(client):
    resp = client.get("/api-keys", cookies=_session_cookie(), follow_redirects=False)
    assert resp.status_code == 200
    assert b"API Keys" in resp.content


def test_create_key_returns_redirect_with_flash(client):
    resp = client.post(
        "/api-keys",
        data={"name": "My CI", "expiry_days": "30"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    # Should redirect back to /api-keys
    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/api-keys") or "api-keys" in resp.headers["location"]


def test_created_key_appears_in_list(client):
    client.post(
        "/api-keys",
        data={"name": "Deploy key", "expiry_days": "7"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    resp = client.get("/api-keys", cookies=_session_cookie())
    assert b"Deploy key" in resp.content


def test_revoke_own_key(client):
    client.post(
        "/api-keys",
        data={"name": "To revoke", "expiry_days": "never"},
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    from web.main import get_db
    keys = get_db().list_api_keys_for_user("user1")
    key_id = keys[0]["id"]
    resp = client.post(
        f"/api-keys/{key_id}/revoke",
        cookies=_session_cookie(),
        follow_redirects=False,
    )
    assert resp.status_code == 303
    keys_after = get_db().list_api_keys_for_user("user1")
    assert keys_after[0]["is_active"] == 0


def test_nav_link_visible_for_authenticated_user(client):
    resp = client.get("/api-keys", cookies=_session_cookie())
    assert b'href="/api-keys"' in resp.content
