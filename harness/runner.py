import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from harness.db import Database
from harness.models import Run, AppState, StepResult, TestResult
from harness.loader import resolve_base_url
from harness.api import run_api_test
from harness.browser import run_browser_test, run_availability_test
from harness.alerts import dispatch_alerts, dispatch_run_webhook
from harness.types import AlertType
from harness.ssl_context import get_ssl_context, write_ca_bundle
from harness.screenshot_diff import compute_diff

_log = logging.getLogger(__name__)

SCREENSHOTS_DIR = "data/screenshots"

def determine_alert(previous_state: str, new_status: str) -> Optional[AlertType]:
    is_fail = new_status in ("fail", "error")
    if is_fail and previous_state in ("unknown", "passing"):
        return AlertType.FAIL
    if not is_fail and previous_state == "failing":
        return AlertType.RESOLVE
    return None

def _apply_slo_threshold(test_def: dict, result: TestResult) -> None:
    """Tier-2: fail an otherwise-passing test if its duration exceeds max_duration_ms.

    Mutates `result` in place. No-op if the test didn't pass, the threshold
    isn't set, or duration_ms is None (e.g. exception path).
    """
    if result.status != "pass":
        return
    max_ms = test_def.get("max_duration_ms")
    if not max_ms or result.duration_ms is None:
        return
    if result.duration_ms <= max_ms:
        return
    result.step_log.append(StepResult(
        step=f"SLO check: {result.duration_ms}ms > {max_ms}ms",
        status="fail",
        duration_ms=0,
    ))
    result.status = "fail"
    result.error_msg = (
        f"SLO violation: {result.duration_ms}ms exceeds max_duration_ms={max_ms}"
    )


async def _execute_test(test_def: dict, run_id: str, app: str, environment: str,
                        base_url: str, ssl_ctx, browser_cfg: dict, db=None):
    """Run a single test with optional retry. Returns TestResult."""
    max_retries = int(test_def.get("retry", 0))
    test_type = test_def.get("type", "availability")
    timeout_ms = browser_cfg.get("timeout_ms", 30000)
    result = None
    for _attempt in range(max_retries + 1):
        if test_type == "api":
            result = await run_api_test(run_id, app, environment, base_url,
                                        test_def, ssl_ctx=ssl_ctx)
        elif test_type == "browser":
            test_timeout_ms = test_def.get("timeout_ms", timeout_ms)
            result = await run_browser_test(run_id, app, environment, base_url,
                                            test_def,
                                            headless=browser_cfg.get("headless", True),
                                            timeout_ms=test_timeout_ms)
            # Screenshot diff check (only on pass, only if previous screenshot exists)
            if result.screenshot and result.status == "pass" and db is not None:
                prev_shot = db.get_last_screenshot(app, environment, test_def["name"])
                if prev_shot:
                    diff_threshold = browser_cfg.get("screenshot_diff_threshold", 0.05)
                    diff = compute_diff(
                        f"{SCREENSHOTS_DIR}/{prev_shot}",
                        f"{SCREENSHOTS_DIR}/{result.screenshot}",
                    )
                    if diff > diff_threshold:
                        result.step_log.append(StepResult(
                            step=f"Screenshot diff: {diff:.1%} pixels changed "
                                 f"(threshold {diff_threshold:.0%})",
                            status="fail",
                            duration_ms=0,
                        ))
                        result.status = "fail"
                        result.error_msg = (
                            f"Visual regression: {diff:.1%} pixels changed"
                        )
        else:
            result = await run_availability_test(run_id, app, environment, base_url,
                                                 test_def, ssl_ctx=ssl_ctx)
        _apply_slo_threshold(test_def, result)
        if result.status not in ("fail", "error"):
            break
    return result

