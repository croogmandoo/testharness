"""Tier-3: audit_log table + helpers."""
import pytest

from harness.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "audit.db"))
    d.init_schema()
    return d


def test_log_audit_persists_entry(db):
    eid = db.log_audit(
        "user.create", user_id="u1", username="alice",
        target="bob", details="role=admin",
    )
    assert eid
    rows = db.list_audit_log()
    assert len(rows) == 1
    e = rows[0]
    assert e["action"] == "user.create"
    assert e["user_id"] == "u1"
    assert e["username"] == "alice"
    assert e["target"] == "bob"
    assert e["details"] == "role=admin"
    assert e["timestamp"]


def test_list_audit_log_orders_newest_first(db):
    db.log_audit("a", user_id="u")
    db.log_audit("b", user_id="u")
    db.log_audit("c", user_id="u")
    rows = db.list_audit_log()
    actions = [r["action"] for r in rows]
    assert actions == ["c", "b", "a"]


def test_list_audit_log_filters_by_user(db):
    db.log_audit("x", user_id="u1", username="alice")
    db.log_audit("x", user_id="u2", username="bob")
    rows = db.list_audit_log(user_id="u1")
    assert all(r["user_id"] == "u1" for r in rows)
    assert len(rows) == 1


def test_list_audit_log_filters_by_action(db):
    db.log_audit("user.create", user_id="u")
    db.log_audit("user.delete", user_id="u")
    db.log_audit("secret.upsert", user_id="u")
    rows = db.list_audit_log(action="user.delete")
    assert len(rows) == 1
    assert rows[0]["action"] == "user.delete"


def test_list_audit_log_respects_limit(db):
    for i in range(5):
        db.log_audit(f"action.{i}", user_id="u")
    rows = db.list_audit_log(limit=3)
    assert len(rows) == 3


def test_log_audit_handles_missing_user_metadata(db):
    """Auth code may pass partial user info; helper must not crash."""
    eid = db.log_audit("system.event")
    assert eid
    rows = db.list_audit_log()
    assert rows[0]["user_id"] is None
    assert rows[0]["username"] is None


def test_log_audit_swallows_db_errors_silently(tmp_path):
    """Audit failures must NEVER block the action being audited."""
    d = Database(str(tmp_path / "noschema.db"))
    # Intentionally do NOT init_schema -> table missing
    eid = d.log_audit("anything")
    assert eid  # still returns an ID even though insert failed
