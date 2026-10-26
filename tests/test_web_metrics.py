"""Tests for /metrics Prometheus exposition endpoint."""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import AppState, Run, TestResult
from web.main import create_app


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "metrics.db"))
    d.init_schema()
    return d


def _seed_history(db):
    db.upsert_app_state(AppState(
        app="A", environment="production", test_name="login",
        state="passing", since="2026-05-01T10:00:00+00:00",
    ))
    db.upsert_app_state(AppState(
        app="A", environment="production", test_name="checkout",
        state="failing", since="2026-05-01T10:00:00+00:00",
    ))
    run = Run(app="A", environment="production", triggered_by="schedule")
    db.insert_run(run)
    db.update_run_status(
        run.id, "complete",
        started_at="2026-05-07T15:00:00+00:00",
        finished_at="2026-05-07T15:00:42+00:00",
    )
    db.insert_test_result(TestResult(
        run_id=run.id, app="A", environment="production", test_name="login",
        status="pass", duration_ms=1234,
        finished_at="2026-05-07T15:00:42+00:00",
    ))


def _client(db, config=None):
    cfg = config or {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
    }
    app = create_app(db=db, config=cfg, apps_dir="/nonexistent")
    return TestClient(app)


def test_metrics_endpoint_returns_prometheus_format(db):
    _seed_history(db)
    with _client(db) as c:
        r = c.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert "# HELP harness_test_status" in body
    assert "# TYPE harness_test_status gauge" in body


def test_metrics_includes_test_status_labels(db):
    _seed_history(db)
    with _client(db) as c:
        body = c.get("/metrics").text
    assert 'harness_test_status{app="A",environment="production",test="login"} 1' in body
    assert 'harness_test_status{app="A",environment="production",test="checkout"} 0' in body


def test_metrics_includes_last_duration(db):
    _seed_history(db)
    with _client(db) as c:
        body = c.get("/metrics").text
    assert "harness_test_last_duration_seconds" in body
    assert 'app="A",environment="production",test="login"} 1.234' in body


def test_metrics_runs_total_grouped_by_status(db):
    _seed_history(db)
    with _client(db) as c:
        body = c.get("/metrics").text
    assert "# TYPE harness_runs_total counter" in body
    assert 'status="complete"' in body


def test_metrics_disabled_returns_404(db):
    cfg = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "metrics": {"enabled": False},
    }
    with _client(db, cfg) as c:
        r = c.get("/metrics")
    assert r.status_code == 404


def test_metrics_bearer_token_required(db):
    cfg = {
        "default_environment": "production",
        "environments": {"production": {"label": "Production"}},
        "metrics": {"bearer_token": "shhh"},
    }
    with _client(db, cfg) as c:
        no_token = c.get("/metrics")
        wrong   = c.get("/metrics", headers={"Authorization": "Bearer nope"})
        right   = c.get("/metrics", headers={"Authorization": "Bearer shhh"})
    assert no_token.status_code == 401
    assert wrong.status_code   == 401
    assert right.status_code   == 200


def test_metrics_label_escaping_handles_quotes(db):
    """Test names containing special chars must be escaped per exposition spec."""
    db.upsert_app_state(AppState(
        app='weird"app',
        environment="production",
        test_name="t",
        state="passing",
        since="2026-05-01T10:00:00+00:00",
    ))
    with _client(db) as c:
        body = c.get("/metrics").text
    assert 'app="weird\\"app"' in body


def test_metrics_skipped_by_first_run_middleware(tmp_path):
    """No users -> dashboard 302s to /setup, but /metrics must stay reachable."""
    d = Database(str(tmp_path / "empty.db"))
    d.init_schema()
    cfg = {"environments": {"production": {"label": "Production"}}}
    app = create_app(db=d, config=cfg, apps_dir="/nonexistent")
    with TestClient(app) as c:
        r = c.get("/metrics")
    assert r.status_code == 200


def test_metrics_emits_runner_heartbeats(db):
    """When remote runners are registered, their heartbeat timestamp is emitted."""
    db.upsert_runner(name="remote-worker-1", tags=[], capabilities=[], version="1.0.0")
    with _client(db) as c:
        body = c.get("/metrics").text
    assert "harness_runner_last_seen_timestamp_seconds" in body
    assert 'runner="remote-worker-1"' in body
