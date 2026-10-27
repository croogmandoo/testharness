"""Maintenance windows — suppress alerts during planned downtime.

Supports:
  1. Recurring (cron-based):
       cron: "0 2 * * 6"          # Saturday 02:00 UTC
       duration_minutes: 120      # window stays open 2 hours

  2. One-shot (fixed range):
       start: "2026-12-23T00:00:00Z"
       end:   "2026-12-27T23:59:59Z"

  3. Weekly recurrence:
       days_of_week: ["sunday"]
       start_time: "02:00"
       end_time: "04:00"          # or duration_minutes: 120
       timezone: "America/New_York" # default "UTC"

  4. Database-backed maintenance windows with app and environment targeting.

The predicate `is_in_maintenance(config, now=None, app=None, environment=None, db=None)`
returns the matched window's name (truthy) or None. Runs themselves are NEVER
suppressed — only the alert dispatch is gated, so dashboards/audit/metrics still see
the real state.
"""
import json
import logging
from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional, Any, List, Dict
import zoneinfo

_log = logging.getLogger(__name__)

DAY_NAME_TO_WEEKDAY = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _parse_iso(value: str) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        s = value.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_time(value: str) -> Optional[dtime]:
    if not isinstance(value, str):
        return None
    try:
        parts = value.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        second = int(parts[2]) if len(parts) > 2 else 0
        return dtime(hour=hour, minute=minute, second=second)
    except (ValueError, IndexError, TypeError):
        return None


def _get_tz(tz_name: Optional[str]) -> timezone | zoneinfo.ZoneInfo:
    if not tz_name or tz_name.upper() == "UTC":
        return timezone.utc
    try:
        return zoneinfo.ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _cron_window_active(cron: str, duration_minutes: int, now: datetime) -> bool:
    """True if `now` falls inside the most recent firing of `cron` and the
    next `duration_minutes` after it."""
    try:
        from harness.auto_scheduler import cron_trigger
    except ImportError:  # pragma: no cover
        return False
    try:
        trigger = cron_trigger(cron, timezone="UTC")
    except (ValueError, KeyError) as e:
        _log.warning("maintenance: invalid cron %r: %s", cron, e)
        return False

    horizon = now - timedelta(minutes=duration_minutes)
    candidate = trigger.get_next_fire_time(None, horizon)
    while candidate is not None and candidate <= now:
        end = candidate + timedelta(minutes=duration_minutes)
        if candidate <= now < end:
            return True
        candidate = trigger.get_next_fire_time(candidate, candidate + timedelta(seconds=1))
    return False


def _weekly_window_active(
    days_of_week: Any,
    start_time_val: Any,
    end_time_val: Any,
    duration_minutes: Any,
    tz_str: Optional[str],
    now: datetime,
) -> bool:
    """Check if `now` falls within a recurring weekly window."""
    if not days_of_week or not start_time_val:
        return False

    target_days = set()
    if isinstance(days_of_week, str):
        try:
            days_of_week = json.loads(days_of_week)
        except Exception:
            days_of_week = [days_of_week]

    if not isinstance(days_of_week, (list, set, tuple)):
        days_of_week = [days_of_week]

    for d in days_of_week:
        if isinstance(d, int):
            # If 0-6 where 0=Monday (Python convention) or 0=Sunday (cron)
            # Map standard cron 0 -> 6 (Sunday) or accept 0-6 as Python weekday
            target_days.add(d % 7)
        elif isinstance(d, str):
            clean = d.strip().lower()
            if clean in DAY_NAME_TO_WEEKDAY:
                target_days.add(DAY_NAME_TO_WEEKDAY[clean])

    if not target_days:
        return False

    tz = _get_tz(tz_str)
    local_now = now.astimezone(tz)
    st = _parse_time(str(start_time_val))
    if not st:
        return False

    # Check for both today and yesterday in case window spans midnight
    today_date = local_now.date()
    yesterday_date = today_date - timedelta(days=1)

    for test_date in (today_date, yesterday_date):
        test_weekday = test_date.weekday()
        if test_weekday not in target_days:
            continue

        window_start = datetime.combine(test_date, st, tzinfo=tz)
        if duration_minutes and int(duration_minutes) > 0:
            window_end = window_start + timedelta(minutes=int(duration_minutes))
        elif end_time_val:
            et = _parse_time(str(end_time_val))
            if not et:
                continue
            window_end = datetime.combine(test_date, et, tzinfo=tz)
            if window_end <= window_start:
                # Spans midnight to the next day
                window_end += timedelta(days=1)
        else:
            continue

        if window_start <= local_now < window_end:
            return True

    return False


def _matches_target(
    window: dict,
    app: Optional[str] = None,
    environment: Optional[str] = None,
) -> bool:
    w_app = window.get("app")
    if w_app and app and w_app.strip().lower() != app.strip().lower():
        return False
    if w_app and not app:
        # App-scoped window does not match general checks
        return False

    w_env = window.get("environment")
    if w_env and environment and w_env.strip().lower() != environment.strip().lower():
        return False
    if w_env and not environment:
        return False

    return True


def is_in_maintenance(
    config: Optional[dict] = None,
    now: Optional[datetime] = None,
    app: Optional[str] = None,
    environment: Optional[str] = None,
    db: Optional[Any] = None,
) -> Optional[str]:
    """Returns the matched window name when `now` is inside any configured
    maintenance window, otherwise None.

    Checks both `config.yaml` (`alerts.maintenance_windows`) and database-defined
    maintenance windows when `db` is supplied.
    """
    config_windows = ((config or {}).get("alerts") or {}).get("maintenance_windows") or []
    db_windows = []
    if db is not None and hasattr(db, "list_maintenance_windows"):
        try:
            db_windows = db.list_maintenance_windows(enabled_only=True, app=app, environment=environment)
        except Exception as exc:
            _log.warning("maintenance: failed to load windows from db: %s", exc)

    all_windows = list(config_windows) + list(db_windows)
    if not all_windows:
        return None

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    for w in all_windows:
        if not isinstance(w, dict):
            continue

        # Filter enabled flag (default true if omitted)
        if not w.get("enabled", True):
            continue

        # Check app and environment targeting
        if not _matches_target(w, app=app, environment=environment):
            continue

        name = w.get("name") or "(unnamed)"
        w_type = (w.get("window_type") or "").lower()

        # 1. Weekly recurring window
        days = w.get("days_of_week")
        if w_type == "weekly" or days:
            start_t = w.get("start_time") or w.get("start")
            end_t = w.get("end_time") or w.get("end")
            dur = w.get("duration_minutes")
            tz_str = w.get("timezone") or "UTC"
            if _weekly_window_active(days, start_t, end_t, dur, tz_str, now):
                return name

        # 2. Fixed range: start + end
        start_raw = w.get("start_time") if w_type == "range" else (w.get("start") or w.get("start_time"))
        end_raw = w.get("end_time") if w_type == "range" else (w.get("end") or w.get("end_time"))
        start = _parse_iso(start_raw)
        end = _parse_iso(end_raw)
        if start and end and start <= now <= end:
            return name

        # 3. Recurring: cron + duration_minutes
        cron = w.get("cron")
        duration = w.get("duration_minutes")
        if cron and isinstance(duration, (int, float)) and duration > 0:
            if _cron_window_active(cron, int(duration), now):
                return name

    return None
