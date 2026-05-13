"""Integration tests for the /api/runner/* endpoints."""
import hashlib
import secrets
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from web.main import create_app


def _seed_runner_user_and_key(db) -> tuple[dict, str]:
    """Create a runner-role user + an API key. Returns (user_dict, plaintext_key)."""
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": "worker-bot", "display_name": "Worker Bot",
        "email": None, "password_hash": None, "role": "runner",
        "auth_provider": "local", "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    plaintext = "hth_" + secrets.token_urlsafe(30)
    db.insert_api_key({
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "name": "worker key",
        "key_prefix": plaintext[4:12],
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
    })
    return db.get_user_by_id(uid), plaintext


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "runner.db"))
    d.init_schema()
    return d


@pytest.fixture
def client_and_key(db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    user, plaintext = _seed_runner_user_and_key(db)
    c = TestClient(app)
    return c, plaintext


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── /api/runner/register ────────────────────────────────────────────────────

def test_register_inserts_runner_row(client_and_key, db):
    c, key = client_and_key
    resp = c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "eu-worker-1",
        "tags": ["eu", "media"],
        "capabilities": ["api", "availability", "browser"],
        "version": "1.0.0",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "eu-worker-1"
    assert "server_time" in body

    row = db.get_runner_by_name("eu-worker-1")
    assert row is not None
    assert row["tags"] == ["eu", "media"]
    assert row["capabilities"] == ["api", "availability", "browser"]
    assert row["status"] == "online"


def test_register_is_idempotent(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": ["a"], "capabilities": [],
    })
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": ["a", "b"], "capabilities": ["browser"], "version": "2.0",
    })
    row = db.get_runner_by_name("w")
    assert row["tags"] == ["a", "b"]
    assert row["capabilities"] == ["browser"]
    assert row["version"] == "2.0"


def test_register_rejects_missing_auth(db, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir()
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    c = TestClient(app, follow_redirects=False)
    resp = c.post("/api/runner/register", json={"name": "x"})
    assert resp.status_code in (401, 403)


# ── /api/runner/poll ────────────────────────────────────────────────────────

def test_poll_returns_jobs_pinned_to_this_worker(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })

    from harness.models import Run
    r = Run(app="A", environment="prod", triggered_by="test", status="queued")
    db.insert_run(r)
    job_id = db.queue_runner_job(
        run_id=r.id,
        app_def_json='{"app":"A","tests":[]}',
        environment="prod",
        runner_name="w",
    )

    resp = c.post("/api/runner/poll", headers=_bearer(key),
                  json={"name": "w", "capacity": 5})
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == job_id
    assert jobs[0]["run_id"] == r.id
    assert jobs[0]["environment"] == "prod"
    assert jobs[0]["app_def"]["app"] == "A"


def test_poll_empty_when_no_matching_jobs(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })
    resp = c.post("/api/runner/poll", headers=_bearer(key),
                  json={"name": "w", "capacity": 5})
    assert resp.status_code == 200
    assert resp.json()["jobs"] == []


def test_poll_draining_runner_gets_no_jobs(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })
    db.set_runner_status("w", "draining")

    from harness.models import Run
    r = Run(app="A", environment="prod", triggered_by="test", status="queued")
    db.insert_run(r)
    db.queue_runner_job(run_id=r.id, app_def_json="{}",
                         environment="prod", runner_name="w")

    resp = c.post("/api/runner/poll", headers=_bearer(key),
                  json={"name": "w", "capacity": 5})
    assert resp.json()["jobs"] == []


def test_poll_unknown_runner_returns_404(client_and_key):
    c, key = client_and_key
    resp = c.post("/api/runner/poll", headers=_bearer(key),
                  json={"name": "ghost", "capacity": 1})
    assert resp.status_code == 404


# ── /api/runner/heartbeat ───────────────────────────────────────────────────

def test_heartbeat_updates_last_seen(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })
    before = db.get_runner_by_name("w")["last_seen_at"]
    resp = c.post("/api/runner/heartbeat", headers=_bearer(key),
                  json={"name": "w"})
    assert resp.status_code == 200
    after = db.get_runner_by_name("w")["last_seen_at"]
    assert after >= before


# ── /api/runner/results ─────────────────────────────────────────────────────

def test_results_writes_test_results_and_completes_run(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })

    from harness.models import Run
    r = Run(app="MyApp", environment="prod", triggered_by="test", status="queued")
    db.insert_run(r)
    job_id = db.queue_runner_job(
        run_id=r.id,
        app_def_json='{"app":"MyApp","tests":[]}',
        environment="prod",
        runner_name="w",
    )
    # Claim it (simulates poll)
    db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=1)

    resp = c.post("/api/runner/results", headers=_bearer(key), json={
        "job_id": job_id,
        "run_status": "complete",
        "results": [
            {
                "test_name": "Healthcheck",
                "status": "pass",
                "duration_ms": 142,
                "finished_at": "2026-05-13T10:00:00+00:00",
                "step_log": [],
            },
        ],
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "complete"

    # Run is complete, test_results landed, app_state updated, job marked complete
    run = db.get_run(r.id)
    assert run["status"] == "complete"
    trs = db.get_results_for_run(r.id)
    assert len(trs) == 1 and trs[0]["test_name"] == "Healthcheck"
    state = db.get_app_state("MyApp", "prod", "Healthcheck")
    assert state["state"] == "passing"
    job = db.get_runner_job(job_id)
    assert job["state"] == "complete"


def test_results_rejects_unclaimed_job(client_and_key, db):
    c, key = client_and_key
    c.post("/api/runner/register", headers=_bearer(key), json={
        "name": "w", "tags": [], "capabilities": [],
    })
    from harness.models import Run
    r = Run(app="A", environment="prod", triggered_by="test", status="queued")
    db.insert_run(r)
    job_id = db.queue_runner_job(run_id=r.id, app_def_json="{}",
                                  environment="prod", runner_name="w")
    # NOT claimed — should be rejected
    resp = c.post("/api/runner/results", headers=_bearer(key), json={
        "job_id": job_id,
        "run_status": "complete",
        "results": [],
    })
    assert resp.status_code == 409
