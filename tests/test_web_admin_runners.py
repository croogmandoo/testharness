"""Tests for the /admin/runners management UI + drain/resume/delete actions."""
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import Run, TestResult
from web.auth import get_current_user
from web.csrf import verify_csrf
from web.main import create_app


def _seed_admin(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_admin",
        "display_name": "Admin",
        "email": None, "password_hash": None,
        "role": "admin", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


def _seed_runner_user(db) -> dict:
    user = {
        "id": str(uuid.uuid4()),
        "username": "_runner",
        "display_name": "Runner",
        "email": None, "password_hash": None,
        "role": "runner", "auth_provider": "local",
        "role_override": 0, "is_active": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    }
    db.insert_user(user)
    return user


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "admin_runners.db"))
    d.init_schema()
    return d


def _make_client(db, user, tmp_path):
    apps_dir = tmp_path / "apps"; apps_dir.mkdir(exist_ok=True)
    app = create_app(db=db, config={}, apps_dir=str(apps_dir))
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[verify_csrf] = lambda: None
    return TestClient(app, follow_redirects=False)


def test_admin_runners_empty_state(db, tmp_path):
    admin = _seed_admin(db)
    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    # Tier 2 polish: the runners empty state moved from a flat <p class="empty">
    # to an .empty-state card with friendlier copy + iconography.
    assert b"No remote workers registered yet" in r.content
    assert b'class="empty-state"' in r.content


def test_admin_runners_lists_registered(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="eu-1", tags=["eu"],
                      capabilities=["api", "browser"], version="1.0")
    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    assert b"eu-1" in r.content
    assert b"eu" in r.content


