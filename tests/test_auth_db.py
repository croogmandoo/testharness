import pytest
from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def _user(overrides=None):
    base = {
        "id": "u1",
        "username": "alice",
        "display_name": "Alice",
        "email": "alice@example.com",
        "password_hash": "$2b$12$fakehash",
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": "2026-01-01T00:00:00",
        "last_login_at": None,
    }
    if overrides:
        base.update(overrides)
    return base


def test_users_table_exists(db):
    with db._connect() as conn:
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert "users" in tables


def test_insert_and_get_user_by_username(db):
    db.insert_user(_user())
    row = db.get_user_by_username("alice")
    assert row is not None
    assert row["username"] == "alice"
    assert row["role"] == "admin"


def test_get_user_by_id(db):
    db.insert_user(_user())
    row = db.get_user_by_id("u1")
    assert row is not None
    assert row["id"] == "u1"


def test_get_user_missing_returns_none(db):
    assert db.get_user_by_username("nobody") is None
    assert db.get_user_by_id("nope") is None


def test_count_users(db):
    assert db.count_users() == 0
    db.insert_user(_user())
    assert db.count_users() == 1


def test_list_users(db):
    db.insert_user(_user({"id": "u1", "username": "alice"}))
    db.insert_user(_user({"id": "u2", "username": "bob"}))
    users = db.list_users()
    assert len(users) == 2
    usernames = {u["username"] for u in users}
    assert usernames == {"alice", "bob"}


def test_list_users_excludes_password_hash(db):
    db.insert_user(_user())
    users = db.list_users()
    assert "password_hash" not in users[0]


def test_update_user(db):
    db.insert_user(_user())
    db.update_user("u1", role="runner", is_active=0)
    row = db.get_user_by_id("u1")
    assert row["role"] == "runner"
    assert row["is_active"] == 0


def test_update_user_last_login(db):
    db.insert_user(_user())
    db.update_user_last_login("u1", "2026-04-13T10:00:00")
    row = db.get_user_by_id("u1")
    assert row["last_login_at"] == "2026-04-13T10:00:00"


def test_upsert_ldap_user_creates(db):
    user = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
    assert user["username"] == "bob"
    assert user["auth_provider"] == "ldap"
    assert user["role"] == "runner"


def test_upsert_ldap_user_updates_role(db):
    db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
    updated = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "reporting")
    assert updated["role"] == "reporting"


def test_upsert_ldap_user_respects_role_override(db):
    db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
    # Set role_override so LDAP sync won't change the role
    db.update_user(db.get_user_by_username("bob")["id"], role="admin", role_override=1)
    result = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
    # Role should still be admin because role_override=1
    assert result["role"] == "admin"


def test_upsert_ldap_user_excludes_password_hash(db):
    user = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
    assert "password_hash" not in user
