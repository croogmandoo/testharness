"""Automated Synthetic SSL Renewal Verification and Expedited Re-checking.

Scans for applications whose monitored TLS certificates are failing or expiring (< 30d),
triggers immediate synthetic re-checks, and emits resolution alerts when certificates
are renewed (> 60d).
"""

import logging
from typing import List, Dict, Any, Optional

from harness.models import TestResult
from harness.ssl_context import get_ssl_context
from harness.tls_engine import run_tls_test

_log = logging.getLogger(__name__)


def find_tls_tests_for_app(app_def: dict) -> List[dict]:
    """Return all TLS/certificate test definitions inside an app definition."""
    results = []
    for td in app_def.get("tests", []):
        if td.get("type") in ("tls", "certificate", "ssl"):
            results.append(td)
    return results


def get_expiring_or_failing_tls_apps(
    db: Any,
    apps: List[dict],
    environments: Optional[List[str]] = None,
    threshold_days: float = 30.0,
) -> List[Dict[str, Any]]:
    """Find applications whose latest TLS certificate status is either failing or < threshold_days."""
    if not hasattr(db, "get_latest_cert_statuses"):
        return []

    targets = []
    # Build lookup of app definitions
    app_map = {a.get("app"): a for a in apps if a.get("app")}

    if not environments:
        environments = ["production", "staging", "prod"]

    for env in environments:
        cert_map = db.get_latest_cert_statuses(env)
        for app_name, info in cert_map.items():
            if app_name not in app_map:
                continue
            app_def = app_map[app_name]
            # Ensure app actually has a TLS test
            if not find_tls_tests_for_app(app_def):
                continue

            days = info.get("days_remaining")
            status = info.get("status")
            if status in ("fail", "error") or (days is not None and days < threshold_days):
                targets.append({
                    "app": app_name,
                    "environment": env,
                    "app_def": app_def,
                    "days_remaining": days,
                    "status": status,
                    "issuer": info.get("issuer"),
                    "expires": info.get("expires"),
                })

    return targets


async def verify_app_tls(
    app_def: dict,
    environment: str,
    db: Any,
    config: dict,
    triggered_by: str = "tls_verifier",
) -> List[TestResult]:
    """Execute immediate synthetic TLS re-checks for an application and record results."""
    from harness.runner import run_app

    # We run the app via runner to trigger the full execution pipeline including
    # certificate renewal detection, DB recording, and alert dispatching.
    run_id = await run_app(
        app_def=app_def,
        environment=environment,
        triggered_by=triggered_by,
        db=db,
        config=config,
    )
    return db.get_results_for_run(run_id)


async def check_and_verify_expiring_certificates(
    db: Any,
    apps: List[dict],
    config: dict,
    threshold_days: float = 30.0,
) -> List[str]:
    """Scan all apps for expiring/failing TLS certificates and run expedited re-checks."""
    envs = list((config.get("environments") or {"production": {}}).keys())
    expiring = get_expiring_or_failing_tls_apps(db, apps, environments=envs, threshold_days=threshold_days)
    if not expiring:
        return []

    verified_apps = []
    for item in expiring:
        app_name = item["app"]
        env = item["environment"]
        app_def = item["app_def"]
        _log.info(
            "tls_verifier: expedited re-check for %s (%s) — current: status=%s, days=%.1f",
            app_name, env, item.get("status"), item.get("days_remaining") or 0.0,
        )
        try:
            await verify_app_tls(app_def, env, db, config)
            verified_apps.append(f"{app_name}:{env}")
        except Exception as exc:
            _log.warning("tls_verifier: failed to re-check %s (%s): %s", app_name, env, exc)

    return verified_apps
