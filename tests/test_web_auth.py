import pytest
import bcrypt
import uuid
from datetime import datetime, timezone, timedelta
from harness.db import Database
from web.main import create_app
from fastapi.testclient import TestClient

CONFIG = {
    "default_environment": "production",
    "environments": {"production": {"label": "Production"}},
    "auth": {"session_hours": 8, "secure_cookie": False},
}


def _key():
    return b"\x00" * 32


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


@pytest.fixture
def client(db, tmp_path):
    (tmp_path / "apps").mkdir()
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def _make_local_user(db, username="alice", password="Secret123!"):
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


def test_make_and_load_session_token_roundtrip():
    from web.auth import make_session_token, load_session_token
    token = make_session_token("user-123", _key(), session_hours=8)
    user_id = load_session_token(token, _key(), session_hours=8)
    assert user_id == "user-123"


def test_load_session_token_wrong_key():
    from web.auth import make_session_token, load_session_token
    token = make_session_token("user-123", _key(), session_hours=8)
    assert load_session_token(token, b"\xff" * 32, session_hours=8) is None


def test_load_session_token_tampered():
    from web.auth import load_session_token
    assert load_session_token("tampered.garbage", _key(), session_hours=8) is None


def test_load_session_token_empty():
    from web.auth import load_session_token
    assert load_session_token("", _key(), session_hours=8) is None


# ── Brute-force protection — login route ──────────────────────────────────────

def test_failed_login_increments_counter(client, db):
    """Each wrong password increments failed_login_count."""
    _make_local_user(db)
    client.post("/auth/login", data={"username": "alice", "password": "wrong1"})
    client.post("/auth/login", data={"username": "alice", "password": "wrong2"})
    row = db.get_user_by_username("alice")
    assert row["failed_login_count"] == 2


def test_tenth_failed_login_sets_locked_until(client, db):
    """The 10th failed attempt sets locked_until ~15 min in the future."""
    _make_local_user(db)
    for _ in range(10):
        client.post("/auth/login", data={"username": "alice", "password": "bad"})
    row = db.get_user_by_username("alice")
    assert row["locked_until"] is not None
    lock_dt = datetime.fromisoformat(row["locked_until"])
    assert lock_dt > datetime.now(timezone.utc)


def test_locked_user_cannot_login_with_correct_password(client, db):
    """A locked account is rejected even with the correct password."""
    uid = _make_local_user(db)
    future = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    db.increment_failed_login(uid, future)
    resp = client.post("/auth/login", data={"username": "alice", "password": "Secret123!"})
    assert resp.status_code in (200, 401)
    assert "session" not in resp.cookies


def test_successful_login_resets_counter(client, db):
    """A successful login clears failed_login_count and locked_until."""
    uid = _make_local_user(db)
    db.increment_failed_login(uid, None)
    db.increment_failed_login(uid, None)
    resp = client.post("/auth/login", data={"username": "alice", "password": "Secret123!"})
    assert resp.status_code in (302, 303)
    row = db.get_user_by_username("alice")
    assert row["failed_login_count"] == 0
    assert row["locked_until"] is None


def test_unauthenticated_html_route_redirects_with_location_header(client, db):
    """Regression: HTTPException(307, headers={"Location": ...}) raised by
    get_current_user must produce a 307 *with the Location header intact*.

    The custom exception handler in web/main.py used to call JSONResponse
    without forwarding exc.headers, which stripped Location and made browsers
    render {"detail":"Temporary Redirect"} verbatim instead of following the
    redirect to /auth/login.
    """
    _make_local_user(db)  # ensure first-run middleware doesn't intercept
    resp = client.get("/")
    assert resp.status_code == 307, (
        f"expected 307 redirect for unauthenticated browser, got {resp.status_code}: {resp.text!r}"
    )
    assert resp.headers.get("location") == "/auth/login", (
        "Location header missing — exception handler is stripping exc.headers; "
        f"got headers={dict(resp.headers)!r}"
    )


def test_unauthenticated_api_route_returns_401_not_redirect(client, db):
    """API routes should 401 with JSON, not 307 (that path doesn't set Location
    and we want to keep it that way for non-browser clients)."""
    _make_local_user(db)
    resp = client.get("/api/apps")
    assert resp.status_code == 401
    assert "location" not in {k.lower() for k in resp.headers}
