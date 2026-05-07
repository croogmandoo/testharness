"""Tests for the /admin/backup endpoint and Database.backup_to_path."""
import os
import sqlite3
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import Run
from web.auth import get_current_user
from web.main import create_app


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "live.db"))
    d.init_schema()
    return d


def _seed_admin(db):
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


def _seed_runner(db):
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


# ── Database.backup_to_path ──────────────────────────────────────────────────

def test_backup_creates_a_valid_sqlite_file(db, tmp_path):
    db.insert_run(Run(app="A", environment="prod", triggered_by="ui"))
    dest = tmp_path / "snap.db"
    db.backup_to_path(str(dest))
    assert dest.exists()
    # Reading the backup should reveal the same data
    conn = sqlite3.connect(str(dest))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT app FROM runs").fetchall()
    conn.close()
    assert rows and rows[0]["app"] == "A"


def test_backup_is_a_consistent_snapshot(db, tmp_path):
    """Online backup must capture a point-in-time view, not partial writes."""
    for i in range(50):
        db.insert_run(Run(app=f"App{i}", environment="prod", triggered_by="ui"))
    dest = tmp_path / "snap.db"
    db.backup_to_path(str(dest))
    # Continue writing AFTER the backup — those writes must NOT appear
    db.insert_run(Run(app="LATE", environment="prod", triggered_by="ui"))
    conn = sqlite3.connect(str(dest))
    rows = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
    conn.close()
    assert rows[0] == 50  # not 51


# ── /admin/backup HTTP endpoint ──────────────────────────────────────────────

def test_backup_endpoint_returns_octet_stream(db, tmp_path):
    admin = _seed_admin(db)
    db.insert_run(Run(app="X", environment="prod", triggered_by="ui"))
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as c:
        r = c.get("/admin/backup")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert ".db" in r.headers["content-disposition"]
    # Body must be a real SQLite header
    assert r.content[:16].startswith(b"SQLite format 3\x00")


def test_backup_endpoint_records_audit_entry(db, tmp_path):
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as c:
        c.get("/admin/backup")
    audit = db.list_audit_log(action="db.backup")
    assert len(audit) == 1
    assert audit[0]["user_id"] == admin["id"]


def test_backup_endpoint_is_admin_only(db, tmp_path):
    runner = _seed_runner(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: runner
    with TestClient(app) as c:
        r = c.get("/admin/backup")
    assert r.status_code == 403


def test_backup_endpoint_cleans_up_temp_file(db, tmp_path):
    """Temp files in the system tempdir must not leak after the request."""
    import tempfile as _tf
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    before = set(f for f in os.listdir(_tf.gettempdir())
                 if f.startswith("harness-backup-"))
    with TestClient(app) as c:
        c.get("/admin/backup")
    # FastAPI's BackgroundTask runs after the response body is sent — by the
    # time TestClient returns, the file should be gone.
    after = set(f for f in os.listdir(_tf.gettempdir())
                if f.startswith("harness-backup-"))
    assert after - before == set()