def test_admin_runners_drain_sets_status(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/drain")
    assert r.status_code == 303
    assert db.get_runner_by_name("w")["status"] == "draining"


def test_admin_runners_resume_sets_online(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    db.set_runner_status("w", "draining")
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/resume")
    assert r.status_code == 303
    assert db.get_runner_by_name("w")["status"] == "online"


def test_admin_runners_delete_removes_row(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/w/delete")
    assert r.status_code == 303
    assert db.get_runner_by_name("w") is None


def test_admin_runners_action_logs_audit(db, tmp_path):
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    c = _make_client(db, admin, tmp_path)
    c.post("/admin/runners/w/drain")
    entries = db.list_audit_log(action="runner.drain")
    assert len(entries) == 1
    assert entries[0]["target"] == "w"


def test_admin_runners_404_on_missing_target(db, tmp_path):
    admin = _seed_admin(db)
    c = _make_client(db, admin, tmp_path)
    r = c.post("/admin/runners/ghost/drain")
    assert r.status_code == 404


def test_admin_runners_requires_admin_role(db, tmp_path):
    runner = _seed_runner_user(db)
    c = _make_client(db, runner, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 403


# ── workers-v2 surfaces (Task #8B) ──────────────────────────────────────────

def _seed_api_key(db, user_id: str, scope: list[str]) -> str:
    """Insert an api_keys row with the given scope and return its id."""
    plaintext = "hth_" + secrets.token_urlsafe(20)
    row = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "name": "worker key",
        "key_prefix": plaintext[4:12],
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
        "scope": json.dumps(scope),
    }
    db.insert_api_key(row)
    return row["id"]


def _seed_failed_job(db, *, app: str = "FailedApp", env: str = "prod",
                     retry_count: int = 3,
                     worker_name: str = "w",
                     error: str = "stuck job exceeded max_retries=3") -> str:
    """Insert a runs row and a runner_jobs row in state='failed' that the
    reaper panel will surface. Returns the job_id."""
    run = Run(app=app, environment=env, triggered_by="test", status="error")
    db.insert_run(run)
    job_id = db.queue_runner_job(
        run_id=run.id,
        app_def_json=json.dumps({"app": app, "tests": []}),
        environment=env,
        runner_name=worker_name,
    )
    completed = datetime.now(timezone.utc).isoformat()
    with db._connect() as conn:
        conn.execute(
            "UPDATE runner_jobs SET state='failed', assigned_to=?, "
            "assigned_at=?, completed_at=?, error=?, retry_count=? WHERE id=?",
            (worker_name, completed, completed, error, retry_count, job_id),
        )
        conn.execute(
            "UPDATE runs SET status='error', finished_at=? WHERE id=?",
            (completed, run.id),
        )
    return job_id


def test_admin_runners_shows_retries_24h_per_worker(db, tmp_path):
    """The workers table surfaces a 'Retries' column = sum of retry_count
    over runner_jobs assigned to that worker in the last 24h. A 30-day-old
    retry must NOT count toward the 24h window."""
    admin = _seed_admin(db)
    db.upsert_runner(name="busy", tags=[], capabilities=[])

    # Two jobs in the last 24h with retry_count 2 and 3 → total 5.
    for retries in (2, 3):
        run = Run(app="A", environment="prod", triggered_by="test", status="error")
        db.insert_run(run)
        jid = db.queue_runner_job(
            run_id=run.id, app_def_json='{"app":"A"}', environment="prod",
            runner_name="busy",
        )
        with db._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs SET assigned_to='busy', retry_count=? "
                "WHERE id=?",
                (retries, jid),
            )

    # One ancient job (older than 24h) with retry_count=99 → must be excluded.
    ancient = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    run = Run(app="A", environment="prod", triggered_by="test", status="error")
    db.insert_run(run)
    jid = db.queue_runner_job(
        run_id=run.id, app_def_json='{"app":"A"}', environment="prod",
        runner_name="busy",
    )
    with db._connect() as conn:
        conn.execute(
            "UPDATE runner_jobs SET assigned_to='busy', retry_count=99, "
            "queued_at=? WHERE id=?",
            (ancient, jid),
        )

    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    body = r.content.decode()
    assert "Retries" in body
    # The retries sum (5) should appear as a font-mono number in the row.
    # Use a structural check rather than a brittle string match.
    assert db.sum_retry_count_for_runner(
        "busy",
        since_iso=(datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(),
    ) == 5
    # Ancient retry-count of 99 is excluded.
    assert "99" not in body.split("Retries")[1].split("Last seen")[0]


def test_admin_runners_shows_key_scope_per_worker(db, tmp_path):
    """When a runner row carries api_key_id, the workers table renders the
    scope list of that key. Runners without an api_key_id render '—'."""
    admin = _seed_admin(db)

    # Worker A registered with a scoped key
    rid = _seed_runner_user(db)["id"]
    key_id = _seed_api_key(db, rid, scope=["worker:results", "worker:pull"])
    db.upsert_runner(name="scoped-w", tags=[], capabilities=[],
                      api_key_id=key_id)

    # Worker B has no api_key_id (legacy registration)
    db.upsert_runner(name="legacy-w", tags=[], capabilities=[])

    c = _make_client(db, admin, tmp_path)
    r = c.get("/admin/runners")
    assert r.status_code == 200
    body = r.content.decode()
    assert "Key scope" in body
    assert "worker:results" in body
    assert "worker:pull" in body
    # The legacy worker's "Key scope" cell should render an em-dash.
    # Cheap structural check: split on each worker's name to verify the
    # legacy row contains the em-dash glyph.
    legacy_chunk = body.split("legacy-w", 1)[1].split("</tr>", 1)[0]
    assert "—" in legacy_chunk


def test_admin_runners_shows_screenshot_stats_panel(db, tmp_path):
    """The 'Last 24h artifacts' panel aggregates screenshots uploaded by
    workers in the last 24h. File size is read from disk via os.path.getsize."""
    admin = _seed_admin(db)
    # Stage 1: create a worker + queue a job assigned to it
    db.upsert_runner(name="w", tags=[], capabilities=[])
    run = Run(app="MyApp", environment="prod", triggered_by="test",
              status="complete")
    db.insert_run(run)
    job_id = db.queue_runner_job(
        run_id=run.id, app_def_json='{"app":"MyApp"}', environment="prod",
        runner_name="w",
    )
    db.claim_runner_jobs(worker_name="w", worker_tags=[], capacity=1)

    # Stage 2: insert a test_results row with a screenshot_path and write
    # a stub image to disk so os.path.getsize returns a real number.
    screenshot_dir = tmp_path / "data" / "screenshots" / run.id
    screenshot_dir.mkdir(parents=True)
    screenshot_file = screenshot_dir / "step.png"
    screenshot_file.write_bytes(b"\x89PNG_fake_payload_42_bytes_for_size_test_x")
    expected_size = screenshot_file.stat().st_size

    tr = TestResult(
        run_id=run.id, app="MyApp", environment="prod",
        test_name="Healthcheck", status="pass",
        finished_at=datetime.now(timezone.utc).isoformat(),
    )
    db.insert_test_result(tr)
    db.set_test_result_screenshot_path(tr.id, f"/screenshots/{run.id}/step.png")

    # Hop into tmp_path so the route's relative ``data/screenshots/...``
    # resolution finds the file we just wrote.
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        c = _make_client(db, admin, tmp_path)
        r = c.get("/admin/runners")
    finally:
        os.chdir(cwd)

    assert r.status_code == 200
    body = r.content.decode()
    assert "Last 24h artifacts" in body
    assert "Screenshots" in body
    assert "Total bytes" in body
    assert str(expected_size) in body
    # Exactly 1 screenshot in the window.
    assert ">1<" in body or "> 1 <" in body or ">\n      1" in body \
        or '<div class="stat-value font-mono">1</div>' in body


def test_admin_runners_failed_panel_and_manual_requeue(db, tmp_path):
    """The 'Failed runs (reaper-exhausted)' panel surfaces runner_jobs in
    state='failed' with retry_count > 0. POSTing to the manual-requeue
    route flips the job back to 'queued', writes an audit log entry, and
    redirects."""
    admin = _seed_admin(db)
    job_id = _seed_failed_job(db, app="DeadCheck", env="prod", retry_count=3)

    c = _make_client(db, admin, tmp_path)

    # 1. Panel surfaces the failed job
    r = c.get("/admin/runners")
    assert r.status_code == 200
    body = r.content.decode()
    assert "Failed runs (reaper-exhausted)" in body
    assert "DeadCheck" in body
    assert f"/admin/runners/jobs/{job_id}/requeue" in body

    # 2. POST to the requeue route flips state and audits
    r = c.post(f"/admin/runners/jobs/{job_id}/requeue")
    assert r.status_code == 303
    job = db.get_runner_job(job_id)
    assert job["state"] == "queued"
    assert job["assigned_to"] is None
    assert job["error"] is None
    # retry_count is preserved (history)
    assert job["retry_count"] == 3
    # The Run flips back to queued so the dashboard reflects the re-attempt
    run = db.get_run(job["run_id"])
    assert run["status"] == "queued"

    entries = db.list_audit_log(action="runner.requeue")
    assert len(entries) == 1
    assert entries[0]["target"] == job_id


def test_admin_runners_manual_requeue_rejects_non_failed_job(db, tmp_path):
    """Refuse to requeue a job that isn't in state='failed' — we don't
    want to clobber a job that's still 'assigned' or already 'queued'."""
    admin = _seed_admin(db)
    db.upsert_runner(name="w", tags=[], capabilities=[])
    run = Run(app="A", environment="prod", triggered_by="test", status="running")
    db.insert_run(run)
    jid = db.queue_runner_job(
        run_id=run.id, app_def_json='{"app":"A"}', environment="prod",
        runner_name="w",
    )  # state='queued'

    c = _make_client(db, admin, tmp_path)
    r = c.post(f"/admin/runners/jobs/{jid}/requeue")
    assert r.status_code == 409


def test_admin_runners_manual_requeue_404_on_missing_job(db, tmp_path):
    admin = _seed_admin(db)
    c = _make_client(db, admin, tmp_path)
    r = c.post(f"/admin/runners/jobs/{uuid.uuid4()}/requeue")
    assert r.status_code == 404
