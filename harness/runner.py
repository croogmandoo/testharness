import asyncio
from datetime import datetime, timezone
from typing import Optional
from harness.db import Database
from harness.models import Run, AppState
from harness.loader import resolve_base_url
from harness.api import run_api_test
from harness.browser import run_browser_test, run_availability_test
from harness.alerts import dispatch_alerts, dispatch_run_webhook
from harness.types import AlertType
from harness.ssl_context import get_ssl_context, write_ca_bundle

def determine_alert(previous_state: str, new_status: str) -> Optional[AlertType]:
    is_fail = new_status in ("fail", "error")
    if is_fail and previous_state in ("unknown", "passing"):
        return AlertType.FAIL
    if not is_fail and previous_state == "failing":
        return AlertType.RESOLVE
    return None

async def _execute_test(test_def: dict, run_id: str, app: str, environment: str,
                        base_url: str, ssl_ctx, browser_cfg: dict):
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
        else:
            result = await run_availability_test(run_id, app, environment, base_url,
                                                 test_def, ssl_ctx=ssl_ctx)
        if result.status not in ("fail", "error"):
            break
    return result

async def run_app(app_def: dict, environment: str, triggered_by: str,
                  db: Database, config: dict, run_id: str = None,
                  secrets_store=None) -> str:
    if secrets_store is not None:
        secrets_store.inject_to_env()
    from harness.config import resolve_env_vars
    app_def = resolve_env_vars(app_def, strict=True)
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
    results = await asyncio.gather(
        *[_execute_test(td, run.id, run.app, environment, base_url,
                        ssl_ctx, browser_cfg)
          for td in test_defs],
        return_exceptions=True,
    )

    alerts_to_send = []
    for result in results:
        if isinstance(result, BaseException):
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
