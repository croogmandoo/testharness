"""Tests for automated synthetic SSL renewal verification and transition alerting."""

import json
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import TestResult, StepResult, Run, AppState
from harness.runner import run_app
from harness.alerts import AlertType, format_alert_message
from harness.tls_verifier import (
    find_tls_tests_for_app,
    get_expiring_or_failing_tls_apps,
    check_and_verify_expiring_certificates,
)
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


def _make_client(db, user, tmp_path, apps=None):
    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    apps_dir = str(tmp_path / "apps")
    import os
    os.makedirs(apps_dir, exist_ok=True)
    app = create_app(db=db, config=config, apps_dir=apps_dir)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app)


def test_format_alert_message_resolution():
    msg = format_alert_message(
        AlertType.RESOLVE,
        "my-app",
        "production",
        "cert-check",
        "TLS/SSL Certificate renewed: valid for 89.5 days (expires 2027-01-25, issuer: Let's Encrypt)",
    )
    assert "[RESOLVED]" in msg
    assert "my-app" in msg
    assert "TLS/SSL Certificate renewed: valid for 89.5 days" in msg


@pytest.mark.asyncio
async def test_ssl_renewal_transition_detection(tmp_path):
    db = Database(str(tmp_path / "cert_test.db"))
    db.init_schema()

    config = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "alerts": {},
    }

    app_def = {
        "app": "secure-app",
        "environments": {"production": "https://secure.example.com"},
        "tests": [
            {
                "name": "TLS Check",
                "type": "tls",
                "host": "secure.example.com",
                "min_days_valid": 30,
            }
        ],
    }

    # Track dispatched alerts
    dispatched_alerts = []

    async def fake_dispatch(alerts, alerts_cfg, db=None, app_defs=None):
        dispatched_alerts.extend(alerts)

    # 1. Run 1: Expiring/failing cert (e.g. 5 days remaining < 30)
    fake_step_fail = [
        StepResult(
            step="Cert expiry: 5.0 days remaining (expires 2026-11-01, issuer: Old Corp)",
            status="fail",
            duration_ms=50,
            error="Certificate expires in 5.0 days",
        )
    ]
    fail_result = TestResult(
        run_id="run-1",
        app="secure-app",
        environment="production",
        test_name="TLS Check",
        status="fail",
        error_msg="Certificate expires in 5.0 days",
        step_log=fake_step_fail,
        finished_at="2026-10-27T10:00:00Z",
    )

    with patch("harness.runner.dispatch_alerts", side_effect=fake_dispatch):
        with patch("harness.tls_engine.run_tls_test", return_value=fail_result):
            await run_app(app_def, "production", "manual", db, config, run_id="run-1")

    # Confirmed failed alert sent
    assert len(dispatched_alerts) == 1
    assert dispatched_alerts[0][0] == AlertType.FAIL

    dispatched_alerts.clear()

    # 2. Run 2: Certificate was renewed! (e.g. 89.5 days remaining > 60)
    fake_step_pass = [
        StepResult(
            step="Cert expiry: 89.5 days remaining (expires 2027-01-25, issuer: Let's Encrypt)",
            status="pass",
            duration_ms=45,
        )
    ]
    pass_result = TestResult(
        run_id="run-2",
        app="secure-app",
        environment="production",
        test_name="TLS Check",
        status="pass",
        error_msg=None,
        step_log=fake_step_pass,
        finished_at="2026-10-27T11:00:00Z",
    )

    with patch("harness.runner.dispatch_alerts", side_effect=fake_dispatch):
        with patch("harness.tls_engine.run_tls_test", return_value=pass_result):
            await run_app(app_def, "production", "manual", db, config, run_id="run-2")

    # Confirmed RESOLVE alert sent with renewal details
    assert len(dispatched_alerts) == 1
    alert_type, app_name, env, test_name, alert_msg = dispatched_alerts[0]
    assert alert_type == AlertType.RESOLVE
    assert app_name == "secure-app"
    assert "Certificate renewed: valid for 89.5 days" in alert_msg
    assert "Let's Encrypt" in alert_msg


def test_find_tls_tests_and_expiring_apps(tmp_path):
    db = Database(str(tmp_path / "filter_test.db"))
    db.init_schema()

    app_def = {
        "app": "crm-app",
        "environments": {"production": "https://crm.example.com"},
        "tests": [
            {"name": "HTTP Ping", "type": "http"},
            {"name": "TLS Check", "type": "tls", "host": "crm.example.com"},
        ],
    }

    assert len(find_tls_tests_for_app(app_def)) == 1

    # Insert a test result where days_remaining is 15 (< 30d)
    steps = [
        {"step": "Cert expiry: 15.0 days remaining (expires 2026-11-10, issuer: Test CA)", "status": "pass", "duration_ms": 10}
    ]
    res = TestResult(
        run_id="run-old",
        app="crm-app",
        environment="production",
        test_name="TLS Check",
        status="pass",
        step_log=[StepResult(**s) for s in steps],
        finished_at="2026-10-27T12:00:00Z",
    )
    db.insert_test_result(res)

    expiring = get_expiring_or_failing_tls_apps(db, [app_def], environments=["production"], threshold_days=30.0)
    assert len(expiring) == 1
    assert expiring[0]["app"] == "crm-app"
    assert expiring[0]["days_remaining"] == 15.0


@pytest.mark.asyncio
async def test_check_and_verify_expiring_certificates(tmp_path):
    db = Database(str(tmp_path / "verify_test.db"))
    db.init_schema()

    config = {
        "default_environment": "production",
        "environments": {"production": {}},
    }

    app_def = {
        "app": "billing-service",
        "environments": {"production": "https://billing.example.com"},
        "tests": [
            {"name": "Cert", "type": "tls", "host": "billing.example.com"}
        ],
    }

    steps = [
        {"step": "Cert expiry: 10.0 days remaining (expires 2026-11-05, issuer: DigiCert)", "status": "pass", "duration_ms": 10}
    ]
    res = TestResult(
        run_id="run-expiring",
        app="billing-service",
        environment="production",
        test_name="Cert",
        status="pass",
        step_log=[StepResult(**s) for s in steps],
        finished_at="2026-10-27T08:00:00Z",
    )
    db.insert_test_result(res)

    with patch("harness.tls_verifier.verify_app_tls", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = []
        verified = await check_and_verify_expiring_certificates(db, [app_def], config, threshold_days=30.0)
        assert verified == ["billing-service:production"]
        mock_verify.assert_called_once()


def test_tls_verification_rest_endpoints(tmp_path):
    db = Database(str(tmp_path / "tls_api_test.db"))
    db.init_schema()
    admin = _seed_admin(db)
    client = _make_client(db, admin, tmp_path)

    # 1. GET /api/tls/expiring
    resp = client.get("/api/tls/expiring")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # 2. POST /api/tls/verify/nonexistent -> 404
    resp = client.post("/api/tls/verify/nonexistent")
    assert resp.status_code == 404
