import ssl
import time
import httpx
from typing import Any, Optional
from datetime import datetime, timezone
from harness.models import TestResult, StepResult


def _extract_path(obj: Any, path: str) -> Optional[Any]:
    """Walk a dot-separated JSON path like '$.user.id' or 'user.id'.

    Supports the common 95% case (object key access) without pulling in
    `jsonpath-ng`.  Returns None if any segment is missing or if the path
    traverses a non-mapping intermediate value.
    """
    if path.startswith("$."):
        path = path[2:]
    elif path.startswith("$"):
        path = path[1:]
    if not path:
        return obj
    current: Any = obj
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
            continue
        if isinstance(current, list):
            try:
                idx = int(segment)
            except ValueError:
                return None
            if not -len(current) <= idx < len(current):
                return None
            current = current[idx]
            continue
        return None
    return current


async def run_api_test(run_id: str, app: str, environment: str,
                       base_url: str, test_def: dict,
                       ssl_ctx: Optional[ssl.SSLContext] = None,
                       proxy: Optional[str] = None) -> TestResult:
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    try:
        method = test_def.get("method", "GET").upper()
        endpoint = test_def.get("endpoint", "/")
        url = base_url.rstrip("/") + endpoint
        expect_status = test_def.get("expect_status", 200)
        expect_json = test_def.get("expect_json")
        headers = test_def.get("headers", {})
        body = test_def.get("body")
        json_body = test_def.get("json")

        timeout_s = test_def.get("timeout_ms", 30000) / 1000
        client_kwargs = {"timeout": timeout_s, "verify": ssl_ctx or True}
        if proxy is not None:
            client_kwargs["proxy"] = proxy
        else:
            from harness.proxy import get_httpx_proxy_url
            p_url = get_httpx_proxy_url()
            if p_url:
                client_kwargs["proxy"] = p_url
        async with httpx.AsyncClient(**client_kwargs) as client:
            request_kwargs: dict = {"headers": headers}
            if json_body is not None:
                request_kwargs["json"] = json_body
            elif body is not None:
                request_kwargs["content"] = body
            response = await client.request(method, url, **request_kwargs)

        step = StepResult(step=f"{method} {endpoint}", status="pass",
                          duration_ms=int((time.monotonic() - start) * 1000))

        if response.status_code != expect_status:
            step.status = "fail"
            step.error = f"Expected status {expect_status}, got {response.status_code}"
            result.status = "fail"
            result.error_msg = step.error
            result.step_log = [step]
            result.duration_ms = step.duration_ms
            result.finished_at = datetime.now(timezone.utc).isoformat()
            return result

        # Parse JSON once for both expect_json and extract
        body_json: Any = None
        body_parsed = False

        def _parse_body():
            nonlocal body_json, body_parsed
            if body_parsed:
                return body_json
            try:
                body_json = response.json()
            except Exception:
                body_json = {}
            body_parsed = True
            return body_json

        if expect_json:
            parsed = _parse_body()
            mismatches = {k: f"expected {v!r}, got {parsed.get(k) if isinstance(parsed, dict) else None!r}"
                          for k, v in expect_json.items()
                          if not isinstance(parsed, dict) or parsed.get(k) != v}
            if mismatches:
                step.status = "fail"
                step.error = "JSON mismatch: " + ", ".join(
                    f"{k}: {m}" for k, m in mismatches.items())
                result.status = "fail"
                result.error_msg = step.error
                result.step_log = [step]
                result.duration_ms = step.duration_ms
                result.finished_at = datetime.now(timezone.utc).isoformat()
                return result

        # Variable extraction for chaining into subsequent tests.
        # Format: extract: { VAR_NAME: "$.json.path", ... }
        extract = test_def.get("extract") or {}
        if extract:
            parsed = _parse_body()
            missing = []
            for var_name, path in extract.items():
                value = _extract_path(parsed, path) if isinstance(path, str) else None
                if value is None:
                    missing.append(f"{var_name} (path {path!r})")
                else:
                    # All captured values stringified — they get substituted as
                    # `$VAR_NAME` text in subsequent test definitions.
                    result.captures[var_name] = str(value)
            if missing:
                step.status = "fail"
                step.error = "Extract failed: " + ", ".join(missing)
                result.status = "fail"
                result.error_msg = step.error
                result.step_log = [step]
                result.duration_ms = step.duration_ms
                result.finished_at = datetime.now(timezone.utc).isoformat()
                return result

        result.status = "pass"
        result.step_log = [step]
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()
    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)
        result.duration_ms = int((time.monotonic() - start) * 1000)
        result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
