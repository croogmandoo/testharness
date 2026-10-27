from __future__ import annotations

import inspect
import os
import ssl
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

# Playwright is a heavy (~1.3 GB with browsers) optional dependency: the slim
# worker image (worker/Dockerfile.slim) deliberately omits it. ``Page`` is only
# used as a type annotation here, and ``async_playwright`` is exposed as a
# lazy module attribute (PEP 562 ``__getattr__``) so that
# ``from harness.browser import run_availability_test`` succeeds on the slim
# image -- where the worker is configured for ``api,availability`` capabilities
# and never calls the browser-test path. Tests can still monkey-patch
# ``browser_mod.async_playwright`` because attribute-setting fills the module
# dict and shadows the ``__getattr__`` fallback (which is exactly what
# ``monkeypatch.setattr`` does).
if TYPE_CHECKING:
    from playwright.async_api import Page

from harness.models import TestResult, StepResult
from harness.url_recorder import URLRecorder

from harness.loader import slugify_test_name
from harness.ssl_context import BUNDLE_PATH, chromium_spki_args


def __getattr__(name: str):  # PEP 562
    if name == "async_playwright":
        from playwright.async_api import async_playwright as _ap
        globals()["async_playwright"] = _ap  # cache for subsequent lookups
        return _ap
    raise AttributeError(f"module 'harness.browser' has no attribute {name!r}")

async def extract_web_vitals(page: "Page") -> dict:
    """Extract Navigation Timing and Core Web Vitals metrics from the active page."""
    script = """() => {
        try {
            const nav = (performance.getEntriesByType('navigation') || [])[0] || {};
            const paint = performance.getEntriesByType('paint') || [];
            const fcpEntry = paint.find(e => e.name === 'first-contentful-paint');
            const lcpEntries = performance.getEntriesByType('largest-contentful-paint') || [];
            const layoutShiftEntries = performance.getEntriesByType('layout-shift') || [];

            let cls = 0.0;
            for (const entry of layoutShiftEntries) {
                if (!entry.hadRecentInput) {
                    cls += (entry.value || 0);
                }
            }

            const ttfb = nav.responseStart ? Math.round(nav.responseStart) : null;
            const domContentLoaded = nav.domContentLoadedEventEnd ? Math.round(nav.domContentLoadedEventEnd) : null;
            const loadTime = nav.loadEventEnd ? Math.round(nav.loadEventEnd) : null;
            const fcp = fcpEntry ? Math.round(fcpEntry.startTime) : null;
            const lcp = lcpEntries.length > 0 ? Math.round(lcpEntries[lcpEntries.length - 1].startTime) : null;

            return {
                ttfb: ttfb,
                dom_content_loaded: domContentLoaded,
                load_time: loadTime,
                fcp: fcp,
                lcp: lcp,
                cls: Number(cls.toFixed(3)),
            };
        } catch (e) {
            return { error: String(e) };
        }
    }"""
    try:
        metrics = await page.evaluate(script)
        if isinstance(metrics, dict) and "error" not in metrics:
            return metrics
    except Exception:
        pass
    return {}


