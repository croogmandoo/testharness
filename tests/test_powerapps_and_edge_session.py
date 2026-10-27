"""Tests for Microsoft Edge session import, storage state management, and PowerApps automation."""

import json
import os
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from harness.db import Database
from harness.edge_session import (
    parse_cookies_to_storage_state,
    save_storage_state,
    inspect_storage_state,
    delete_storage_state,
    import_edge_session,
)
from harness.browser import execute_step, run_browser_test
from harness.models import StepResult
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
        "created_at": "2026-10-27T00:00:00Z",
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


# ── Cookie & Session Parser Unit Tests ────────────────────────────────────────

def test_parse_cookie_editor_json_array():
    cookie_array = [
        {
            "name": "ESTSAUTH",
            "value": "fake_auth_token_xyz",
            "domain": ".login.microsoftonline.com",
            "path": "/",
            "expirationDate": 1893456000,  # year 2030
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        },
        {
            "name": "fpc",
            "value": "client_fpc_val",
            "domain": ".powerapps.com",
            "path": "/",
            "secure": True,
        }
    ]
    state = parse_cookies_to_storage_state(cookie_array)
    assert "cookies" in state
    assert len(state["cookies"]) == 2
    assert state["cookies"][0]["name"] == "ESTSAUTH"
    assert state["cookies"][0]["httpOnly"] is True
    assert state["cookies"][0]["sameSite"] == "None"


def test_parse_netscape_format():
    netscape_text = """# Netscape HTTP Cookie File
.login.microsoftonline.com\tTRUE\t/\tTRUE\t1893456000\tESTSAUTHPERSISTENT\tpersistent_token_123
.apps.powerapps.com\tTRUE\t/\tTRUE\t1893456000\tSignInStateCookie\tsigned_in
"""
    state = parse_cookies_to_storage_state(netscape_text)
    assert len(state["cookies"]) == 2
    assert state["cookies"][0]["name"] == "ESTSAUTHPERSISTENT"
    assert state["cookies"][1]["name"] == "SignInStateCookie"


def test_inspect_storage_state_entra_id_detection(tmp_path, monkeypatch):
    test_dir = str(tmp_path / "storage_states")
    monkeypatch.setattr("harness.edge_session.STORAGE_STATES_DIR", test_dir)

    cookies = [
        {"name": "ESTSAUTH", "value": "xyz", "domain": ".login.microsoftonline.com", "expires": 1893456000},
        {"name": "MSPOK", "value": "ok", "domain": ".powerapps.com", "expires": 1893456000},
    ]
    path = save_storage_state("powerapp", "prod", {"cookies": cookies, "origins": []})
    assert os.path.exists(path)

    info = inspect_storage_state(path)
    assert info["exists"] is True
    assert info["cookie_count"] == 2
    assert info["has_entra_id_auth"] is True
    assert "ESTSAUTH" in info["entra_id_tokens"]
    assert "MSPOK" in info["entra_id_tokens"]
    assert info["status"] == "valid"

    # Delete
    deleted = delete_storage_state("powerapp", "prod")
    assert deleted is True
    assert not os.path.exists(path)


# ── PowerApps Iframe & Control Steps Execution Tests ─────────────────────────

@pytest.mark.asyncio
async def test_execute_step_with_explicit_frame():
    page = AsyncMock()
    frame_mock = MagicMock()
    locator_mock = AsyncMock()
    frame_mock.locator.return_value = locator_mock
    page.frame_locator.return_value = frame_mock

    step = {
        "click": "[data-control-name='BtnSubmit']",
        "frame": "fullscreen-app-host",
    }
    res = await execute_step(page, step, "https://apps.powerapps.com")
    assert res.status == "pass"
    page.frame_locator.assert_called_once_with("iframe[name='fullscreen-app-host']")
    frame_mock.locator.assert_called_once_with("[data-control-name='BtnSubmit']")
    locator_mock.click.assert_called_once()


@pytest.mark.asyncio
async def test_execute_step_wait_for_powerapps():
    page = AsyncMock()
    step = {"wait_for_powerapps": {"timeout_ms": 15000}}
    res = await execute_step(page, step, "https://apps.powerapps.com")
    assert res.status == "pass"
    assert "wait_for_powerapps ready" in res.step
    page.wait_for_selector.assert_any_call(
        'iframe[name="fullscreen-app-host"], #fullscreen-app-host, div[data-control-name]',
        timeout=15000,
    )


@pytest.mark.asyncio
async def test_execute_step_automatic_powerapps_fallback_on_click():
    page = AsyncMock()
    # Direct click on page fails
    page.click.side_effect = Exception("Element not found in main document")

    frame_mock = MagicMock()
    locator_mock = AsyncMock()
    frame_mock.locator.return_value = locator_mock
    page.frame_locator.return_value = frame_mock

    step = {"click": "[data-control-name='Gallery1']"}
    res = await execute_step(page, step, "https://apps.powerapps.com")
    assert res.status == "pass"
    assert "(powerapps frame)" in res.step
    page.frame_locator.assert_called_once_with('iframe[name="fullscreen-app-host"]')
    locator_mock.click.assert_called_once()


# ── REST API Endpoints Tests ──────────────────────────────────────────────────

def test_storage_state_rest_endpoints(tmp_path, monkeypatch):
    test_dir = str(tmp_path / "storage_states")
    monkeypatch.setattr("harness.edge_session.STORAGE_STATES_DIR", test_dir)

    db = Database(str(tmp_path / "api_test.db"))
    db.init_schema()
    admin = _seed_admin(db)
    client = _make_client(db, admin, tmp_path)

    # 1. GET non-existent
    resp = client.get("/api/storage-states/myapp/production")
    assert resp.status_code == 200
    assert resp.json()["exists"] is False

    # 2. POST import cookies
    cookie_payload = [
        {"name": "ESTSAUTH", "value": "secret", "domain": ".login.microsoftonline.com"}
    ]
    resp = client.post(
        "/api/storage-states/myapp/production",
        json={"storage_state": cookie_payload},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["info"]["cookie_count"] == 1
    assert data["info"]["has_entra_id_auth"] is True

    # 3. GET now exists
    resp = client.get("/api/storage-states/myapp/production")
    assert resp.status_code == 200
    assert resp.json()["exists"] is True

    # 4. DELETE
    resp = client.delete("/api/storage-states/myapp/production")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    # 5. Confirm deleted
    resp = client.get("/api/storage-states/myapp/production")
    assert resp.json()["exists"] is False
