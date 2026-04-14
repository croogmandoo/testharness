import pytest
from unittest.mock import patch, MagicMock
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def _make_local_user(db, username="alice", password="secret123"):
    import bcrypt
    from datetime import datetime, timezone
    import uuid
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.insert_user({
        "id": str(uuid.uuid4()),
        "username": username,
        "display_name": "Alice",
        "email": None,
        "password_hash": pw_hash,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })


def test_verify_local_password_success(db):
    from harness.auth_manager import verify_local_password
    _make_local_user(db)
    user = verify_local_password("alice", "secret123", db)
    assert user is not None
    assert user["username"] == "alice"


def test_verify_local_password_wrong_password(db):
    from harness.auth_manager import verify_local_password
    _make_local_user(db)
    assert verify_local_password("alice", "wrongpass", db) is None


def test_verify_local_password_no_user(db):
    from harness.auth_manager import verify_local_password
    assert verify_local_password("nobody", "pass", db) is None


def test_verify_local_password_inactive_user(db):
    from harness.auth_manager import verify_local_password
    _make_local_user(db)
    user = db.get_user_by_username("alice")
    db.update_user(user["id"], is_active=0)
    assert verify_local_password("alice", "secret123", db) is None


def test_verify_local_password_ldap_user_returns_none(db):
    from harness.auth_manager import verify_local_password
    db.upsert_ldap_user("ldapuser", "LDAP User", None, "runner")
    assert verify_local_password("ldapuser", "anypass", db) is None


def test_ldap_authenticate_success():
    from harness.auth_manager import ldap_authenticate
    ldap_cfg = {
        "enabled": True,
        "server": "ldap://dc.corp.com",
        "port": 389,
        "use_tls": False,
        "base_dn": "DC=corp,DC=com",
        "user_search_filter": "(sAMAccountName={username})",
        "group_search_base": "OU=Groups,DC=corp,DC=com",
        "group_attribute": "memberOf",
        "role_map": {"CN=Admins,OU=Groups,DC=corp,DC=com": "admin"},
        "default_role": "read_only",
    }
    mock_conn = MagicMock()
    mock_conn.bind.return_value = True
    mock_conn.search.return_value = True
    mock_conn.entries = [MagicMock(**{
        "displayName.value": "Bob Smith",
        "mail.value": "bob@corp.com",
        "memberOf.values": ["CN=Admins,OU=Groups,DC=corp,DC=com"],
    })]
    with patch("harness.auth_manager.Connection", return_value=mock_conn), \
         patch("harness.auth_manager.Server"):
        result = ldap_authenticate("bob", "pass", ldap_cfg)
    assert result is not None
    assert result["username"] == "bob"
    assert result["role"] == "admin"
    assert result["display_name"] == "Bob Smith"


def test_ldap_authenticate_bind_failure():
    from harness.auth_manager import ldap_authenticate
    ldap_cfg = {
        "enabled": True, "server": "ldap://dc.corp.com", "port": 389,
        "use_tls": False, "base_dn": "DC=corp,DC=com",
        "user_search_filter": "(sAMAccountName={username})",
        "group_search_base": "OU=Groups,DC=corp,DC=com",
        "group_attribute": "memberOf", "role_map": {}, "default_role": "read_only",
    }
    mock_conn = MagicMock()
    mock_conn.bind.return_value = False
    with patch("harness.auth_manager.Connection", return_value=mock_conn), \
         patch("harness.auth_manager.Server"):
        result = ldap_authenticate("bob", "badpass", ldap_cfg)
    assert result is None


def test_ldap_authenticate_default_role():
    from harness.auth_manager import ldap_authenticate
    ldap_cfg = {
        "enabled": True, "server": "ldap://dc.corp.com", "port": 389,
        "use_tls": False, "base_dn": "DC=corp,DC=com",
        "user_search_filter": "(sAMAccountName={username})",
        "group_search_base": "OU=Groups,DC=corp,DC=com",
        "group_attribute": "memberOf",
        "role_map": {"CN=Admins,OU=Groups,DC=corp,DC=com": "admin"},
        "default_role": "read_only",
    }
    mock_conn = MagicMock()
    mock_conn.bind.return_value = True
    mock_conn.search.return_value = True
    mock_conn.entries = [MagicMock(**{
        "displayName.value": "Carol",
        "mail.value": "carol@corp.com",
        "memberOf.values": [],
    })]
    with patch("harness.auth_manager.Connection", return_value=mock_conn), \
         patch("harness.auth_manager.Server"):
        result = ldap_authenticate("carol", "pass", ldap_cfg)
    assert result["role"] == "read_only"