async def run_app(app_def: dict, environment: str, triggered_by: str,
                  db: Database, config: dict, run_id: str = None,
                  secrets_store=None) -> str:
    from harness.config import resolve_env_vars
    extra_vars = secrets_store.get_all_decrypted() if secrets_store is not None else {}

    # Tier-2: collect names that subsequent tests will `extract:` so the
    # app-level resolver doesn't choke on `$AUTH_TOKEN` placeholders.  Each
    # capture name is seeded with its own literal `$NAME` value, which the
    # resolver substitutes back identically — leaving the placeholder intact
    # for per-test re-resolution against the live `captures` dict.
    capture_seeds = {}
    has_extracts = False
    for _td in app_def.get("tests", []):
        for _capture_name in (_td.get("extract") or {}).keys():
            capture_seeds[_capture_name] = "$" + _capture_name
            has_extracts = True
    app_def = resolve_env_vars(
        app_def, strict=True, extra_vars={**extra_vars, **capture_seeds}
    )
    if run_id:
        run = Run(id=run_id, app=app_def["app"], environment=environment, triggered_by=triggered_by)
    else:
        run = Run(app=app_def["app"], environment=environment, triggered_by=triggered_by)
        db.insert_run(run)
    db.update_run_status(run.id, "running",
                         started_at=datetime.now(timezone.utc).isoformat())
    base_url = resolve_base_url(app_def, environment)
    browser_cfg = config.get("browser", {})
    alerts_cfg = config.get("alerts", {})
    ssl_ctx = get_ssl_context(db)
    write_ca_bundle(db)

    test_defs = app_def.get("tests", [])
    sequential = bool(app_def.get("sequential")) or has_extracts

    if sequential:
        # Tier-2: run tests in order so `extract:` captures are available to
        # subsequent tests' `$VAR` references.  Always re-resolve per-test
        # (even with no captures yet) so `$AUTH_TOKEN` placeholders that were
        # seeded at app-start fail loudly when their capture never happens.
        captures: dict = {}
        results = []
        for td in test_defs:
            try:
                resolved_td = resolve_env_vars(td, strict=True, extra_vars=captures)
                tr = await _execute_test(
                    resolved_td, run.id, run.app, environment, base_url,
                    ssl_ctx, browser_cfg, db=db,
                )
            except BaseException as e:  # noqa: BLE001 — match parallel branch
                results.append(e)
                continue
            results.append(tr)
            if tr is not None and tr.captures:
                captures.update(tr.captures)
    else:
        results = await asyncio.gather(
            *[_execute_test(td, run.id, run.app, environment, base_url,
                            ssl_ctx, browser_cfg, db=db)
              for td in test_defs],
            return_exceptions=True,
        )

    alerts_to_send = []
    for test_def, result in zip(test_defs, results):
        if isinstance(result, BaseException):
            _log.error(
                "Unhandled exception in test %r (app=%s env=%s): %s",
                test_def.get("name", "unknown"), run.app, environment, result,
                exc_info=result,
            )
            err_result = TestResult(
                run_id=run.id,
                app=run.app,
                environment=environment,
                test_name=test_def.get("name", "unknown"),
                status="error",
                error_msg=f"Internal error: {type(result).__name__}: {result}",
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
            db.insert_test_result(err_result)
            db.upsert_app_state(AppState(
                app=run.app, environment=environment,
                test_name=test_def.get("name", "unknown"),
                state="failing",
                since=err_result.finished_at,
            ))
            continue
        db.insert_test_result(result)
        prev = db.get_app_state(run.app, environment, result.test_name)
        prev_state = prev["state"] if prev else "unknown"
        new_state_str = "passing" if result.status == "pass" else "failing"
        new_state = AppState(
            app=run.app, environment=environment,
            test_name=result.test_name, state=new_state_str,
            since=result.finished_at or datetime.now(timezone.utc).isoformat(),
        )
        db.upsert_app_state(new_state)
        alert_type = determine_alert(prev_state, result.status)
        if alert_type:
            alerts_to_send.append(
                (alert_type, run.app, environment, result.test_name, result.error_msg)
            )

    db.update_run_status(run.id, "complete",
                         finished_at=datetime.now(timezone.utc).isoformat())
    if alerts_to_send:
        await dispatch_alerts(alerts_to_send, alerts_cfg)
    webhook_cfg = config.get("alerts", {}).get("webhook", {})
    if webhook_cfg.get("url"):
        finished_run = db.get_run(run.id)
        run_results = db.get_results_for_run(run.id)
        await dispatch_run_webhook(
            run.id, run.app, environment, "complete",
            triggered_by, finished_run["finished_at"] or "",
            run_results, webhook_cfg,
        )
    return run.id
