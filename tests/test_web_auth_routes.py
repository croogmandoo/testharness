import pytest
from fastapi.testclient import TestClient
from harness.db import Database
from web.main import create_app

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
def client(db, tmp_path):
    (tmp_path / "apps").mkdir()
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    return TestClient(app, follow_redirects=False)

def test_setup_redirects_when_no_users(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/setup"

def test_setup_get_renders_form(client):
    resp = client.get("/setup")
    assert resp.status_code == 200

def test_setup_post_creates_admin(client, db):
    resp = client.post("/setup", data={
        "username": "admin", "password": "Secret123!", "confirm": "Secret123!", "display_name": ""
    })
    assert resp.status_code in (302, 303)
    assert db.count_users() == 1
    assert db.get_user_by_username("admin")["role"] == "admin"

def test_setup_post_password_mismatch(client):
    resp = client.post("/setup", data={
        "username": "admin", "password": "Secret123!", "confirm": "Different!", "display_name": ""
    })
    assert resp.status_code in (200, 422)

def test_setup_returns_404_when_users_exist(db, tmp_path):
    (tmp_path / "apps").mkdir()
    db.upsert_ldap_user("existing", "Existing", None, "admin")
    app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
    c = TestClient(app, follow_redirects=False)
    resp = c.get("/setup")
    assert resp.status_code == 404

def test_login_get_renders_form(client, db):
    db.upsert_ldap_user("someone", "Someone", None, "admin")
    resp = client.get("/auth/login")
    assert resp.status_code == 200

def test_login_post_success(client, db):
    import bcrypt, uuid
    from datetime import datetime, timezone
    db.insert_user({
        "id": str(uuid.uuid4()), "username": "alice", "display_name": "Alice",
        "email": None,
        "password_hash": bcrypt.hashpw(b"pass123", bcrypt.gensalt()).decode(),
        "role": "admin", "auth_provider": "local", "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    resp = client.post("/auth/login", data={"username": "alice", "password": "pass123"})
    assert resp.status_code in (302, 303)
    assert "session" in resp.cookies

def test_login_post_wrong_password(client, db):
    import bcrypt, uuid
    from datetime import datetime, timezone
    db.insert_user({
        "id": str(uuid.uuid4()), "username": "alice", "display_name": "Alice",
        "email": None,
        "password_hash": bcrypt.hashpw(b"correct", bcrypt.gensalt()).decode(),
        "role": "admin", "auth_provider": "local", "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    resp = client.post("/auth/login", data={"username": "alice", "password": "wrong"})
    assert resp.status_code in (200, 401)

def test_logout_redirects(client, db):
    db.upsert_ldap_user("bob", "Bob", None, "admin")
    resp = client.post("/auth/logout")
    assert resp.status_code in (302, 303)
