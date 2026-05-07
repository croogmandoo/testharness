"""Tests for the dashboard polish bundle: per-app run history, tag filter
chips, sparklines, and the empty-state onboarding card."""
import uuid
from datetime import datetime, timezone

import pytest
import yaml
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import AppState, Run, TestResult
from web.main import create_app
from web.auth import get_current_user


def _seed_admin(db):
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None,
        "password_hash": None,
        "role": "admin",
        "auth_provider": "local",
        "role_override": 0,
        "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "polish.db"))
    d.init_schema()
    return d


# ── DB: get_app_run_history ──────────────────────────────────────────────────

def _seed_run(db, app: str, env: str, finished_iso: str, statuses: list[str]):
    run = Run(app=app, environment=env, triggered_by="ui")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at=finished_iso, finished_at=finished_iso)
    for i, st in enumerate(statuses):
        db.insert_test_result(TestResult(
            run_id=run.id, app=app, environment=env,
            test_name=f"t{i}", status=st, finished_at=finished_iso,
        ))
    return run.id


def test_run_history_marks_run_pass_when_all_tests_pass(db):
    _seed_run(db, "A", "prod", "2026-05-01T10:00:00+00:00",
              statuses=["pass", "pass"])
    history = db.get_app_run_history("A", "prod")
    assert len(history) == 1
    assert history[0]["status"] == "pass"


def test_run_history_marks_run_fail_when_any_test_fails(db):
    _seed_run(db, "A", "prod", "2026-05-01T10:00:00+00:00",
              statuses=["pass", "fail"])
    history = db.get_app_run_history("A", "prod")
    assert history[0]["status"] == "fail"


def test_run_history_orders_newest_first(db):
    _seed_run(db, "A", "prod", "2026-05-01T10:00:00+00:00", ["pass"])
    _seed_run(db, "A", "prod", "2026-05-03T10:00:00+00:00", ["pass"])
    _seed_run(db, "A", "prod", "2026-05-02T10:00:00+00:00", ["pass"])
    history = db.get_app_run_history("A", "prod")
    finished = [r["finished_at"] for r in history]
    assert finished == sorted(finished, reverse=True)


def test_run_history_respects_limit(db):
    for i in range(10):
        _seed_run(db, "A", "prod",
                  f"2026-05-{i+1:02d}T10:00:00+00:00", ["pass"])
    history = db.get_app_run_history("A", "prod", limit=3)
    assert len(history) == 3


def test_run_history_isolates_by_app_and_env(db):
    _seed_run(db, "A", "prod", "2026-05-01T10:00:00+00:00", ["pass"])
    _seed_run(db, "B", "prod", "2026-05-01T10:00:00+00:00", ["pass"])
    _seed_run(db, "A", "stag", "2026-05-01T10:00:00+00:00", ["pass"])
    assert len(db.get_app_run_history("A", "prod")) == 1
    assert len(db.get_app_run_history("B", "prod")) == 1
    assert len(db.get_app_run_history("A", "stag")) == 1


def test_run_history_excludes_pending_and_running(db):
    """In-flight runs (status != 'complete') must NOT show on the sparkline."""
    pending = Run(app="A", environment="prod", triggered_by="ui")
    db.insert_run(pending)  # status='pending', no finished_at
    _seed_run(db, "A", "prod", "2026-05-01T10:00:00+00:00", ["pass"])
    history = db.get_app_run_history("A", "prod")
    assert len(history) == 1
    assert history[0]["status"] == "pass"


# ── Dashboard rendering ──────────────────────────────────────────────────────

@pytest.fixture
def client(db, tmp_path):
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "alpha.yaml").write_text(yaml.dump({
        "app": "Alpha", "tags": ["critical", "media"],
        "environments": {"production": "https://a"}, "tests": [],
    }))
    (apps_dir / "beta.yaml").write_text(yaml.dump({
        "app": "Beta", "tags": ["nightly"],
        "environments": {"production": "https://b"}, "tests": [],
    }))
    db.upsert_app_state(AppState(
        app="Alpha", environment="production", test_name="t",
        state="passing", since="2026-05-01T10:00:00+00:00",
    ))
    app = create_app(
        db=db,
        config={"default_environment": "production",
                "environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    return TestClient(app)


def test_dashboard_renders_tag_chips(client):
    with client:
        body = client.get("/").content
    assert b'class="tag-chips"' in body
    assert b'data-tag="critical"' in body
    assert b'data-tag="media"' in body
    assert b'data-tag="nightly"' in body


def test_dashboard_renders_sparkline_when_history_exists(client, db):
    _seed_run(db, "Alpha", "production", "2026-05-01T10:00:00+00:00", ["pass"])
    _seed_run(db, "Alpha", "production", "2026-05-02T10:00:00+00:00", ["fail"])
    with client:
        body = client.get("/").content
    assert b'class="tile-sparkline"' in body
    assert b'spark-dot--pass' in body
    assert b'spark-dot--fail' in body


def test_dashboard_no_sparkline_when_no_history(client):
    """An app with zero completed runs shouldn't render a sparkline at all."""
    with client:
        body = client.get("/").content
    # No history was seeded for Alpha or Beta.
    assert b'tile-sparkline' not in body


def test_dashboard_empty_state_shows_onboarding(db, tmp_path):
    """Dashboard with no apps configured shows the onboarding card."""
    admin = _seed_admin(db)
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app = create_app(
        db=db,
        config={"default_environment": "production",
                "environments": {"production": {"label": "Production"}}},
        apps_dir=str(apps_dir),
    )
    app.dependency_overrides[get_current_user] = lambda: admin
    with TestClient(app) as c:
        body = c.get("/").content
    assert b"empty-onboarding" in body
    assert b"No apps configured yet" in body
    # Admin should see the call-to-action button
    assert b"Create your first app" in body
