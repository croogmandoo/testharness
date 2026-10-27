"""Tests for harness.maintenance — alert suppression during planned downtime."""
from datetime import datetime, timezone

import pytest
from harness.maintenance import is_in_maintenance, _parse_iso


# ── _parse_iso ────────────────────────────────────────────────────────────────

def test_parse_iso_with_z_suffix():
    dt = _parse_iso("2026-12-23T00:00:00Z")
    assert dt == datetime(2026, 12, 23, 0, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_with_offset():
    dt = _parse_iso("2026-12-23T00:00:00+00:00")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_iso_naive_treated_as_utc():
    dt = _parse_iso("2026-12-23T00:00:00")
    assert dt is not None
    assert dt.tzinfo == timezone.utc


def test_parse_iso_returns_none_on_garbage():
    assert _parse_iso("not a date") is None
    assert _parse_iso(None) is None  # type: ignore[arg-type]
    assert _parse_iso(12345) is None  # type: ignore[arg-type]


# ── is_in_maintenance: empty config / no windows ─────────────────────────────

def test_no_config_returns_none():
    assert is_in_maintenance({}) is None
    assert is_in_maintenance(None) is None  # type: ignore[arg-type]
    assert is_in_maintenance({"alerts": {}}) is None
    assert is_in_maintenance({"alerts": {"maintenance_windows": []}}) is None


# ── Fixed-range windows ──────────────────────────────────────────────────────

def test_fixed_range_inside_window():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Holiday freeze",
        "start": "2026-12-23T00:00:00Z",
        "end":   "2026-12-27T23:59:59Z",
    }]}}
    name = is_in_maintenance(cfg, now=datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc))
    assert name == "Holiday freeze"


def test_fixed_range_before_start():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Freeze",
        "start": "2026-12-23T00:00:00Z",
        "end":   "2026-12-27T23:59:59Z",
    }]}}
    assert is_in_maintenance(cfg, now=datetime(2026, 12, 22, 23, 0, tzinfo=timezone.utc)) is None


def test_fixed_range_after_end():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Freeze",
        "start": "2026-12-23T00:00:00Z",
        "end":   "2026-12-27T23:59:59Z",
    }]}}
    assert is_in_maintenance(cfg, now=datetime(2026, 12, 28, 0, 0, 1, tzinfo=timezone.utc)) is None


def test_fixed_range_inclusive_at_boundaries():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Freeze",
        "start": "2026-12-23T00:00:00Z",
        "end":   "2026-12-27T23:59:59Z",
    }]}}
    assert is_in_maintenance(cfg, now=datetime(2026, 12, 23, 0, 0, 0, tzinfo=timezone.utc))
    assert is_in_maintenance(cfg, now=datetime(2026, 12, 27, 23, 59, 59, tzinfo=timezone.utc))


# ── Recurring (cron-based) windows ───────────────────────────────────────────

def test_recurring_inside_window():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Weekly maintenance",
        "cron": "0 2 * * 6",          # Saturday 02:00 UTC
        "duration_minutes": 120,
    }]}}
    # 2026-05-09 is a Saturday
    now = datetime(2026, 5, 9, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) == "Weekly maintenance"


def test_recurring_at_window_start_boundary():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Weekly", "cron": "0 2 * * 6", "duration_minutes": 120,
    }]}}
    assert is_in_maintenance(cfg, now=datetime(2026, 5, 9, 2, 0, tzinfo=timezone.utc))


def test_recurring_outside_window_after_duration():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Weekly", "cron": "0 2 * * 6", "duration_minutes": 120,
    }]}}
    # 2 hours + 1 second after Saturday 02:00 UTC
    now = datetime(2026, 5, 9, 4, 0, 1, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) is None


def test_recurring_outside_window_on_other_days():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Weekly", "cron": "0 2 * * 6", "duration_minutes": 120,
    }]}}
    # 2026-05-08 is a Friday
    now = datetime(2026, 5, 8, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) is None


def test_invalid_cron_skipped_not_raised():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Bad", "cron": "not-a-cron", "duration_minutes": 120,
    }]}}
    # Should not blow up — just returns None
    assert is_in_maintenance(cfg, now=datetime(2026, 5, 9, 2, 30, tzinfo=timezone.utc)) is None


def test_multiple_windows_first_match_wins():
    cfg = {"alerts": {"maintenance_windows": [
        {"name": "Sunday", "cron": "0 0 * * 0", "duration_minutes": 60},
        {"name": "Saturday", "cron": "0 2 * * 6", "duration_minutes": 120},
    ]}}
    name = is_in_maintenance(cfg, now=datetime(2026, 5, 9, 2, 30, tzinfo=timezone.utc))
    assert name == "Saturday"


