"""Tests for harness.auto_scheduler — cron parsing + job wiring."""
from harness.auto_scheduler import (
    build_scheduler, add_retention_job, is_enabled,
)


def _noop(app_def, environment):
    pass


def test_no_jobs_when_no_schedule_configured():
    config = {"environments": {"production": {"label": "Production"}}}
    apps = [{"app": "A", "environments": {"production": "https://a"}, "tests": []}]
    sched = build_scheduler(config, apps, _noop)
    assert sched.get_jobs() == []


def test_env_default_schedule_creates_job_per_app():
    config = {
        "environments": {
            "production": {"label": "Production", "schedule": "*/15 * * * *"},
        }
    }
    apps = [
        {"app": "A", "environments": {"production": "https://a"}, "tests": []},
        {"app": "B", "environments": {"production": "https://b"}, "tests": []},
    ]
    sched = build_scheduler(config, apps, _noop)
    job_ids = sorted(j.id for j in sched.get_jobs())
    assert job_ids == ["run::A::production", "run::B::production"]


def test_per_app_schedule_overrides_env_default():
    config = {
        "environments": {
            "production": {"label": "Production", "schedule": "0 * * * *"},
        }
    }
    apps = [{
        "app": "A",
        "schedule": "*/5 * * * *",
        "environments": {"production": "https://a"},
        "tests": [],
    }]
    sched = build_scheduler(config, apps, _noop)
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    # CronTrigger.from_crontab("*/5 * * * *") -> minute step of 5
    minute_field = next(f for f in jobs[0].trigger.fields if f.name == "minute")
    assert "*/5" in str(minute_field) or "/5" in str(minute_field)


def test_app_skipped_for_env_it_does_not_define():
    config = {
        "environments": {
            "production": {"schedule": "*/15 * * * *"},
            "staging":    {"schedule": "*/15 * * * *"},
        }
    }
    apps = [{
        "app": "ProdOnly",
        "environments": {"production": "https://a"},
        "tests": [],
    }]
    sched = build_scheduler(config, apps, _noop)
    job_ids = [j.id for j in sched.get_jobs()]
    assert job_ids == ["run::ProdOnly::production"]


def test_invalid_cron_is_skipped_not_raised():
    config = {
        "environments": {"production": {"schedule": "not-a-cron-expression"}},
    }
    apps = [{"app": "A", "environments": {"production": "https://a"}, "tests": []}]
    sched = build_scheduler(config, apps, _noop)
    assert sched.get_jobs() == []


def test_add_retention_job_registers_when_days_positive():
    config = {"environments": {"production": {"schedule": "*/15 * * * *"}}}
    apps = [{"app": "A", "environments": {"production": "https://a"}, "tests": []}]
    sched = build_scheduler(config, apps, _noop)
    add_retention_job(sched, retention_days=30, prune_callback=lambda: None)
    job_ids = [j.id for j in sched.get_jobs()]
    assert "retention::prune" in job_ids


def test_add_retention_job_noop_when_days_falsy():
    sched = build_scheduler({}, [], _noop)
    add_retention_job(sched, retention_days=0, prune_callback=lambda: None)
    add_retention_job(sched, retention_days=None, prune_callback=lambda: None)
    assert all(j.id != "retention::prune" for j in sched.get_jobs())


def test_is_enabled_default_true():
    assert is_enabled({}) is True
    assert is_enabled({"scheduling": {}}) is True


def test_is_enabled_explicit_false():
    assert is_enabled({"scheduling": {"enabled": False}}) is False
