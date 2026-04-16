import ssl
import time
import httpx
from typing import Optional
from datetime import datetime, timezone
from harness.models import TestResult, StepResult

async def run_api_test(run_id: str, app: str, environment: str,
                       base_url: str, test_def: dict,
                       ssl_ctx: Optional[ssl.SSLContext] = None) -> TestResult:
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

        async with httpx.AsyncClient(timeout=30, verify=ssl_ctx or True) as client:
            response = await client.request(method, url, headers=headers)

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

        if expect_json:
            try:
                body = response.json()
            except Exception:
                body = {}
            mismatches = {k: f"expected {v!r}, got {body.get(k)!r}"
                          for k, v in expect_json.items() if body.get(k) != v}
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
