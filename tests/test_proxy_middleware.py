"""Unit tests for web.proxy_middleware (ReverseProxyMiddleware)."""
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.testclient import TestClient
from fastapi import FastAPI

from web.proxy_middleware import ReverseProxyMiddleware
from web.ratelimit import client_ip
from web.auth import issue_session_cookie, set_auth_config


def _make_test_app(trusted_proxies=None, trust_all=False, default_root_path=""):
    app = FastAPI()

    @app.get("/inspect")
    async def inspect(request: Request):
        return {
            "client_host": request.client.host if request.client else None,
            "scheme": request.url.scheme,
            "host": request.headers.get("host"),
            "root_path": request.scope.get("root_path"),
            "ratelimit_client_ip": client_ip(request),
            "proxy_state": getattr(request.state, "proxy", None),
        }

    @app.get("/login-test")
    async def login_test(request: Request):
        resp = Response("ok")
        issue_session_cookie(resp, {"id": "user-1", "session_version": 1}, request=request)
        return resp

    app.add_middleware(
        ReverseProxyMiddleware,
        trusted_proxies=trusted_proxies,
        trust_all=trust_all,
        default_root_path=default_root_path,
    )
    return app


def test_trusted_proxy_forwarded_for():
    app = _make_test_app(trusted_proxies=["127.0.0.1", "10.0.0.0/8"])
    client = TestClient(app)

    # TestClient connects as 127.0.0.1 (trusted)
    r = client.get("/inspect", headers={"X-Forwarded-For": "203.0.113.195, 10.0.0.1"})
    assert r.status_code == 200
    data = r.json()
    assert data["client_host"] == "203.0.113.195"
    assert data["ratelimit_client_ip"] == "203.0.113.195"
    assert data["proxy_state"]["is_trusted"] is True
    assert data["proxy_state"]["effective_client_ip"] == "203.0.113.195"


def test_untrusted_proxy_ignores_forwarded_for():
    # Only 192.168.1.1 is trusted; TestClient connects as 127.0.0.1
    app = _make_test_app(trusted_proxies=["192.168.1.1"])
    client = TestClient(app)

    r = client.get("/inspect", headers={"X-Forwarded-For": "203.0.113.195"})
    assert r.status_code == 200
    data = r.json()
    assert data["client_host"] != "203.0.113.195"
    assert data["proxy_state"]["is_trusted"] is False
    assert data["proxy_state"]["effective_client_ip"] != "203.0.113.195"


def test_trusted_proxy_forwarded_proto_and_auto_https_cookie():
    set_auth_config(signing_key=b"k" * 32, session_hours=8, secure_cookie=False)
    app = _make_test_app(trusted_proxies=["127.0.0.1"])
    client = TestClient(app)

    # Inspect HTTPS scheme
    r = client.get("/inspect", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    data = r.json()
    assert data["scheme"] == "https"
    assert data["proxy_state"]["effective_scheme"] == "https"

    # Mint session cookie over forwarded HTTPS -> secure flag must be True
    resp = client.get("/login-test", headers={"X-Forwarded-Proto": "https"})
    cookie_header = resp.headers.get("set-cookie", "")
    assert "Secure" in cookie_header or "secure" in cookie_header


def test_untrusted_proxy_ignores_proto():
    app = _make_test_app(trusted_proxies=["192.168.1.50"])
    client = TestClient(app)

    r = client.get("/inspect", headers={"X-Forwarded-Proto": "https"})
    assert r.status_code == 200
    data = r.json()
    assert data["scheme"] == "http"


def test_trusted_proxy_forwarded_host_and_prefix():
    app = _make_test_app(trusted_proxies=["127.0.0.1"])
    client = TestClient(app)

    r = client.get("/inspect", headers={
        "X-Forwarded-Host": "monitor.corp.example.com",
        "X-Forwarded-Prefix": "/testharness",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["host"] == "monitor.corp.example.com"
    assert data["root_path"] == "/testharness"


def test_default_root_path_configuration():
    app = _make_test_app(trusted_proxies=["127.0.0.1"], default_root_path="/qa/harness")
    client = TestClient(app)

    r = client.get("/inspect")
    assert r.status_code == 200
    data = r.json()
    assert data["root_path"] == "/qa/harness"


def test_wildcard_trusted_proxies():
    app = _make_test_app(trusted_proxies=["*"])
    client = TestClient(app)

    r = client.get("/inspect", headers={
        "X-Forwarded-For": "198.51.100.22",
        "X-Forwarded-Proto": "https",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["client_host"] == "198.51.100.22"
    assert data["scheme"] == "https"
    assert data["proxy_state"]["is_trusted"] is True


def test_cidr_network_matching():
    app = _make_test_app(trusted_proxies=["172.16.0.0/12", "10.0.0.0/8"])
    mw = app.user_middleware[0].cls(app, trusted_proxies=["172.16.0.0/12", "10.0.0.0/8"])

    assert mw.is_ip_trusted("172.18.0.4") is True
    assert mw.is_ip_trusted("10.254.1.9") is True
    assert mw.is_ip_trusted("192.168.1.1") is False
    assert mw.is_ip_trusted("8.8.8.8") is False
    assert mw.is_ip_trusted(None) is False
    assert mw.is_ip_trusted("invalid-ip") is False