def test_invalid_window_entry_ignored():
    """A non-dict entry must not crash the predicate."""
    cfg = {"alerts": {"maintenance_windows": [
        "not a dict",
        {"name": "OK", "cron": "0 2 * * 6", "duration_minutes": 120},
    ]}}
    assert is_in_maintenance(cfg, now=datetime(2026, 5, 9, 2, 30, tzinfo=timezone.utc)) == "OK"


# ── Weekly recurrence windows ────────────────────────────────────────────────

def test_weekly_recurring_inside_window():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Sunday Night DB Maintenance",
        "window_type": "weekly",
        "days_of_week": ["sunday"],
        "start_time": "02:00",
        "end_time": "04:00",
        "timezone": "UTC",
    }]}}
    # 2026-05-10 is a Sunday
    now = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) == "Sunday Night DB Maintenance"


def test_weekly_recurring_outside_window():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Sunday Night DB Maintenance",
        "window_type": "weekly",
        "days_of_week": ["sunday"],
        "start_time": "02:00",
        "duration_minutes": 120,
        "timezone": "UTC",
    }]}}
    # Sunday at 04:01 UTC
    now = datetime(2026, 5, 10, 4, 1, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) is None


def test_weekly_recurring_wrong_day():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Sunday Night DB Maintenance",
        "window_type": "weekly",
        "days_of_week": ["sunday"],
        "start_time": "02:00",
        "duration_minutes": 120,
    }]}}
    # 2026-05-11 is a Monday
    now = datetime(2026, 5, 11, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now) is None


def test_weekly_recurring_with_custom_timezone():
    # 02:00-04:00 in America/New_York (UTC-4 in May) is 06:00-08:00 UTC
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Sunday NY Maintenance",
        "window_type": "weekly",
        "days_of_week": ["sun"],
        "start_time": "02:00",
        "duration_minutes": 120,
        "timezone": "America/New_York",
    }]}}
    # 06:30 UTC on Sunday = 02:30 EDT on Sunday -> inside window
    now_inside = datetime(2026, 5, 10, 6, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now_inside) == "Sunday NY Maintenance"

    # 02:30 UTC on Sunday = 22:30 EDT Saturday -> outside window
    now_outside = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=now_outside) is None


def test_weekly_recurring_spans_midnight():
    # 23:00 to 02:00 spanning midnight
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Saturday Overnight Maintenance",
        "window_type": "weekly",
        "days_of_week": ["saturday"],
        "start_time": "23:00",
        "end_time": "02:00",
        "timezone": "UTC",
    }]}}
    # Saturday 23:30 UTC -> inside
    sat_night = datetime(2026, 5, 9, 23, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=sat_night) == "Saturday Overnight Maintenance"

    # Sunday 01:30 UTC (the tail of the Saturday window) -> inside
    sun_morning = datetime(2026, 5, 10, 1, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=sun_morning) == "Saturday Overnight Maintenance"

    # Sunday 02:30 UTC -> outside
    sun_late = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    assert is_in_maintenance(cfg, now=sun_late) is None


# ── App and Environment Targeting ────────────────────────────────────────────

def test_targeting_app_and_environment():
    cfg = {"alerts": {"maintenance_windows": [{
        "name": "Billing Staging Maintenance",
        "app": "billing-api",
        "environment": "staging",
        "start": "2026-12-23T00:00:00Z",
        "end": "2026-12-27T23:59:59Z",
    }]}}
    now = datetime(2026, 12, 25, 12, 0, tzinfo=timezone.utc)

    # Matches exact app and env
    assert is_in_maintenance(cfg, now=now, app="billing-api", environment="staging") == "Billing Staging Maintenance"

    # Does not match different app
    assert is_in_maintenance(cfg, now=now, app="auth-service", environment="staging") is None

    # Does not match different environment
    assert is_in_maintenance(cfg, now=now, app="billing-api", environment="production") is None

    # Global check without app does not match app-specific window
    assert is_in_maintenance(cfg, now=now, app=None, environment="staging") is None


# ── Database integration ─────────────────────────────────────────────────────

def test_db_maintenance_windows(db):
    win = db.create_maintenance_window(
        name="DB Weekly Window",
        window_type="weekly",
        days_of_week=["sunday"],
        start_time="02:00",
        duration_minutes=120,
        app="my-app",
        environment="prod",
    )
    assert win["id"] is not None
    assert win["name"] == "DB Weekly Window"

    now_inside = datetime(2026, 5, 10, 2, 30, tzinfo=timezone.utc)
    # Check matching
    assert is_in_maintenance(config={}, now=now_inside, app="my-app", environment="prod", db=db) == "DB Weekly Window"
    # Check non-matching app
    assert is_in_maintenance(config={}, now=now_inside, app="other-app", environment="prod", db=db) is None

    # Test disabled
    db.update_maintenance_window(win["id"], enabled=False)
    assert is_in_maintenance(config={}, now=now_inside, app="my-app", environment="prod", db=db) is None

