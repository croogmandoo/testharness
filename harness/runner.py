import asyncio
from datetime import datetime, timezone
from typing import Optional
from harness.db import Database
from harness.models import Run, AppState
from harness.loader import resolve_base_url
from harness.api import run_api_test
from harness.browser import run_browser_test, run_availability_test
from harness.alerts import dispatch_alerts
from harness.types import AlertType

def determine_alert(previous_state: str, new_status: str) -> Optional[AlertType]:
    is_fail = new_status in ("fail", "error")
    if is_fail and previous_state in ("unknown", "passing"):
        return AlertType.FAIL
    if not is_fail and previous_state == "failing":
        return AlertType.RESOLVE
    return None

async def run_app(app_def: dict, environment: str, triggered_by: str,
                  db: Database, config: dict, run_id: str = None,
                  secrets_store=None) -> str:
    if secrets_store is not None:
        secrets_store.inject_to_env()
    if run_id:
        run = Run(id=run_id, app=app_def["app"], environment=environment, triggered_by=triggered_by)
    else:
        run = Run(app=app_def["app"], environment=environment, triggered_by=triggered_by)
        db.insert_run(run)
    db.update_run_status(run.id, "running",
                         started_at=datetime.now(timezone.utc).isoformat())
    base_url = resolve_base_url(app_def, environment)
    browser_cfg = config.get("browser", {})
    headless = browser_cfg.get("headless", True)
    timeout_ms = browser_cfg.get("timeout_ms", 30000)
    alerts_cfg = config.get("alerts", {})
    alerts_to_send = []
    for test_def in app_def.get("tests", []):
        test_type = test_def.get("type", "availability")
        if test_type == "api":
            result = await run_api_test(run.id, run.app, environment, base_url, test_def)
        elif test_type == "browser":
            result = await run_browser_test(run.id, run.app, environment, base_url,
                                            test_def, headless=headless, timeout_ms=timeout_ms)
        else:
            result = await run_availability_test(run.id, run.app, environment, base_url, test_def)
        db.insert_test_result(result)
        prev = db.get_app_state(run.app, environment, test_def["name"])
        prev_state = prev["state"] if prev else "unknown"
        new_state_str = "passing" if result.status == "pass" else "failing"
        new_state = AppState(app=run.app, environment=environment,
                             test_name=test_def["name"], state=new_state_str,
                             since=result.finished_at or datetime.now(timezone.utc).isoformat())
        db.upsert_app_state(new_state)
        alert_type = determine_alert(prev_state, result.status)
        if alert_type:
            alerts_to_send.append((alert_type, run.app, environment, test_def["name"],
                                   result.error_msg))
    db.update_run_status(run.id, "complete",
                         finished_at=datetime.now(timezone.utc).isoformat())
    if alerts_to_send:
        await dispatch_alerts(alerts_to_send, alerts_cfg)
    return run.id
