import os
import time
from datetime import datetime, timezone
from typing import Optional
from playwright.async_api import async_playwright, Page
from harness.models import TestResult, StepResult
from harness.loader import slugify_test_name

async def execute_step(page: Page, step: dict, base_url: str) -> StepResult:
    start = time.monotonic()
    def _elapsed() -> int:
        return int((time.monotonic() - start) * 1000)
    try:
        if "navigate" in step:
            target = step["navigate"]
            url = target if target.startswith("http") else base_url.rstrip("/") + target
            await page.goto(url)
            return StepResult(step=f"navigate {target}", status="pass", duration_ms=_elapsed())
        if "fill" in step:
            s = step["fill"]
            await page.fill(s["field"], s["value"])
            return StepResult(step=f"fill {s['field']}", status="pass", duration_ms=_elapsed())
        if "click" in step:
            await page.click(step["click"])
            return StepResult(step=f"click {step['click']}", status="pass", duration_ms=_elapsed())
        if "assert_url_contains" in step:
            expected = step["assert_url_contains"]
            current = page.url
            if expected not in current:
                err = f"Expected URL to contain '{expected}' but got '{current}'"
                return StepResult(step=f"assert_url_contains {expected}",
                                  status="fail", duration_ms=_elapsed(), error=err)
            return StepResult(step=f"assert_url_contains {expected}",
                              status="pass", duration_ms=_elapsed())
        if "assert_text" in step:
            expected = step["assert_text"]
            body = await page.text_content("body")
            if expected not in (body or ""):
                err = f"Expected page to contain text '{expected}'"
                return StepResult(step=f"assert_text {expected}",
                                  status="fail", duration_ms=_elapsed(), error=err)
            return StepResult(step=f"assert_text {expected}",
                              status="pass", duration_ms=_elapsed())
        return StepResult(step=str(step), status="error", duration_ms=_elapsed(),
                          error=f"Unknown step type: {list(step.keys())}")
    except Exception as e:
        return StepResult(step=str(step), status="error", duration_ms=_elapsed(), error=str(e))

async def run_browser_test(run_id: str, app: str, environment: str,
                           base_url: str, test_def: dict,
                           screenshot_dir: str = "data/screenshots",
                           headless: bool = True, timeout_ms: int = 30000) -> TestResult:
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    step_log = []
    start = time.monotonic()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        page = await browser.new_page()
        page.set_default_timeout(timeout_ms)
        steps = test_def.get("steps", [])
        failed = False
        for step in steps:
            step_result = await execute_step(page, step, base_url)
            step_log.append(step_result)
            if step_result.status in ("fail", "error"):
                failed = True
                slug = slugify_test_name(test_def["name"])
                screenshot_path = os.path.join(screenshot_dir, app, environment, run_id, f"{slug}.png")
                os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
                await page.screenshot(path=screenshot_path)
                result.screenshot = os.path.join(app, environment, run_id, f"{slug}.png")
                result.error_msg = step_result.error
                break
        await browser.close()
    result.status = "fail" if failed else "pass"
    result.step_log = step_log
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result

async def run_availability_test(run_id: str, app: str, environment: str,
                                base_url: str, test_def: dict) -> TestResult:
    import httpx
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    expect_status = test_def.get("expect_status", 200)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(base_url)
        elapsed = int((time.monotonic() - start) * 1000)
        if resp.status_code == expect_status:
            result.status = "pass"
        else:
            result.status = "fail"
            result.error_msg = f"Expected status {expect_status}, got {resp.status_code}"
        result.step_log = [StepResult(step=f"GET {base_url}", status=result.status,
                                      duration_ms=elapsed, error=result.error_msg)]
    except Exception as e:
        result.status = "error"
        result.error_msg = str(e)
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result