def evaluate_performance_thresholds(metrics: dict, thresholds: dict) -> tuple[str, Optional[str]]:
    """Check metrics against performance thresholds.

    Supported thresholds: max_lcp_ms, max_fcp_ms, max_ttfb_ms, max_cls, max_load_ms.
    Returns (summary_step_description, error_message_or_None).
    """
    parts = []
    if metrics.get("ttfb") is not None:
        parts.append(f"TTFB={metrics['ttfb']}ms")
    if metrics.get("fcp") is not None:
        parts.append(f"FCP={metrics['fcp']}ms")
    if metrics.get("lcp") is not None:
        parts.append(f"LCP={metrics['lcp']}ms")
    if metrics.get("cls") is not None:
        parts.append(f"CLS={metrics['cls']}")
    if metrics.get("load_time") is not None:
        parts.append(f"Load={metrics['load_time']}ms")

    summary = "Web Vitals: " + (", ".join(parts) if parts else "metrics unavailable")

    errors = []
    if "max_lcp_ms" in thresholds and metrics.get("lcp") is not None:
        if metrics["lcp"] > thresholds["max_lcp_ms"]:
            errors.append(f"LCP {metrics['lcp']}ms exceeded max_lcp_ms {thresholds['max_lcp_ms']}ms")
    if "max_fcp_ms" in thresholds and metrics.get("fcp") is not None:
        if metrics["fcp"] > thresholds["max_fcp_ms"]:
            errors.append(f"FCP {metrics['fcp']}ms exceeded max_fcp_ms {thresholds['max_fcp_ms']}ms")
    if "max_ttfb_ms" in thresholds and metrics.get("ttfb") is not None:
        if metrics["ttfb"] > thresholds["max_ttfb_ms"]:
            errors.append(f"TTFB {metrics['ttfb']}ms exceeded max_ttfb_ms {thresholds['max_ttfb_ms']}ms")
    if "max_cls" in thresholds and metrics.get("cls") is not None:
        if metrics["cls"] > thresholds["max_cls"]:
            errors.append(f"CLS {metrics['cls']} exceeded max_cls {thresholds['max_cls']}")
    if "max_load_ms" in thresholds and metrics.get("load_time") is not None:
        if metrics["load_time"] > thresholds["max_load_ms"]:
            errors.append(f"Load time {metrics['load_time']}ms exceeded max_load_ms {thresholds['max_load_ms']}ms")

    error_msg = "; ".join(errors) if errors else None
    return summary, error_msg


async def execute_step(page: "Page", step: dict, base_url: str,
                       screenshot_path: Optional[str] = None,
                       app: str = "", environment: str = "") -> StepResult:
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
        if "save_storage_state" in step:
            cfg = step["save_storage_state"]
            state_file = (
                os.path.join("data", "storage_states", f"{app}_{environment}.json")
                if (cfg is True or not cfg) and app
                else str(cfg)
            )
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            ctx = getattr(page, "context", None)
            if ctx and hasattr(ctx, "storage_state"):
                res = ctx.storage_state(path=state_file)
                if inspect.isawaitable(res):
                    await res
            return StepResult(step=f"save_storage_state {state_file}", status="pass", duration_ms=_elapsed())
        if "add_cookies" in step:
            cookies = step["add_cookies"]
            if not isinstance(cookies, list):
                cookies = [cookies]
            ctx = getattr(page, "context", None)
            if ctx and hasattr(ctx, "add_cookies"):
                res = ctx.add_cookies(cookies)
                if inspect.isawaitable(res):
                    await res
            return StepResult(step=f"add_cookies ({len(cookies)} cookies)", status="pass", duration_ms=_elapsed())
        if "select_option" in step:
            s = step["select_option"]
            field = s["field"]
            # Playwright accepts {value: ...}, {label: ...}, or {index: ...}.
            kwargs = {k: s[k] for k in ("value", "label", "index") if k in s}
            if not kwargs:
                return StepResult(step=f"select_option {field}", status="error",
                                  duration_ms=_elapsed(),
                                  error="select_option requires one of value/label/index")
            await page.select_option(field, **kwargs)
            return StepResult(step=f"select_option {field} {kwargs}",
                              status="pass", duration_ms=_elapsed())
        if "press_key" in step:
            target = step["press_key"]
            # Either a bare "Enter" (focused element) or {field: ..., key: ...}.
            if isinstance(target, dict):
                await page.press(target["field"], target["key"])
                desc = f"press_key {target['key']} on {target['field']}"
            else:
                await page.keyboard.press(str(target))
                desc = f"press_key {target}"
            return StepResult(step=desc, status="pass", duration_ms=_elapsed())
        if "hover" in step:
            await page.hover(step["hover"])
            return StepResult(step=f"hover {step['hover']}", status="pass", duration_ms=_elapsed())
        if "wait_for_function" in step:
            expr = step["wait_for_function"]
            await page.wait_for_function(expr)
            return StepResult(step=f"wait_for_function {expr}", status="pass", duration_ms=_elapsed())
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
        if "wait" in step:
            ms = int(step["wait"])
            await page.wait_for_timeout(ms)
            return StepResult(step=f"wait {ms}ms", status="pass", duration_ms=_elapsed())
        if "wait_for_url" in step:
            expected = step["wait_for_url"]
            await page.wait_for_url(f"**{expected}**")
            return StepResult(step=f"wait_for_url {expected}", status="pass", duration_ms=_elapsed())
        if "wait_for_selector" in step:
            selector = step["wait_for_selector"]
            await page.wait_for_selector(selector)
            return StepResult(step=f"wait_for_selector {selector}", status="pass", duration_ms=_elapsed())
        if "screenshot" in step:
            if not screenshot_path:
                return StepResult(step="screenshot", status="error", duration_ms=_elapsed(),
                                  error="No screenshot path provided")
            os.makedirs(os.path.dirname(screenshot_path), exist_ok=True)
            await page.screenshot(path=screenshot_path)
            rel = os.path.relpath(screenshot_path, "data/screenshots").replace("\\", "/")
            return StepResult(step="screenshot", status="pass", duration_ms=_elapsed(),
                              screenshot=rel)
        if any(k in step for k in ("assert_performance", "performance_metrics", "web_vitals", "capture_web_vitals")):
            thresholds = {}
            for k in ("assert_performance", "performance_metrics", "web_vitals"):
                val = step.get(k)
                if isinstance(val, dict):
                    thresholds.update(val)
            metrics = await extract_web_vitals(page)
            summary, error_msg = evaluate_performance_thresholds(metrics, thresholds)
            status = "fail" if error_msg else "pass"
            return StepResult(step=summary, status=status, duration_ms=_elapsed(), error=error_msg)
        return StepResult(step=str(step), status="error", duration_ms=_elapsed(),
                          error=f"Unknown step type: {list(step.keys())}")
    except Exception as e:
        return StepResult(step=str(step), status="error", duration_ms=_elapsed(), error=str(e))

