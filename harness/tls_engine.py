"""TLS / SSL Certificate inspection and security headers verification engine."""
from __future__ import annotations

import asyncio
import datetime
import email.utils
import os
import socket
import ssl
import time
import urllib.parse
from typing import Optional, Dict, Any, List

import httpx

from harness.models import TestResult, StepResult
from harness.proxy import get_httpx_proxy_url
from harness.ssl_context import BUNDLE_PATH


def parse_cert_date(date_str: str) -> datetime.datetime:
    """Parse certificate date string like 'May 10 12:00:00 2027 GMT'."""
    parsed = email.utils.parsedate_to_datetime(date_str)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _extract_cn(subject: tuple) -> str:
    """Extract Common Name (CN) from cert subject tuple."""
    for rdn in subject:
        for key, value in rdn:
            if key == "commonName":
                return value
    return ""


def _extract_issuer_org(issuer: tuple) -> str:
    """Extract organizationName or commonName from cert issuer tuple."""
    org = ""
    cn = ""
    for rdn in issuer:
        for key, value in rdn:
            if key == "organizationName":
                org = value
            elif key == "commonName":
                cn = value
    return org or cn


def inspect_cert_sync(
    hostname: str,
    port: int = 443,
    timeout_s: float = 10.0,
    ca_bundle: Optional[str] = None,
) -> Dict[str, Any]:
    """Synchronously connect and inspect the TLS peer certificate."""
    ctx = ssl.create_default_context()
    if ca_bundle and os.path.exists(ca_bundle):
        ctx.load_verify_locations(ca_bundle)
    elif os.path.exists(BUNDLE_PATH):
        ctx.load_verify_locations(BUNDLE_PATH)

    with socket.create_connection((hostname, port), timeout=timeout_s) as sock:
        with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
            cert = ssock.getpeercert()
            tls_version = ssock.version()
            cipher = ssock.cipher()
            return {
                "cert": cert,
                "tls_version": tls_version,
                "cipher": cipher,
            }


async def run_tls_test(
    run_id: str,
    app: str,
    environment: str,
    base_url: str,
    test_def: dict,
    ssl_ctx: Optional[ssl.SSLContext] = None,
    proxy: Optional[str] = None,
) -> TestResult:
    """Execute a TLS certificate and security header test."""
    result = TestResult(
        run_id=run_id,
        app=app,
        environment=environment,
        test_name=test_def["name"],
    )
    start = time.monotonic()
    step_log: List[StepResult] = []
    failed = False
    error_msg: Optional[str] = None

    # Parse target hostname and port
    target_str = test_def.get("host") or test_def.get("endpoint") or base_url
    if "://" not in target_str:
        target_str = f"https://{target_str}"
    parsed_url = urllib.parse.urlsplit(target_str)
    hostname = parsed_url.hostname or "localhost"
    port = parsed_url.port or 443
    timeout_s = test_def.get("timeout_ms", 15000) / 1000.0

    # ── Step 1: Certificate Inspection ───────────────────────────────────────
    step_start = time.monotonic()
    cert_info: Optional[Dict[str, Any]] = None
    try:
        cert_info = await asyncio.to_thread(
            inspect_cert_sync,
            hostname=hostname,
            port=port,
            timeout_s=timeout_s,
        )
    except Exception as exc:
        failed = True
        error_msg = f"TLS handshake failed for {hostname}:{port}: {exc}"
        step_log.append(StepResult(
            step=f"TLS connect {hostname}:{port}",
            status="error",
            duration_ms=int((time.monotonic() - step_start) * 1000),
            error=str(exc),
        ))

    if cert_info and not failed:
        cert = cert_info["cert"]
        tls_ver = cert_info["tls_version"]
        not_after = cert.get("notAfter", "")
        issuer = _extract_issuer_org(cert.get("issuer", ()))
        cn = _extract_cn(cert.get("subject", ()))
        sans = [val for typ, val in cert.get("subjectAltName", []) if typ == "DNS"]

        if not_after:
            expiry_dt = parse_cert_date(not_after)
            now = datetime.datetime.now(datetime.timezone.utc)
            days_remaining = (expiry_dt - now).total_seconds() / 86400.0
            min_days = float(test_def.get("min_days_valid", 14))

            step_desc = (
                f"Cert expiry: {days_remaining:.1f} days remaining "
                f"(expires {expiry_dt.strftime('%Y-%m-%d')}, issuer: {issuer or 'unknown'})"
            )

            if days_remaining < min_days:
                failed = True
                error_msg = f"Certificate expires in {days_remaining:.1f} days (min required: {min_days:.0f} days)"
                step_log.append(StepResult(
                    step=step_desc,
                    status="fail",
                    duration_ms=int((time.monotonic() - step_start) * 1000),
                    error=error_msg,
                ))
            else:
                step_log.append(StepResult(
                    step=step_desc,
                    status="pass",
                    duration_ms=int((time.monotonic() - step_start) * 1000),
                ))

        # Check required TLS version if configured
        req_tls = test_def.get("min_tls_version")
        if req_tls:
            tls_order = {"TLSv1": 1, "TLSv1.1": 2, "TLSv1.2": 3, "TLSv1.3": 4}
            current_order = tls_order.get(tls_ver, 0)
            req_order = tls_order.get(req_tls, 0)
            if current_order < req_order:
                failed = True
                err = f"Negotiated {tls_ver} is lower than required {req_tls}"
                error_msg = error_msg or err
                step_log.append(StepResult(
                    step=f"TLS protocol version: {tls_ver}",
                    status="fail",
                    duration_ms=0,
                    error=err,
                ))
            else:
                step_log.append(StepResult(
                    step=f"TLS protocol version: {tls_ver} ({cert_info.get('cipher', [None])[0]})",
                    status="pass",
                    duration_ms=0,
                ))

    # ── Step 2: Optional Security Headers Check ──────────────────────────────
    check_headers = test_def.get("security_headers")
    if check_headers and not failed:
        header_start = time.monotonic()
        proxy_url = proxy or get_httpx_proxy_url()
        client_kwargs = {"timeout": timeout_s, "verify": ssl_ctx or True}
        if proxy_url:
            client_kwargs["proxy"] = proxy_url

        try:
            full_url = f"https://{hostname}:{port}" if port != 443 else f"https://{hostname}"
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(full_url, follow_redirects=True)

            required_headers = (
                check_headers if isinstance(check_headers, list)
                else ["Strict-Transport-Security", "X-Content-Type-Options"]
            )
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            for h in required_headers:
                val = resp_headers.get(h.lower())
                if not val:
                    failed = True
                    err = f"Missing security header: {h}"
                    error_msg = error_msg or err
                    step_log.append(StepResult(
                        step=f"Header {h}",
                        status="fail",
                        duration_ms=int((time.monotonic() - header_start) * 1000),
                        error=err,
                    ))
                else:
                    step_log.append(StepResult(
                        step=f"Header {h}: {val[:60]}",
                        status="pass",
                        duration_ms=int((time.monotonic() - header_start) * 1000),
                    ))
        except Exception as exc:
            step_log.append(StepResult(
                step="Security headers HTTP check",
                status="fail",
                duration_ms=int((time.monotonic() - header_start) * 1000),
                error=str(exc),
            ))
            failed = True
            error_msg = error_msg or str(exc)

    result.status = "fail" if failed else "pass"
    result.error_msg = error_msg
    result.step_log = step_log
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return result
