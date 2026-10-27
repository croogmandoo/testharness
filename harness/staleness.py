"""Stale-run detection — catch apps whose monitoring has gone silent.

If an app is configured with `schedule:` (or has a run history) but its most
recent completed run is older than `alerts.stale_threshold_minutes`
(default 1440 = 24h), we dispatch a STALE alert.  Re-alerts are suppressed
for `alerts.stale_realert_hours` (default 24h) per app/env to avoid
hourly noise.

A normal completion *clears* the dedup, so the next time the app goes
stale you'll be alerted again.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from harness.types import AlertType
from harness.maintenance import is_in_maintenance

_log = logging.getLogger(__name__)

DEFAULT_THRESHOLD_MIN = 24 * 60       # 24 hours
DEFAULT_REALERT_HOURS = 24


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        s = value.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _staleness_config(config: dict) -> dict:
    alerts_cfg = (config or {}).get("alerts") or {}
    return {
        "threshold_min": int(alerts_cfg.get("stale_threshold_minutes",
                                            DEFAULT_THRESHOLD_MIN)),
        "realert_hours": int(alerts_cfg.get("stale_realert_hours",
                                            DEFAULT_REALERT_HOURS)),
        "enabled": alerts_cfg.get("stale_check_enabled", True),
    }


def find_stale_apps(db, apps: list, config: dict,
                    now: Optional[datetime] = None) -> list:
    """Return alert tuples (AlertType.STALE, app, env, msg, details) for
    apps whose most-recent completed run is older than the threshold AND
    whose dedup window has elapsed.

    Apps with no runs at all are skipped — we'd alert on unconfigured
    things otherwise.  Maintenance-window-active alerts are also skipped.
    """
    cfg = _staleness_config(config)
    if not cfg["enabled"]:
        return []
    if is_in_maintenance(config, now=now, db=db):
        return []
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    threshold = timedelta(minutes=cfg["threshold_min"])
    realert = timedelta(hours=cfg["realert_hours"])
    alerts: list = []
    envs = list((config.get("environments") or {}).keys())

    for app_def in apps:
        app_name = app_def["app"]
        app_envs = list((app_def.get("environments") or {}).keys())
        # Only check envs the app knows about, and only those declared in
        # global config (so an app YAML doesn't widen the scope).
        for env in [e for e in app_envs if e in envs]:
            if is_in_maintenance(config, now=now, app=app_name, environment=env, db=db):
                continue
            last_finished_str = db.get_app_last_finished_at(app_name, env)
            last_finished = _parse_iso(last_finished_str)
            if last_finished is None:
                continue  # no run history -> not yet "stale", just unused
            age = now - last_finished
            if age <= threshold:
                continue
            # Dedup: skip if we've already alerted recently
            last_alert = _parse_iso(db.get_last_stale_alert_at(app_name, env))
            if last_alert is not None and (now - last_alert) < realert:
                continue
            db.mark_stale_alerted(app_name, env, now.isoformat())
            hours = int(age.total_seconds() // 3600)
            threshold_hours = int(threshold.total_seconds() // 3600)
            summary = f"no run in {hours}h, threshold {threshold_hours}h"
            details = f"last run finished at {last_finished_str}"
            alerts.append((AlertType.STALE, app_name, env, summary, details))
    return alerts


async def check_and_dispatch(db, apps: list, config: dict,
                             now: Optional[datetime] = None) -> int:
    """Run the staleness check and dispatch matching alerts.  Returns the
    number of alerts sent."""
    from harness.alerts import dispatch_alerts
    alerts = find_stale_apps(db, apps, config, now=now)
    if not alerts:
        return 0
    alerts_cfg = (config or {}).get("alerts") or {}
    await dispatch_alerts(alerts, alerts_cfg)
    _log.info("staleness: dispatched %d STALE alert(s)", len(alerts))
    return len(alerts)
