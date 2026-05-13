"""Tests for the /admin/runners management UI + drain/resume/delete actions."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.auth import get_current_user
from web.csrf import verify_csrf
from web.main import create_app


def _seed_admin(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None, "password_hash": None,
        "role": "admin", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def _seed_runner_user(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_runner",
        "display_name": "Runner",
        "email": None, "password_hash": None,
        "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "admin_runners.db"))
    d.init_schema()
    return d


def _make_client(db, user, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir(exist_ok=True)
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def test_admin_runners_empty_state(db, tmp_path):
    admin = _seed_admin(db)
    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    assert b"No workers have registered yet" in r.content


def test_admin_runners_lists_registered(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="eu-1", tags=["eu"],
                      capabilities=["api", "browser"], version="1.0")
    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    assert b"eu-1" in r.content
    assert b"eu" in r.content


def test_admin_runners_drain_sets_status(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/drain")
    assert r.status_code == 303
    assert db.get_runner_by_name("w")["status"] == "draining"


def test_admin_runners_resume_sets_online(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    db.set_runner_status("w", "draining")
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/resume")
    assert r.status_code == 303
    assert db.get_runner_by_name("w")["status"] == "online"


def test_admin_runners_delete_removes_row(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/delete")
    assert r.status_code == 303
    assert db.get_runner_by_name("w") is None


def test_admin_runners_action_logs_audit(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    c.post("/admin/runners/w/drain")
    entries = db.list_audit_log(action="runner.drain")
    assert len(entries) == 1
    assert entries[0]["target"] == "w"


def test_admin_runners_404_on_missing_target(db, tmp_path):
    admin = _seed_admin(db)
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/ghost/drain")
    assert r.status_code == 404


def test_admin_runners_requires_admin_role(db, tmp_path):
    runner = _seed_runner_user(db)
    c = _make_client(db, runner, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 403
