"""Tests for /status — public read-only health page."""
import pytest
import yaml
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import AppState, Run
from web.main import create_app


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "status.db"))
    d.init_schema()
    return d


def _client(db, apps_dir, config=None):
    cfg = config or {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=cfg, apps_dir=str(apps_dir))
    return TestClient(app)


def test_status_page_is_public(db, tmp_path):
    """/status renders without auth and without a session cookie."""
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text(yaml.dump({
        "app": "MyApp",
        "environments": {"production": "https://x"},
        "tests": [],
    }))
    with _client(db, apps_dir) as c:
        r = c.get("/status")
    assert r.status_code == 200
    assert b"MyApp" in r.content
    assert b"<title>Status</title>" in r.content


def test_status_page_shows_passing_when_all_tests_pass(db, tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text(yaml.dump({
        "app": "MyApp",
        "environments": {"production": "https://x"},
        "tests": [],
    }))
    db.upsert_app_state(AppState(
        app="MyApp", environment="production", test_name="t",
        state="passing", since="2026-05-01T10:00:00+00:00",
    ))
    with _client(db, apps_dir) as c:
        body = c.get("/status").content
    assert b"All systems normal" in body
    assert b"beacon-pass" in body


def test_status_page_shows_failing_when_any_test_fails(db, tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text(yaml.dump({
        "app": "MyApp",
        "environments": {"production": "https://x"},
        "tests": [],
    }))
    db.upsert_app_state(AppState(
        app="MyApp", environment="production", test_name="t",
        state="failing", since="2026-05-01T10:00:00+00:00",
    ))
    with _client(db, apps_dir) as c:
        body = c.get("/status").content
    assert b"Issues detected" in body
    assert b"beacon-fail" in body


def test_status_page_does_not_leak_error_messages(db, tmp_path):
    """Sanity: error messages must NOT appear on the public page."""
    from harness.models import TestResult
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    (apps_dir / "a.yaml").write_text(yaml.dump({
        "app": "MyApp",
        "environments": {"production": "https://x"},
        "tests": [],
    }))
    db.upsert_app_state(AppState(
        app="MyApp", environment="production", test_name="t",
        state="failing", since="2026-05-01T10:00:00+00:00",
    ))
    run = Run(app="MyApp", environment="production", triggered_by="schedule")
    db.insert_run(run)
    db.insert_test_result(TestResult(
        run_id=run.id, app="MyApp", environment="production", test_name="t",
        status="fail", error_msg="VERY SECRET INTERNAL TRACE",
        finished_at="2026-05-07T15:00:00+00:00",
    ))
    with _client(db, apps_dir) as c:
        body = c.get("/status").text
    assert "VERY SECRET INTERNAL TRACE" not in body


def test_status_page_can_be_disabled(db, tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    cfg = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "status_page": {"enabled": False},
    }
    with _client(db, apps_dir, cfg) as c:
        r = c.get("/status")
    assert r.status_code == 404


def test_status_page_skipped_by_first_run_middleware(tmp_path):
    """Reachable even before any user has signed up (no /setup redirect)."""
    d = Database(str(tmp_path / "empty.db"))
    d.init_schema()
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    cfg = {"environments": {"production": {"label": "Production"}}}
    app = create_app(db=d, config=cfg, apps_dir=str(apps_dir))
    with TestClient(app) as c:
        r = c.get("/status")
    assert r.status_code == 200
