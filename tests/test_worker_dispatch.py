"""Tests for harness.worker_dispatch — parse_run_on, queue_remote_run, and
the runner_jobs queue lifecycle."""
import json
import pytest

from harness.db import Database
from harness.worker_dispatch import (
    parse_run_on, queue_remote_run, should_dispatch_remotely,
)


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    d.init_schema()
    return d


# ── parse_run_on ────────────────────────────────────────────────────────────

def test_parse_run_on_string_pins_by_name():
    name, tag = parse_run_on({"app": "X", "run_on": "eu-worker-1"})
    assert name == "eu-worker-1"
    assert tag is None


def test_parse_run_on_dict_tag_does_tag_dispatch():
    name, tag = parse_run_on({"app": "X", "run_on": {"tag": "media"}})
    assert name is None
    assert tag == "media"


def test_parse_run_on_dict_name_pins_by_name():
    name, tag = parse_run_on({"app": "X", "run_on": {"name": "eu-worker-1"}})
    assert name == "eu-worker-1"
    assert tag is None


def test_parse_run_on_absent_runs_locally():
    name, tag = parse_run_on({"app": "X"})
    assert name is None
    assert tag is None


def test_parse_run_on_malformed_falls_back_to_local():
    name, tag = parse_run_on({"app": "X", "run_on": 42})
    assert name is None and tag is None


def test_should_dispatch_remotely():
    assert should_dispatch_remotely({"run_on": "w"}) is True
    assert should_dispatch_remotely({"run_on": {"tag": "t"}}) is True
    assert should_dispatch_remotely({}) is False


# ── queue_remote_run ────────────────────────────────────────────────────────

def test_queue_remote_run_creates_run_and_job_rows(db):
    app_def = {
        "app": "MyApp",
        "url": "https://example.com",
        "environments": {"production": "https://example.com"},
        "run_on": "eu-worker-1",
        "tests": [{"name": "ping", "type": "availability"}],
    }
    run_id = queue_remote_run(db, app_def, "production", "api")
    run = db.get_run(run_id)
    assert run["app"] == "MyApp"
    assert run["status"] == "queued"

    # Job row exists
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runner_jobs WHERE run_id=?", (run_id,)
        ).fetchall()
    assert len(rows) == 1
    job = dict(rows[0])
    assert job["runner_name"] == "eu-worker-1"
    assert job["target_tag"] is None
    assert job["environment"] == "production"
    assert job["state"] == "queued"


def test_queue_remote_run_with_tag_dispatch(db):
    app_def = {
        "app": "MyApp", "url": "https://x",
        "environments": {"production": "https://x"},
        "run_on": {"tag": "media"},
        "tests": [],
    }
    run_id = queue_remote_run(db, app_def, "production", "api")
    with db._connect() as conn:
        job = dict(conn.execute(
            "SELECT * FROM runner_jobs WHERE run_id=?", (run_id,)
        ).fetchone())
    assert job["runner_name"] is None
    assert job["target_tag"] == "media"


def test_queue_remote_run_resolves_secrets_into_app_def(db, monkeypatch):
    """The app_def_json stored on the job must contain resolved values, not
    $VAR placeholders — the worker doesn't have the secrets store."""
    monkeypatch.setenv("AUTH_TOKEN", "deadbeef-secret")
    app_def = {
        "app": "MyApp", "url": "https://x",
        "environments": {"production": "https://x"},
        "run_on": "eu-worker-1",
        "tests": [{
            "name": "call",
            "type": "api",
            "endpoint": "/me",
            "headers": {"Authorization": "Bearer $AUTH_TOKEN"},
        }],
    }
    run_id = queue_remote_run(db, app_def, "production", "api")
    with db._connect() as conn:
        job = dict(conn.execute(
            "SELECT * FROM runner_jobs WHERE run_id=?", (run_id,)
        ).fetchone())
    payload = json.loads(job["app_def_json"])
    # The header value should now contain the resolved secret
    header = payload["tests"][0]["headers"]["Authorization"]
    assert header == "Bearer deadbeef-secret"


# ── claim_runner_jobs ───────────────────────────────────────────────────────

def test_claim_runner_jobs_pins_by_name(db):
    # Two jobs queued: one for eu-worker-1, one for us-worker
    db.upsert_runner(name="eu-worker-1", tags=[], capabilities=[])
    db.upsert_runner(name="us-worker", tags=[], capabilities=[])

    from harness.models import Run
    for runner in ("eu-worker-1", "us-worker"):
        r = Run(app="A", environment="p", triggered_by="test", status="queued")
        db.insert_run(r)
        db.queue_runner_job(
            run_id=r.id, app_def_json="{}", environment="p",
            runner_name=runner,
        )

    claimed = db.claim_runner_jobs(
        worker_name="eu-worker-1", worker_tags=[], capacity=10,
    )
    assert len(claimed) == 1
    assert claimed[0]["runner_name"] == "eu-worker-1"
    assert claimed[0]["state"] == "assigned"
    assert claimed[0]["assigned_to"] == "eu-worker-1"


def test_claim_runner_jobs_by_tag(db):
    db.upsert_runner(name="media-1", tags=["media", "eu"], capabilities=[])

    from harness.models import Run
    # One job tagged 'media' (any worker with that tag should claim),
    # one tagged 'nightly' (this worker doesn't have it)
    for tag in ("media", "nightly"):
        r = Run(app="A", environment="p", triggered_by="test", status="queued")
        db.insert_run(r)
        db.queue_runner_job(
            run_id=r.id, app_def_json="{}", environment="p",
            target_tag=tag,
        )

    claimed = db.claim_runner_jobs(
        worker_name="media-1", worker_tags=["media", "eu"], capacity=10,
    )
    assert len(claimed) == 1
    assert claimed[0]["target_tag"] == "media"


def test_claim_runner_jobs_respects_capacity(db):
    db.upsert_runner(name="w", tags=[], capabilities=[])
    from harness.models import Run
    for _ in range(5):
        r = Run(app="A", environment="p", triggered_by="test", status="queued")
        db.insert_run(r)
        db.queue_runner_job(run_id=r.id, app_def_json="{}",
                             environment="p", runner_name="w")
    claimed = db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=3)
    assert len(claimed) == 3
    # Remaining queued
    remaining = db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=10)
    assert len(remaining) == 2


def test_claim_runner_jobs_does_not_pick_already_assigned(db):
    db.upsert_runner(name="w", tags=[], capabilities=[])
    from harness.models import Run
    r = Run(app="A", environment="p", triggered_by="test", status="queued")
    db.insert_run(r)
    db.queue_runner_job(run_id=r.id, app_def_json="{}",
                         environment="p", runner_name="w")
    first = db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=10)
    again = db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=10)
    assert len(first) == 1
    assert len(again) == 0


def test_complete_runner_job_transitions_state(db):
    db.upsert_runner(name="w", tags=[], capabilities=[])
    from harness.models import Run
    r = Run(app="A", environment="p", triggered_by="test", status="queued")
    db.insert_run(r)
    job_id = db.queue_runner_job(run_id=r.id, app_def_json="{}",
                                  environment="p", runner_name="w")
    claimed = db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=1)
    assert claimed[0]["id"] == job_id
    after = db.complete_runner_job(job_id)
    assert after["state"] == "complete"
    assert after["completed_at"]
