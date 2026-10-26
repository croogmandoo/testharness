"""Admin reverse proxy and outbound proxy settings and diagnostics route."""
import os
import time
import httpx
from typing import Optional
from fastapi import APIRouter, Depends, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from web.auth import require_role
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates
from harness.proxy import get_httpx_proxy_url, get_outbound_proxy_config

router = APIRouter()
templates = make_templates()


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
        **extra,
    }


def _generate_proxy_snippets(host: str, prefix: str = ""):
    host_clean = host or "harness.example.com"
    prefix_clean = f"/{prefix.strip('/')}" if prefix.strip("/") else ""

    caddy = f"""{host_clean} {{
    reverse_proxy localhost:9552
}}"""

    traefik = f"""labels:
  - "traefik.enable=true"
  - "traefik.http.routers.testharness.rule=Host(`{host_clean}`)"
  - "traefik.http.routers.testharness.entrypoints=websecure"
  - "traefik.http.routers.testharness.tls=true"
  - "traefik.http.services.testharness.loadbalancer.server.port=9552" """

    nginx = f"""server {{
    listen 443 ssl http2;
    server_name {host_clean};

    # ssl_certificate /etc/letsencrypt/live/{host_clean}/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/{host_clean}/privkey.pem;

    location {prefix_clean or "/"} {{
        proxy_pass http://127.0.0.1:9552/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 90s;
    }}
}}"""

    apache = f"""<VirtualHost *:443>
    ServerName {host_clean}
    SSLEngine on

    ProxyPreserveHost On
    RequestHeader set X-Forwarded-Proto "https"
    ProxyPass {prefix_clean or "/"} http://127.0.0.1:9552/
    ProxyPassReverse {prefix_clean or "/"} http://127.0.0.1:9552/
</VirtualHost>"""

    return {
        "caddy": caddy,
        "traefik": traefik,
        "nginx": nginx,
        "apache": apache,
    }


@router.get("/admin/proxy", response_class=HTMLResponse)
async def admin_proxy_view(
    request: Request,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_config
    config = get_config()
    proxy_cfg = config.get("proxy", {})
    proxy_state = getattr(request.state, "proxy", {}) or {}

    effective_host = proxy_state.get("effective_host") or request.headers.get("host", "localhost:9552")
    host_only = effective_host.split(":")[0]
    snippets = _generate_proxy_snippets(host_only, proxy_state.get("root_path", ""))

    outbound_info = get_outbound_proxy_config(config)

    env_vars = {
        "FORWARDED_ALLOW_IPS": os.environ.get("FORWARDED_ALLOW_IPS"),
        "TESTHARNESS_TRUSTED_PROXIES": os.environ.get("TESTHARNESS_TRUSTED_PROXIES"),
        "TESTHARNESS_ROOT_PATH": os.environ.get("TESTHARNESS_ROOT_PATH"),
        "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
        "NO_PROXY": os.environ.get("NO_PROXY"),
    }

    return templates.TemplateResponse(
        request, "admin_proxy.html",
        _ctx(
            request, current_user,
            proxy_state=proxy_state,
            proxy_cfg=proxy_cfg,
            outbound_info=outbound_info,
            env_vars=env_vars,
            snippets=snippets,
        ),
    )


@router.post("/admin/proxy/test")
async def admin_proxy_test_connection(
    request: Request,
    target_url: str = Form("https://httpbin.org/status/200"),
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Diagnostic tool to test outbound connectivity through the configured forward proxy."""
    from web.main import get_config
    config = get_config()
    proxy_url = get_httpx_proxy_url(config)

    client_kwargs = {"timeout": 10.0}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.get(target_url)
        duration_ms = int((time.monotonic() - start) * 1000)
        return JSONResponse({
            "ok": True,
            "status_code": resp.status_code,
            "duration_ms": duration_ms,
            "proxy_used": proxy_url or "(none, direct connection)",
            "target_url": target_url,
            "message": f"Successfully reached {target_url} in {duration_ms}ms (HTTP {resp.status_code})",
        })
    except Exception as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return JSONResponse({
            "ok": False,
            "error": str(exc),
            "duration_ms": duration_ms,
            "proxy_used": proxy_url or "(none, direct connection)",
            "target_url": target_url,
            "message": f"Connection failed after {duration_ms}ms: {exc}",
        }, status_code=502)
