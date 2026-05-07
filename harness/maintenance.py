"""Maintenance windows — suppress alerts during planned downtime.

Two window flavors, both expressed in `config.yaml` under
`alerts.maintenance_windows`:

  1. Recurring (cron-based):
       cron: "0 2 * * 6"          # Saturday 02:00 UTC
       duration_minutes: 120      # window stays open 2 hours

  2. One-shot (fixed range):
       start: "2026-12-23T00:00:00Z"
       end:   "2026-12-27T23:59:59Z"

The predicate `is_in_maintenance(config, now=None)` returns the matched
window's name (truthy) or None.  Runs themselves are NEVER suppressed —
only the alert dispatch is gated, so dashboards/audit/metrics still see
the real state and you can review what happened during the window.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

_log = logging.getLogger(__name__)


def _parse_iso(value: str) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        s = value.rstrip("Z")
        # accept "2026-12-23T00:00:00Z" (treat as UTC) or full RFC3339
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _cron_window_active(cron: str, duration_minutes: int,
                        now: datetime) -> bool:
    """True if `now` falls inside the most recent firing of `cron` and the
    next `duration_minutes` after it."""
    try:
        # Use the standard-cron-respecting wrapper so dow=0 means Sunday
        from harness.auto_scheduler import cron_trigger
    except ImportError:  # pragma: no cover
        return False
    try:
        trigger = cron_trigger(cron, timezone="UTC")
    except (ValueError, KeyError) as e:
        _log.warning("maintenance: invalid cron %r: %s", cron, e)
        return False
    # Walk back: when did the trigger most recently fire on or before `now`?
    # APScheduler exposes get_next_fire_time(prev, now); we use it to find
    # the next firing strictly after `now - duration` and check if that
    # window contains `now`.
    horizon = now - timedelta(minutes=duration_minutes)
    candidate = trigger.get_next_fire_time(None, horizon)
    while candidate is not None and candidate <= now:
        end = candidate + timedelta(minutes=duration_minutes)
        if candidate <= now < end:
            return True
        candidate = trigger.get_next_fire_time(candidate, candidate + timedelta(seconds=1))
    return False


def is_in_maintenance(config: dict,
                      now: Optional[datetime] = None) -> Optional[str]:
    """Returns the matched window name when `now` is inside any configured
    maintenance window, otherwise None.

    A truthy return is the signal to suppress alert dispatch.
    """
    windows = ((config or {}).get("alerts") or {}).get("maintenance_windows") or []
    if not windows:
        return None
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    for w in windows:
        if not isinstance(w, dict):
            continue
        name = w.get("name") or "(unnamed)"

        # Fixed range: start + end
        start = _parse_iso(w.get("start"))
        end = _parse_iso(w.get("end"))
        if start and end and start <= now <= end:
            return name

        # Recurring: cron + duration_minutes
        cron = w.get("cron")
        duration = w.get("duration_minutes")
        if cron and isinstance(duration, (int, float)) and duration > 0:
            if _cron_window_active(cron, int(duration), now):
                return name
    return None