async def run_browser_test(run_id: str, app: str, environment: str,
                           base_url: str, test_def: dict,
                           screenshot_dir: str = "data/screenshots",
                           headless: bool = True, timeout_ms: int = 30000,
                           proxy: Optional[dict] = None,
                           record_url_steps: bool = False) -> TestResult:
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    step_log = []
    start = time.monotonic()
    # ``SSL_CERT_FILE`` is OpenSSL-only -- Chromium ignores it and uses NSS
    # on Linux. We still maintain the env var here so any in-process httpx
    # call that runs alongside the browser job uses the bundle; Chromium
    # trust is handled separately via ``chromium_spki_args`` below. If the
    # bundle was removed (admin deleted the last cert), unset the env var
    # so we don't point at a vanished file.
    if os.path.exists(BUNDLE_PATH):
        os.environ["SSL_CERT_FILE"] = BUNDLE_PATH
    elif os.environ.get("SSL_CERT_FILE") == BUNDLE_PATH:
        os.environ.pop("SSL_CERT_FILE", None)
    # Trust the admin-uploaded CAs in Chromium by pinning their SPKI hashes
    # via ``--ignore-certificate-errors-spki-list``. Empty list when the
    # bundle is absent -- normal launch, normal trust.
    launch_args = chromium_spki_args(BUNDLE_PATH)
    launch_kwargs: dict = {"headless": headless, "args": launch_args}
    if proxy is not None:
        launch_kwargs["proxy"] = proxy
    else:
        from harness.proxy import get_playwright_proxy_args
        pw_proxy = get_playwright_proxy_args()
        if pw_proxy:
            launch_kwargs["proxy"] = pw_proxy
    # PEP-562 ``__getattr__`` only fires for attribute access on the module
    # (``browser_mod.async_playwright``), not bare-name lookup inside the
    # module itself -- which would raise ``NameError`` on the full image. So
    # resolve via ``globals()`` first (tests' ``monkeypatch.setattr`` writes
    # the fake there) and fall back to importing playwright on demand.
    ap = globals().get("async_playwright")
    if ap is None:
        from playwright.async_api import async_playwright as ap
    async with ap() as pw:
        browser = await pw.chromium.launch(**launch_kwargs)
        page = await browser.new_page()
        # Set extra HTTP headers if configured (e.g. Bearer tokens, API keys, proxy auth)
        headers = test_def.get("headers")
        if headers and hasattr(page, "set_extra_http_headers"):
            res = page.set_extra_http_headers({str(k): str(v) for k, v in headers.items()})
            if inspect.isawaitable(res):
                await res
        # Initialize URL recorder if requested
        recorder: URLRecorder | None = None
        if record_url_steps:
            recorder = URLRecorder(page, base_url)
            await recorder.start()
        page.set_default_timeout(timeout_ms)
        steps = list(test_def.get("steps", []))
        if not steps and record_url_steps:
            steps = [{"navigate": "/"}]
        slug = slugify_test_name(test_def["name"])
        failed = False
        for i, step in enumerate(steps):
            step_shot = (
                os.path.join(screenshot_dir, app, environment, run_id, f"{slug}-step-{i}.png")
                if "screenshot" in step else None
            )
            step_result = await execute_step(page, step, base_url, screenshot_path=step_shot)
            step_log.append(step_result)
            if step_result.status in ("fail", "error"):
                failed = True
                failure_shot = os.path.join(screenshot_dir, app, environment, run_id, f"{slug}.png")
                os.makedirs(os.path.dirname(failure_shot), exist_ok=True)
                await page.screenshot(path=failure_shot)
                result.screenshot = os.path.join(app, environment, run_id, f"{slug}.png")
                result.error_msg = step_result.error
                break
        # Collect recorded navigation steps if recorder was active
        if recorder is not None:
            recorded_steps = await recorder.stop()
            if record_url_steps and recorded_steps:
                recorded_results = [
                    StepResult(step=f"navigate {s['navigate']}", status="pass", duration_ms=0)
                    for s in recorded_steps
                ]
                step_log = recorded_results + step_log

        if not failed:
            top_thresholds = {
                k: test_def[k]
                for k in ("max_lcp_ms", "max_fcp_ms", "max_ttfb_ms", "max_cls", "max_load_ms")
                if k in test_def
            }
            if "assert_performance" in test_def and isinstance(test_def["assert_performance"], dict):
                top_thresholds.update(test_def["assert_performance"])

            if top_thresholds or test_def.get("capture_web_vitals"):
                metrics = await extract_web_vitals(page)
                summary, error_msg = evaluate_performance_thresholds(metrics, top_thresholds)
                perf_status = "fail" if error_msg else "pass"
                step_log.append(StepResult(step=summary, status=perf_status, duration_ms=0, error=error_msg))
                if error_msg:
                    failed = True
                    result.error_msg = error_msg
                    failure_shot = os.path.join(screenshot_dir, app, environment, run_id, f"{slug}.png")
                    os.makedirs(os.path.dirname(failure_shot), exist_ok=True)
                    try:
                        await page.screenshot(path=failure_shot)
                        result.screenshot = os.path.join(app, environment, run_id, f"{slug}.png")
                    except Exception:
                        pass

        await browser.close()
    result.status = "fail" if failed else "pass"
    result.step_log = step_log
    result.duration_ms = int((time.monotonic() - start) * 1000)
    result.finished_at = datetime.now(timezone.utc).isoformat()
    return result

async def run_availability_test(run_id: str, app: str, environment: str,
                                base_url: str, test_def: dict,
                                ssl_ctx: Optional[ssl.SSLContext] = None,
                                proxy: Optional[str] = None) -> TestResult:
    import httpx
    result = TestResult(run_id=run_id, app=app, environment=environment,
                        test_name=test_def["name"])
    start = time.monotonic()
    expect_status = test_def.get("expect_status", 200)
    try:
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
