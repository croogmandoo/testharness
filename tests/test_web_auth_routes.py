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
    from web.csrf import verify_csrf
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)

def test_setup_redirects_when_no_users(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/setup"

def test_setup_get_renders_form(client):
    resp = client.get("/setup")
    assert resp.status_code == 200
    assert 'name="setup_token"' in resp.text

def test_setup_post_creates_admin(client, db):
    from web.routes.auth import get_setup_token
    resp = client.post("/setup", data={
        "username": "admin", "password": "Secret123!", "confirm": "Secret123!", "display_name": "",
        "setup_token": get_setup_token(),
    })
    assert resp.status_code in (302, 303)
    assert db.count_users() == 1
    assert db.get_user_by_username("admin")["role"] == "admin"

def test_setup_post_password_mismatch(client, db):
    from web.routes.auth import get_setup_token
    resp = client.post("/setup", data={
        "username": "admin", "password": "Secret123!", "confirm": "Different!", "display_name": "",
        "setup_token": get_setup_token(),
    })
    assert resp.status_code in (200, 422)
    assert db.count_users() == 0

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


def test_github_oauth_login_redirects_to_github(db, tmp_path):
    """GET /auth/oauth/github/login redirects to GitHub with client_id."""
    from fastapi.testclient import TestClient
    from web.main import create_app

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {
        "auth": {
            "github": {
                "client_id": "test_client_id",
                "client_secret": "test_secret",
            }
        },
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/oauth/github/login")
    assert r.status_code in (302, 307)
    location = r.headers["location"]
    assert "github.com/login/oauth/authorize" in location
    assert "test_client_id" in location


def test_github_oauth_login_returns_404_when_not_configured(db, tmp_path):
    from fastapi.testclient import TestClient
    from web.main import create_app
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)
    r = c.get("/auth/oauth/github/login")
    assert r.status_code == 404


def test_github_oauth_callback_creates_user_and_sets_session(db, tmp_path):
    """Callback with valid code creates the user and redirects to /."""
    import uuid
    from unittest.mock import patch, AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from web.main import create_app

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {
        "auth": {
            "github": {
                "client_id": "cid",
                "client_secret": "csecret",
                "default_role": "read_only",
                "allowed_users": ["testuser"],
            }
        },
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json = MagicMock(return_value={"access_token": "gha_test_token"})
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json = MagicMock(return_value={
        "id": 12345,
        "login": "testuser",
        "name": "Test User",
        "email": "test@example.com",
    })

    c.cookies.set("oauth_state", "teststate")

    with patch("web.routes.auth.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=user_response)
        mock_httpx.AsyncClient.return_value = mock_client

        r = c.get("/auth/oauth/github/callback?code=abc123&state=teststate")

    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/"

    user = db.get_user_by_oauth_provider_id("github", "12345")
    assert user is not None
    assert user["username"] == "github:testuser"
    assert user["role"] == "read_only"
    assert user["auth_provider"] == "github"


def test_github_oauth_callback_rejects_inactive_user_by_oauth_id(db, tmp_path):
    """Callback rejects existing inactive user looked up by oauth_provider_id with 403."""
    import uuid
    from datetime import datetime, timezone
    from unittest.mock import patch, AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from web.main import create_app

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {
        "auth": {
            "github": {
                "client_id": "cid",
                "client_secret": "csecret",
                "default_role": "read_only",
            }
        },
        "environments": {"production": {"label": "Production"}},
    }

    # Pre-create an inactive user linked to github oauth_provider_id
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": "github:inactiveuser",
        "display_name": "Inactive User",
        "email": "inactive@example.com",
        "password_hash": None,
        "role": "read_only",
        "auth_provider": "github",
        "oauth_provider_id": "12345",
        "role_override": 0,
        "is_active": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })

    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json = MagicMock(return_value={"access_token": "gha_test_token"})
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json = MagicMock(return_value={
        "id": 12345,
        "login": "inactiveuser",
        "name": "Inactive User",
        "email": "inactive@example.com",
    })

    c.cookies.set("oauth_state", "teststate")

    with patch("web.routes.auth.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=user_response)
        mock_httpx.AsyncClient.return_value = mock_client

        r = c.get("/auth/oauth/github/callback?code=abc123&state=teststate")

    assert r.status_code == 403
    assert "session" not in r.cookies


def test_github_oauth_callback_rejects_inactive_user_by_username(db, tmp_path):
    """Callback rejects existing inactive user looked up by username with 403."""
    import uuid
    from datetime import datetime, timezone
    from unittest.mock import patch, AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from web.main import create_app

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {
        "auth": {
            "github": {
                "client_id": "cid",
                "client_secret": "csecret",
                "default_role": "read_only",
            }
        },
        "environments": {"production": {"label": "Production"}},
    }

    # Pre-create an inactive user with github username but no oauth_provider_id
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": "github:existinguser",
        "display_name": "Existing User",
        "email": "existing@example.com",
        "password_hash": None,
        "role": "read_only",
        "auth_provider": "github",
        "oauth_provider_id": None,
        "role_override": 0,
        "is_active": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })

    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json = MagicMock(return_value={"access_token": "gha_test_token"})
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json = MagicMock(return_value={
        "id": 99999,
        "login": "existinguser",
        "name": "Existing User",
        "email": "existing@example.com",
    })

    c.cookies.set("oauth_state", "teststate")

    with patch("web.routes.auth.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=user_response)
        mock_httpx.AsyncClient.return_value = mock_client

        r = c.get("/auth/oauth/github/callback?code=abc123&state=teststate")

    assert r.status_code == 403
    assert "session" not in r.cookies


def test_github_oauth_callback_allows_active_user(db, tmp_path):
    """Callback allows existing active user to log in normally."""
    import uuid
    from datetime import datetime, timezone
    from unittest.mock import patch, AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from web.main import create_app

    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    config = {
        "auth": {
            "github": {
                "client_id": "cid",
                "client_secret": "csecret",
                "default_role": "read_only",
            }
        },
        "environments": {"production": {"label": "Production"}},
    }

    # Pre-create an active user linked to github oauth_provider_id
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": "github:activeuser",
        "display_name": "Active User",
        "email": "active@example.com",
        "password_hash": None,
        "role": "read_only",
        "auth_provider": "github",
        "oauth_provider_id": "54321",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })

    app = create_app(db=db, config=config, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json = MagicMock(return_value={"access_token": "gha_test_token"})
    user_response = MagicMock()
    user_response.status_code = 200
    user_response.json = MagicMock(return_value={
        "id": 54321,
        "login": "activeuser",
        "name": "Active User",
        "email": "active@example.com",
    })

    c.cookies.set("oauth_state", "teststate")

    with patch("web.routes.auth.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=token_response)
        mock_client.get = AsyncMock(return_value=user_response)
        mock_httpx.AsyncClient.return_value = mock_client

        r = c.get("/auth/oauth/github/callback?code=abc123&state=teststate")

    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/"
    assert "session" in r.cookies
