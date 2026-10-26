# tests/test_notification_groups.py
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database
from harness.alerts import dispatch_alerts, AlertType


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


def _seed_runner(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_runner",
        "display_name": "Runner",
        "email": "runner@example.com",
        "password_hash": None,
        "role": "runner",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def _seed_viewer(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_viewer",
        "display_name": "Viewer",
        "email": "viewer@example.com",
        "password_hash": None,
        "role": "viewer",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    _seed_admin(d)
    _seed_runner(d)
    _seed_viewer(d)
    return d


def _make_client(db, user, tmp_path):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    apps_dir = str(tmp_path / "apps")
    import os
    os.makedirs(apps_dir, exist_ok=True)
    app = create_app(db=db, config=config, apps_dir=apps_dir)
    from web.auth import get_current_user
    from web.csrf import verify_csrf
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app)


@pytest.fixture
def admin_client(db, tmp_path):
    return _make_client(db, db.get_user_by_username("_admin"), tmp_path)


@pytest.fixture
def runner_client(db, tmp_path):
    return _make_client(db, db.get_user_by_username("_runner"), tmp_path)


@pytest.fixture
def viewer_client(db, tmp_path):
    return _make_client(db, db.get_user_by_username("_viewer"), tmp_path)


# ── DB Layer Tests ──────────────────────────────────────────────────────────

def test_db_create_and_get_group(db):
    grp = db.create_notification_group(
        name="DevOps Team",
        description="On-call devops engineers",
        emails=[" Alice@Example.com ", "bob@example.com", "alice@example.com"]
    )
    assert grp["id"] and isinstance(grp["id"], str)
    assert grp["name"] == "DevOps Team"
    assert grp["description"] == "On-call devops engineers"
    # Whitespace trimmed, lowercased, and deduplicated
    assert grp["emails"] == ["alice@example.com", "bob@example.com"]
    assert grp["created_at"] is not None
    assert grp["updated_at"] is not None

    by_id = db.get_notification_group(grp["id"])
    assert by_id["id"] == grp["id"]
    assert by_id["name"] == "DevOps Team"

    by_name = db.get_notification_group_by_name("devops team")
    assert by_name is not None
    assert by_name["id"] == grp["id"]

    all_groups = db.list_notification_groups()
    assert len(all_groups) == 1
    assert all_groups[0]["name"] == "DevOps Team"


def test_db_validation_errors(db):
    # Empty name
    with pytest.raises(ValueError, match="Notification group name cannot be empty"):
        db.create_notification_group(name="  ", emails=["test@example.com"])

    # Invalid email format
    with pytest.raises(ValueError, match="at least one valid email address"):
        db.create_notification_group(name="Invalid", emails=["not-an-email"])

    # Duplicate name
    db.create_notification_group(name="Unique Name", emails=["a@example.com"])
    with pytest.raises(ValueError, match="already exists"):
        db.create_notification_group(name="Unique Name", emails=["b@example.com"])


def test_db_update_group(db):
    grp = db.create_notification_group(name="SRE", description="Site Reliability", emails=["sre1@example.com"])
    updated = db.update_notification_group(
        grp["id"],
        name="SRE Global",
        description="Global SRE Team",
        emails=["sre1@example.com", "sre2@example.com"]
    )
    assert updated["name"] == "SRE Global"
    assert updated["description"] == "Global SRE Team"
    assert len(updated["emails"]) == 2

    # Not found
    assert db.update_notification_group("nonexistent-uuid", name="Ghost") is None

    # Name conflict on update
    db.create_notification_group(name="Platform", emails=["p@example.com"])
    with pytest.raises(ValueError, match="already exists"):
        db.update_notification_group(grp["id"], name="Platform")


def test_db_delete_group(db):
    grp = db.create_notification_group(name="Temp Group", emails=["t@example.com"])
    assert db.delete_notification_group(grp["id"]) is True
    assert db.get_notification_group(grp["id"]) is None
    assert db.delete_notification_group(grp["id"]) is False


def test_db_resolve_emails_for_groups(db):
    g1 = db.create_notification_group(name="Infra", emails=["infra1@example.com", "shared@example.com"])
    g2 = db.create_notification_group(name="Security", emails=["sec@example.com", "shared@example.com"])

    # Resolve by name
    resolved = db.resolve_emails_for_groups(["Infra"])
    assert resolved == ["infra1@example.com", "shared@example.com"]

    # Resolve by ID
    resolved_id = db.resolve_emails_for_groups([g2["id"]])
    assert resolved_id == ["sec@example.com", "shared@example.com"]

    # Resolve multiple (merges, deduplicates, sorts)
    combined = db.resolve_emails_for_groups(["Infra", str(g2["id"])])
    assert combined == ["infra1@example.com", "sec@example.com", "shared@example.com"]

    # Empty or unknown
    assert db.resolve_emails_for_groups([]) == []
    assert db.resolve_emails_for_groups(["NonExistent"]) == []


# ── REST API Tests ──────────────────────────────────────────────────────────

def test_api_list_notification_groups(admin_client, db):
    db.create_notification_group(name="Group A", emails=["a@example.com"])
    resp = admin_client.get("/api/notification-groups")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Group A"


def test_api_create_notification_group_permissions(admin_client, runner_client, viewer_client):
    payload = {"name": "Test Group", "description": "Desc", "emails": ["user@example.com"]}

    # Viewer denied
    v_resp = viewer_client.post("/api/notification-groups", json=payload)
    assert v_resp.status_code == 403

    # Runner allowed
    r_resp = runner_client.post("/api/notification-groups", json=payload)
    assert r_resp.status_code == 201
    assert r_resp.json()["name"] == "Test Group"


def test_api_create_validation(admin_client):
    # Missing/blank name
    resp = admin_client.post("/api/notification-groups", json={"name": "", "emails": ["a@b.com"]})
    assert resp.status_code == 400

    # Invalid email
    resp = admin_client.post("/api/notification-groups", json={"name": "Good", "emails": ["bad-email"]})
    assert resp.status_code == 400

    # Duplicate name
    admin_client.post("/api/notification-groups", json={"name": "Duplicate", "emails": ["a@b.com"]})
    resp = admin_client.post("/api/notification-groups", json={"name": "Duplicate", "emails": ["c@d.com"]})
    assert resp.status_code == 409


def test_api_update_notification_group(admin_client, viewer_client, db):
    grp = db.create_notification_group(name="Original", emails=["orig@example.com"])

    # Viewer denied
    resp = viewer_client.put(f"/api/notification-groups/{grp['id']}", json={"name": "New Name"})
    assert resp.status_code == 403

    # Admin updates
    resp = admin_client.put(f"/api/notification-groups/{grp['id']}", json={"name": "Updated Name", "emails": ["new@example.com"]})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated Name"
    assert resp.json()["emails"] == ["new@example.com"]

    # 404 not found
    resp = admin_client.put("/api/notification-groups/9999", json={"name": "Ghost"})
    assert resp.status_code == 404


def test_api_delete_notification_group(admin_client, viewer_client, db):
    grp = db.create_notification_group(name="To Delete", emails=["del@example.com"])

    # Viewer denied
    resp = viewer_client.delete(f"/api/notification-groups/{grp['id']}")
    assert resp.status_code == 403

    # Admin deletes
    resp = admin_client.delete(f"/api/notification-groups/{grp['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == grp["id"]

    # 404 on repeat
    resp = admin_client.delete(f"/api/notification-groups/{grp['id']}")
    assert resp.status_code == 404


# ── Web Admin UI Tests ──────────────────────────────────────────────────────

def test_admin_notifications_ui_access(admin_client, runner_client, db):
    # Runner forbidden from admin area
    resp = runner_client.get("/admin/notifications")
    assert resp.status_code == 403

    # Admin allowed
    resp = admin_client.get("/admin/notifications")
    assert resp.status_code == 200
    assert "Notification Groups" in resp.text


def test_admin_notifications_ui_form_flows(admin_client, db):
    # Create via UI form
    resp = admin_client.post(
        "/admin/notifications",
        data={"name": "Frontend Team", "description": "UI developers", "emails": "dev1@example.com, dev2@example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/notifications"

    grp = db.get_notification_group_by_name("Frontend Team")
    assert grp is not None
    assert grp["emails"] == ["dev1@example.com", "dev2@example.com"]

    # Edit via UI form
    resp = admin_client.post(
        f"/admin/notifications/{grp['id']}/edit",
        data={"name": "Frontend Team Global", "description": "Updated", "emails": "lead@example.com"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    updated = db.get_notification_group(grp["id"])
    assert updated["name"] == "Frontend Team Global"
    assert updated["emails"] == ["lead@example.com"]

    # Delete via UI form
    resp = admin_client.post(
        f"/admin/notifications/{grp['id']}/delete",
        data={},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert db.get_notification_group(grp["id"]) is None


# ── Alert Routing Integration Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_routing_to_notification_group(db):
    # Create notification group
    db.create_notification_group(name="CoreOps", emails=["ops1@example.com", "ops2@example.com"])

    app_defs = [
        {
            "app": "payment-service",
            "url": "https://payment.example.com",
            "notification_groups": ["CoreOps"],
        }
    ]

    alerts_config = {
        "email": {
            "host": "smtp.example.com",
            "port": 587,
            "from": "alerts@example.com",
            "to": ["fallback@example.com"],
        }
    }

    alerts = [(AlertType.FAIL, "payment-service", "production", "health_check", "Connection timeout")]

    with patch("harness.alerts._send_email") as mock_send_email:
        await dispatch_alerts(alerts, alerts_config, db=db, app_defs=app_defs)
        assert mock_send_email.called
        args, kwargs = mock_send_email.call_args
        recipients = kwargs.get("recipients") if "recipients" in kwargs else (args[3] if len(args) > 3 else None)
        assert recipients == ["ops1@example.com", "ops2@example.com"]


@pytest.mark.asyncio
async def test_alert_routing_fallback_when_no_groups(db):
    app_defs = [
        {
            "app": "legacy-service",
            "url": "https://legacy.example.com",
            # No notification_groups
        }
    ]

    alerts_config = {
        "email": {
            "host": "smtp.example.com",
            "port": 587,
            "from": "alerts@example.com",
            "to": ["fallback@example.com"],
        }
    }

    alerts = [(AlertType.FAIL, "legacy-service", "production", "health_check", "Error 500")]

    with patch("harness.alerts._send_email") as mock_send_email:
        await dispatch_alerts(alerts, alerts_config, db=db, app_defs=app_defs)
        assert mock_send_email.called
        args, kwargs = mock_send_email.call_args
        assert kwargs.get("recipients") is None
        assert len(args) == 3
