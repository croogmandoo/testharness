"""Unit tests for harness.tls_engine (TLS certificate expiration and security headers prober)."""
import datetime
import pytest
from unittest.mock import patch, AsyncMock

from harness.tls_engine import parse_cert_date, run_tls_test


def test_parse_cert_date():
    dt = parse_cert_date("May 10 12:00:00 2028 GMT")
    assert dt.year == 2028
    assert dt.month == 5
    assert dt.day == 10
    assert dt.tzinfo is not None


@pytest.mark.asyncio
async def test_run_tls_test_valid_certificate(monkeypatch):
    # Future expiry in 100 days
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=100)
    date_str = future.strftime("%b %d %H:%M:%S %Y GMT")

    fake_cert_info = {
        "cert": {
            "notAfter": date_str,
            "notBefore": "Jan 01 00:00:00 2024 GMT",
            "subject": ((("commonName", "example.com"),),),
            "subjectAltName": [("DNS", "example.com"), ("DNS", "www.example.com")],
            "issuer": ((("organizationName", "Let's Encrypt"),),),
        },
        "tls_version": "TLSv1.3",
        "cipher": ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    }

    monkeypatch.setattr(
        "harness.tls_engine.inspect_cert_sync",
        lambda hostname, port, timeout_s: fake_cert_info,
    )

    test_def = {
        "name": "TLS cert validity check",
        "type": "tls",
        "host": "example.com",
        "min_days_valid": 30,
        "min_tls_version": "TLSv1.2",
    }

    res = await run_tls_test(
        run_id="run-1",
        app="web-app",
        environment="prod",
        base_url="https://example.com",
        test_def=test_def,
    )

    assert res.status == "pass"
    assert res.error_msg is None
    assert len(res.step_log) == 2
    assert "Cert expiry:" in res.step_log[0].step
    assert "TLSv1.3" in res.step_log[1].step


@pytest.mark.asyncio
async def test_run_tls_test_expiring_certificate_fails(monkeypatch):
    # Expiry in 5 days, but min_days_valid is 14
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=5)
    date_str = future.strftime("%b %d %H:%M:%S %Y GMT")

    fake_cert_info = {
        "cert": {
            "notAfter": date_str,
            "notBefore": "Jan 01 00:00:00 2024 GMT",
            "subject": ((("commonName", "dying.example.com"),),),
            "issuer": ((("organizationName", "DigiCert Inc"),),),
        },
        "tls_version": "TLSv1.3",
        "cipher": ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    }

    monkeypatch.setattr(
        "harness.tls_engine.inspect_cert_sync",
        lambda hostname, port, timeout_s: fake_cert_info,
    )

    test_def = {
        "name": "Expiring cert check",
        "type": "tls",
        "min_days_valid": 14,
    }

    res = await run_tls_test(
        run_id="run-1",
        app="dying-app",
        environment="prod",
        base_url="https://dying.example.com",
        test_def=test_def,
    )

    assert res.status == "fail"
    assert "expires in" in res.error_msg
    assert "min required: 14 days" in res.error_msg


@pytest.mark.asyncio
async def test_run_tls_test_min_tls_version_failure(monkeypatch):
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=100)
    fake_cert_info = {
        "cert": {"notAfter": future.strftime("%b %d %H:%M:%S %Y GMT")},
        "tls_version": "TLSv1.1",
        "cipher": ("ECDHE-RSA-AES128-SHA", "TLSv1.1", 128),
    }

    monkeypatch.setattr(
        "harness.tls_engine.inspect_cert_sync",
        lambda hostname, port, timeout_s: fake_cert_info,
    )

    test_def = {
        "name": "TLS modern version required",
        "type": "tls",
        "min_tls_version": "TLSv1.2",
    }

    res = await run_tls_test(
        run_id="run-1",
        app="legacy-app",
        environment="prod",
        base_url="https://legacy.example.com",
        test_def=test_def,
    )

    assert res.status == "fail"
    assert "Negotiated TLSv1.1 is lower than required TLSv1.2" in res.error_msg


@pytest.mark.asyncio
async def test_run_tls_test_security_headers_verification(monkeypatch):
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=100)
    fake_cert_info = {
        "cert": {"notAfter": future.strftime("%b %d %H:%M:%S %Y GMT")},
        "tls_version": "TLSv1.3",
        "cipher": ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    }
    monkeypatch.setattr(
        "harness.tls_engine.inspect_cert_sync",
        lambda hostname, port, timeout_s: fake_cert_info,
    )

    class FakeHeadersResponse:
        status_code = 200
        headers = {
            "strict-transport-security": "max-age=31536000; includeSubDomains",
            "x-content-type-options": "nosniff",
        }

    class FakeClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            return FakeHeadersResponse()

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)

    test_def = {
        "name": "Cert and headers check",
        "type": "tls",
        "security_headers": ["Strict-Transport-Security", "X-Content-Type-Options"],
    }

    res = await run_tls_test(
        run_id="run-1",
        app="secure-app",
        environment="prod",
        base_url="https://secure.example.com",
        test_def=test_def,
    )

    assert res.status == "pass"
    step_names = [s.step for s in res.step_log]
    assert any("Header Strict-Transport-Security" in s for s in step_names)
    assert any("Header X-Content-Type-Options" in s for s in step_names)


@pytest.mark.asyncio
async def test_run_tls_test_handshake_error(monkeypatch):
    def fake_connect(*args, **kwargs):
        raise ConnectionRefusedError("Connection refused by peer")

    monkeypatch.setattr("harness.tls_engine.inspect_cert_sync", fake_connect)

    test_def = {
        "name": "Failing TLS check",
        "type": "tls",
    }

    res = await run_tls_test(
        run_id="run-1",
        app="broken-app",
        environment="prod",
        base_url="https://unreachable.local",
        test_def=test_def,
    )

    assert res.status == "fail"
    assert "TLS handshake failed" in res.error_msg


@pytest.mark.asyncio
async def test_runner_executes_tls_type_test(monkeypatch, tmp_path):
    from harness.db import Database
    from harness.runner import run_app

    db = Database(str(tmp_path / "test.db"))
    db.init_schema()

    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=90)
    fake_cert_info = {
        "cert": {"notAfter": future.strftime("%b %d %H:%M:%S %Y GMT")},
        "tls_version": "TLSv1.3",
        "cipher": ("TLS_AES_256_GCM_SHA384", "TLSv1.3", 256),
    }
    monkeypatch.setattr(
        "harness.tls_engine.inspect_cert_sync",
        lambda hostname, port, timeout_s: fake_cert_info,
    )

    app_def = {
        "app": "cert-monitor-app",
        "url": "https://secure.service.local",
        "tests": [
            {
                "name": "Production SSL Expiration",
                "type": "tls",
                "min_days_valid": 30,
            }
        ],
    }

    run_id = await run_app(
        app_def=app_def,
        environment="production",
        triggered_by="manual",
        db=db,
        config={},
    )

    run = db.get_run(run_id)
    assert run["status"] == "complete"
    results = db.get_results_for_run(run_id)
    assert len(results) == 1
    assert results[0]["status"] == "pass"
    assert "Cert expiry:" in results[0]["step_log"]
