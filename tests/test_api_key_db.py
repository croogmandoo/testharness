# tests/test_api_key_db.py
import pytest
from harness.db import Database

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d

from datetime import datetime, timezone, timedelta

def test_api_keys_table_created(db):
    """init_schema creates the api_keys table without error."""
    with db._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_keys'"
        ).fetchone()
    assert row is not None

def _make_key_row(user_id: str, *, expires_at=None, is_active=1) -> dict:
    import uuid, hashlib, secrets
    plaintext = "hth_" + secrets.token_urlsafe(30)
    prefix = plaintext[4:12]
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": "CI pipeline",
        "key_prefix": prefix,
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": is_active,
        "plaintext": plaintext,  # only used in test setup, not inserted
    }

def _insert_row(db, row: dict):
    """Insert without the plaintext helper key."""
    r = {k: v for k, v in row.items() if k != "plaintext"}
    db.insert_api_key(r)

def test_insert_and_list_for_user(db):
    db.insert_user({
        "id": "u1", "username": "alice", "display_name": "Alice", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u1")
    _insert_row(db, row)
    keys = db.list_api_keys_for_user("u1")
    assert len(keys) == 1
    assert keys[0]["name"] == "CI pipeline"

def test_get_by_prefix(db):
    db.insert_user({
        "id": "u2", "username": "bob", "display_name": "Bob", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u2")
    _insert_row(db, row)
    results = db.get_api_key_by_prefix(row["key_prefix"])
    assert len(results) == 1
    assert results[0]["key_hash"] == row["key_hash"]

def test_revoke_own_key(db):
    db.insert_user({
        "id": "u3", "username": "carol", "display_name": "Carol", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u3")
    _insert_row(db, row)
    db.revoke_api_key(row["id"], user_id="u3")
    keys = db.list_api_keys_for_user("u3")
    assert keys[0]["is_active"] == 0

def test_admin_revoke_any_key(db):
    db.insert_user({
        "id": "u4", "username": "dave", "display_name": "Dave", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u4")
    _insert_row(db, row)
    # admin passes user_id=None to revoke any key
    db.revoke_api_key(row["id"], user_id=None)
    keys = db.list_api_keys_for_user("u4")
    assert keys[0]["is_active"] == 0

def test_touch_last_used(db):
    db.insert_user({
        "id": "u5", "username": "eve", "display_name": "Eve", "email": None,
        "password_hash": None, "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
    })
    row = _make_key_row("u5")
    _insert_row(db, row)
    ts = "2026-04-14T12:00:00+00:00"
    db.touch_api_key_last_used(row["id"], ts)
    keys = db.list_api_keys_for_user("u5")
    assert keys[0]["last_used_at"] == ts

def test_list_all_api_keys(db):
    for uid, uname in [("u6", "frank"), ("u7", "grace")]:
        db.insert_user({
            "id": uid, "username": uname, "display_name": uname.title(), "email": None,
            "password_hash": None, "role": "runner", "auth_provider": "local",
            "role_override": 0, "is_active": 1,
            "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
        })
        _insert_row(db, _make_key_row(uid))
    all_keys = db.list_all_api_keys()
    user_ids = {k["user_id"] for k in all_keys}
    assert "u6" in user_ids and "u7" in user_ids
