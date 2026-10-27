"""Integration tests for Maintenance Windows DB, Admin UI, and REST API."""

import os
import uuid
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app
from web.auth import get_current_user
from web.csrf import verify_csrf


def _seed_admin(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": "admin@example.com",
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def _make_client(db, user, tmp_path):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    apps_dir = str(tmp_path / "apps")
    os.makedirs(apps_dir, exist_ok=True)
    app = create_app(db=db, config=config, apps_dir=apps_dir)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app)


def test_maintenance_windows_crud_db(tmp_path):
    db = Database(str(tmp_path / "maint_test.db"))
    db.init_schema()

    # 1. Create weekly window
    win1 = db.create_maintenance_window(
        name="Weekly DB Scrub",
        window_type="weekly",
        description="Weekly vacuum",
        app="postgres-app",
        environment="staging",
        days_of_week=["sun", "wed"],
        start_time="03:00",
        end_time="05:00",
        timezone_str="UTC",
    )
    assert win1["id"] is not None
    assert win1["name"] == "Weekly DB Scrub"
    assert win1["days_of_week"] == ["sun", "wed"]
    assert win1["enabled"] is True

    # 2. List windows
    windows = db.list_maintenance_windows()
    assert len(windows) >= 1
    assert any(w["name"] == "Weekly DB Scrub" for w in windows)

    # 3. Filter list by app
    app_windows = db.list_maintenance_windows(app="postgres-app")
    assert len(app_windows) >= 1
    other_windows = db.list_maintenance_windows(app="non-existent")
    assert len(other_windows) == 0

    # 4. Get by ID
    fetched = db.get_maintenance_window(win1["id"])
    assert fetched is not None
    assert fetched["name"] == "Weekly DB Scrub"

    # 5. Update window
    updated = db.update_maintenance_window(
        win1["id"],
        name="Weekly DB Scrub Renamed",
        enabled=False,
    )
    assert updated["name"] == "Weekly DB Scrub Renamed"
    assert updated["enabled"] is False

    # 6. Delete window
    deleted = db.delete_maintenance_window(win1["id"])
    assert deleted is True
    assert db.get_maintenance_window(win1["id"]) is None


def test_maintenance_windows_rest_api(tmp_path):
    db = Database(str(tmp_path / "api_test.db"))
    db.init_schema()
    admin = _seed_admin(db)
    client = _make_client(db, admin, tmp_path)

    # 1. Create a cron window via REST API
    payload = {
        "name": "Nightly Freeze",
        "window_type": "cron",
        "description": "Daily freeze",
        "cron": "0 1 * * *",
        "duration_minutes": 60,
        "app": "core-api",
        "environment": "production",
    }
    resp = client.post("/api/maintenance-windows", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    win_id = data["id"]
    assert data["name"] == "Nightly Freeze"
    assert data["window_type"] == "cron"

    # 2. List maintenance windows via REST API
    resp = client.get("/api/maintenance-windows")
    assert resp.status_code == 200
    items = resp.json()
    assert any(item["id"] == win_id for item in items)

    # 3. Get single window
    resp = client.get(f"/api/maintenance-windows/{win_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Nightly Freeze"

    # 4. Check active status endpoint
    resp = client.get("/api/maintenance-windows/active?app=core-api&environment=production")
    assert resp.status_code == 200
    assert "active" in resp.json()

    # 5. Update window
    resp = client.put(f"/api/maintenance-windows/{win_id}", json={"duration_minutes": 90})
    assert resp.status_code == 200
    assert resp.json()["duration_minutes"] == 90

    # 6. Delete window
    resp = client.delete(f"/api/maintenance-windows/{win_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # 7. Confirm deleted
    resp = client.get(f"/api/maintenance-windows/{win_id}")
    assert resp.status_code == 404


def test_maintenance_windows_admin_html_ui(tmp_path):
    db = Database(str(tmp_path / "html_test.db"))
    db.init_schema()
    admin = _seed_admin(db)
    client = _make_client(db, admin, tmp_path)

    # 1. GET /admin/maintenance
    resp = client.get("/admin/maintenance")
    assert resp.status_code == 200
    assert b"Maintenance Windows" in resp.content
    assert b"Schedule Maintenance Window" in resp.content

    # 2. POST /admin/maintenance (create via form)
    form_data = {
        "name": "Weekly Maintenance HTML",
        "description": "Via Web UI",
        "window_type": "weekly",
        "app": "",
        "environment": "",
        "cron": "",
        "duration_minutes": "120",
        "start_time": "02:00",
        "end_time": "04:00",
        "timezone_str": "UTC",
        "days_of_week": ["sun"],
        "csrf_token": "test-csrf",
    }
    resp = client.post("/admin/maintenance", data=form_data, follow_redirects=False)
    assert resp.status_code == 303

    # Verify present in list
    resp = client.get("/admin/maintenance")
    assert resp.status_code == 200
    assert b"Weekly Maintenance HTML" in resp.content

    # 3. Toggle window
    windows = db.list_maintenance_windows()
    assert len(windows) == 1
    win_id = windows[0]["id"]
    assert windows[0]["enabled"] is True

    resp = client.post(f"/admin/maintenance/{win_id}/toggle", follow_redirects=False)
    assert resp.status_code == 303
    toggled = db.get_maintenance_window(win_id)
    assert toggled["enabled"] is False

    # 4. Delete window
    resp = client.post(f"/admin/maintenance/{win_id}/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert db.get_maintenance_window(win_id) is None
