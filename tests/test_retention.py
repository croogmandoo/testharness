"""Tests for harness.retention — prune old runs, results, screenshots."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from harness.db import Database
from harness.models import Run, TestResult
from harness.retention import prune_old_runs


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seed_run(db, app: str, env: str, finished_iso: str | None,
              screenshot: str | None = None) -> str:
    run = Run(app=app, environment=env, triggered_by="ui")
    db.insert_run(run)
    if finished_iso:
        db.update_run_status(run.id, "complete",
                             started_at=finished_iso, finished_at=finished_iso)
    tr = TestResult(
        run_id=run.id, app=app, environment=env, test_name="t",
        status="pass", screenshot=screenshot,
        finished_at=finished_iso,
    )
    db.insert_test_result(tr)
    return run.id


def test_prune_disabled_when_retention_days_none(db):
    counts = prune_old_runs(db, retention_days=None)
    assert counts == {"runs_deleted": 0, "results_deleted": 0, "screenshots_deleted": 0}


def test_prune_disabled_when_retention_days_zero(db):
    _seed_run(db, "a", "prod", _iso_days_ago(100))
    counts = prune_old_runs(db, retention_days=0)
    assert counts["runs_deleted"] == 0


def test_prune_deletes_runs_older_than_cutoff(db):
    old_id = _seed_run(db, "a", "prod", _iso_days_ago(100))
    new_id = _seed_run(db, "a", "prod", _iso_days_ago(5))
    counts = prune_old_runs(db, retention_days=30)
    assert counts["runs_deleted"] == 1
    assert counts["results_deleted"] == 1
    assert db.get_run(old_id) is None
    assert db.get_run(new_id) is not None


def test_prune_preserves_active_runs_with_no_finished_at(db):
    """An active run (no finished_at) must NOT be deleted regardless of age."""
    active_id = _seed_run(db, "a", "prod", finished_iso=None)
    counts = prune_old_runs(db, retention_days=1)
    assert counts["runs_deleted"] == 0
    assert db.get_run(active_id) is not None


def test_prune_removes_screenshot_files(db, tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    keep = shots / "keep.png"
    drop = shots / "drop.png"
    keep.write_bytes(b"\x89PNG keep")
    drop.write_bytes(b"\x89PNG drop")

    _seed_run(db, "a", "prod", _iso_days_ago(100), screenshot="drop.png")
    _seed_run(db, "a", "prod", _iso_days_ago(5),   screenshot="keep.png")

    counts = prune_old_runs(db, retention_days=30, screenshots_dir=str(shots))
    assert counts["screenshots_deleted"] == 1
    assert keep.exists()
    assert not drop.exists()


def test_prune_tolerates_missing_screenshot_files(db, tmp_path):
    """Screenshot file already deleted on disk -> prune still succeeds."""
    shots = tmp_path / "shots"
    shots.mkdir()
    _seed_run(db, "a", "prod", _iso_days_ago(100), screenshot="ghost.png")
    # File never written — prune should skip it cleanly
    counts = prune_old_runs(db, retention_days=30, screenshots_dir=str(shots))
    assert counts["runs_deleted"] == 1
    assert counts["screenshots_deleted"] == 0
