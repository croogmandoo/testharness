"""Tests for harness.staleness — detect apps whose monitoring went silent."""
from datetime import datetime, timedelta, timezone

import pytest

from harness.db import Database
from harness.models import Run
from harness.staleness import find_stale_apps
from harness.types import AlertType


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "stale.db"))
    d.init_schema()
    return d


def _seed_run(db, app, env, finished_iso: str):
    run = Run(app=app, environment=env, triggered_by="ui")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at=finished_iso, finished_at=finished_iso)
    return run.id


def _now_iso(now: datetime) -> str:
    return now.isoformat()


# ── No alerts when everything is fresh ───────────────────────────────────────

def test_fresh_app_no_alert(db):
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(hours=1)).isoformat())
    apps = [{"app": "A", "environments": {"production": "https://a"}}]
    config = {"environments": {"production": {}}}
    # The fixture uses 'prod' but app declares 'production' — staleness only
    # checks env names that are BOTH in the app's `environments` AND the
    # global config.environments.  Adjust:
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}}}
    assert find_stale_apps(db, apps, config, now=now) == []


def test_app_with_no_runs_is_skipped(db):
    """An app that's never run shouldn't generate an alert."""
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    apps = [{"app": "Untouched", "environments": {"prod": "https://u"}}]
    config = {"environments": {"prod": {}}}
    assert find_stale_apps(db, apps, config, now=now) == []


# ── Alerts when stale ────────────────────────────────────────────────────────

def test_stale_app_generates_alert(db):
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    # 30h ago, threshold default 24h
    _seed_run(db, "A", "prod", (now - timedelta(hours=30)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}}}
    alerts = find_stale_apps(db, apps, config, now=now)
    assert len(alerts) == 1
    alert_type, app, env, summary, details = alerts[0]
    assert alert_type == AlertType.STALE
    assert app == "A"
    assert env == "prod"
    assert "30h" in summary and "24h" in summary
    assert "last run finished at" in details


def test_alert_dedup_within_realert_window(db):
    """A second call within the re-alert window must NOT alert again."""
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(hours=30)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}}}
    first = find_stale_apps(db, apps, config, now=now)
    assert len(first) == 1
    later = now + timedelta(minutes=30)
    second = find_stale_apps(db, apps, config, now=later)
    assert second == []  # still in the dedup window


def test_alert_re_emits_after_realert_window(db):
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(hours=30)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}}, "alerts": {"stale_realert_hours": 1}}
    find_stale_apps(db, apps, config, now=now)
    later = now + timedelta(hours=2)
    assert len(find_stale_apps(db, apps, config, now=later)) == 1


def test_clear_stale_alert_resets_dedup(db):
    """When a fresh run lands the runner clears the dedup; next staleness
    should alert again immediately."""
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(hours=30)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}}}
    assert len(find_stale_apps(db, apps, config, now=now)) == 1
    # ... fresh run lands, runner calls clear_stale_alert
    db.clear_stale_alert("A", "prod")
    later = now + timedelta(minutes=5)
    # ... but for some reason it goes stale again immediately
    assert len(find_stale_apps(db, apps, config, now=later)) == 1


# ── Configuration knobs ──────────────────────────────────────────────────────

def test_disabled_via_config(db):
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(hours=999)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {"environments": {"prod": {}},
              "alerts": {"stale_check_enabled": False}}
    assert find_stale_apps(db, apps, config, now=now) == []


def test_custom_threshold_minutes(db):
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "prod", (now - timedelta(minutes=10)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    # Threshold tightened to 5 min -> 10-min-old run is stale
    config = {"environments": {"prod": {}},
              "alerts": {"stale_threshold_minutes": 5}}
    alerts = find_stale_apps(db, apps, config, now=now)
    assert len(alerts) == 1


def test_skipped_during_maintenance_window(db):
    now = datetime(2026, 5, 9, 2, 30, tzinfo=timezone.utc)  # Sat 02:30 UTC
    _seed_run(db, "A", "prod", (now - timedelta(hours=999)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a"}}]
    config = {
        "environments": {"prod": {}},
        "alerts": {"maintenance_windows": [
            {"name": "Weekly", "cron": "0 2 * * 6", "duration_minutes": 120},
        ]},
    }
    assert find_stale_apps(db, apps, config, now=now) == []


# ── Scope ────────────────────────────────────────────────────────────────────

def test_only_checks_envs_in_global_config(db):
    """If the app declares envs the global config doesn't, those are ignored."""
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    _seed_run(db, "A", "qa", (now - timedelta(hours=30)).isoformat())
    apps = [{"app": "A", "environments": {"prod": "https://a", "qa": "https://q"}}]
    config = {"environments": {"prod": {}}}     # qa NOT declared globally
    # qa run is stale but qa isn't in global config -> no alert
    assert find_stale_apps(db, apps, config, now=now) == []
