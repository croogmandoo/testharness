"""Security regression tests for /api/runner/* authorization (finding #1).

Job descriptors handed out by /poll and /jobs carry resolved secrets, so
the worker endpoints must only serve:

* API-key callers (no session cookies),
* whose user has role admin or runner,
* and whose key is the one the runner name is bound to.

/results must additionally only accept results for a job assigned to a
runner bound to the caller's key.
"""
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pytest
from fastapi.testclient import TestClient

from harness.db import Database
from harness.models import Run
from harness.worker_dispatch import queue_remote_run
from web.main import create_app, get_secrets_store

_SECRET = "hunter2-SUPER-SECRET"


# ── Fixtures / helpers ──────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "authz.db"))
    d.init_schema()
    return d


@pytest.fixture
def client(db, tmp_path):
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    # /results writes screenshots relative to cwd; keep them in tmp_path.
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        app = create_app(db=db, config={}, apps_dir=str(apps_dir))
        with TestClient(app, follow_redirects=False) as c:
            yield c
    finally:
        os.chdir(cwd)


def _user(db, username: str, role: str, password: str = "password123") -> str:
    uid = str(uuid.uuid4())
    db.insert_user({
        "id": uid, "username": username, "display_name": username,
        "email": None,
        "password_hash": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        "role": role, "auth_provider": "local", "role_override": 0,
        "is_active": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "last_login_at": None,
    })
    return uid


def _key(db, user_id: str, scope=("*",), *, expires_at=None) -> tuple[str, str]:
    """Insert an API key for ``user_id``. Returns (key_id, plaintext)."""
    plaintext = "hth_" + secrets.token_urlsafe(30)
    key_id = str(uuid.uuid4())
    db.insert_api_key({
        "id": key_id,
        "user_id": user_id,
        "name": "key",
        "key_prefix": plaintext[4:12],
        "key_hash": hashlib.sha256(plaintext.encode()).hexdigest(),
        "expires_at": expires_at,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_used_at": None,
        "is_active": 1,
        "scope": json.dumps(list(scope)),
    })
    return key_id, plaintext


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, key: str, name: str = "eu-worker-1"):
    return client.post("/api/runner/register", headers=_bearer(key),
                       json={"name": name, "tags": [], "capabilities": []})


def _queue_secret_job(db, runner_name: str = "eu-worker-1") -> str:
    """Queue a job pinned to ``runner_name`` whose app_def embeds a secret."""
    get_secrets_store().set("PROD_DB_PASSWORD", _SECRET)
    return queue_remote_run(
        db,
        {"app": "Billing", "url": "https://x", "run_on": runner_name,
         "tests": [{"name": "t", "type": "api", "endpoint": "/",
                    "headers": {"Authorization": "$PROD_DB_PASSWORD"}}]},
        "production", "schedule", secrets_store=get_secrets_store(),
    )


def _queue_assigned_job(db, runner_name: str) -> str:
    r = Run(app="A", environment="prod", triggered_by="test", status="queued")
    db.insert_run(r)
    job_id = db.queue_runner_job(
        run_id=r.id, app_def_json='{"app":"A","tests":[]}',
        environment="prod", runner_name=runner_name,
    )
    db.claim_runner_jobs(worker_name=runner_name, worker_tags=[], capacity=1)
    return job_id


def _results_payload(job_id: str) -> dict:
    return {
        "job_id": job_id, "run_status": "complete",
        "results": [{"test_name": "t", "status": "pass", "duration_ms": 1,
                     "finished_at": "2026-05-13T10:00:00+00:00", "step_log": []}],
    }


def _login(client, username: str, password: str = "password123") -> None:
    r = client.get("/auth/login")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    r = client.post("/auth/login", data={
        "username": username, "password": password, "csrf_token": tok,
    })
    assert r.status_code == 303, r.text


def _create_key_via_form(client, **data):
    r = client.get("/api-keys")
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1)
    return client.post("/api-keys", data={"name": "k", "csrf_token": tok, **data})


# ── PoC regression ──────────────────────────────────────────────────────────

def test_readonly_user_cannot_steal_secrets_via_fake_runner(client, db):
    """Original PoC: a read_only user mints a key from /api-keys, registers
    as the worker an admin's app is pinned to, polls, and reads the
    resolved secret out of the job descriptor."""
    _user(db, "admin", "admin")
    _queue_secret_job(db, "eu-worker-1")
    _user(db, "intern", "read_only")

    _login(client, "intern")
    r = _create_key_via_form(client)
    assert r.status_code == 303
    key = r.cookies.get("flash_new_key") or client.cookies.get("flash_new_key")
    assert key and key.startswith("hth_")
    intern = db.get_user_by_username("intern")
    (row,) = db.list_api_keys_for_user(intern["id"])
    assert json.loads(row["scope"]) == ["read"]

    worker = TestClient(client.app)
    h = _bearer(key)
    assert worker.post("/api/runner/register", headers=h,
                       json={"name": "eu-worker-1"}).status_code == 403
    r = worker.post("/api/runner/poll", headers=h,
                    json={"name": "eu-worker-1", "capacity": 4})
    assert r.status_code in (403, 404)
    assert _SECRET not in r.text


def test_readonly_user_cannot_request_wildcard_scope(client, db):
    _user(db, "intern", "read_only")
    _login(client, "intern")
    for scope in ("*", "worker:pull", "worker:results", "admin"):
        r = _create_key_via_form(client, scope=scope)
        assert r.status_code == 403, scope
    intern = db.get_user_by_username("intern")
    assert db.list_api_keys_for_user(intern["id"]) == []


# ── Role and auth-method gate ───────────────────────────────────────────────

def test_session_cookie_rejected_on_register(client, db):
    _user(db, "ops", "admin")
    _login(client, "ops")
    r = client.post("/api/runner/register",
                    json={"name": "w", "tags": [], "capabilities": []})
    assert r.status_code == 401
    assert db.get_runner_by_name("w") is None


@pytest.mark.parametrize("role", ["read_only", "reporting"])
def test_legacy_wildcard_key_of_non_worker_role_rejected(client, db, role):
    """A pre-existing ``*`` key doesn't let a non-worker role act as a runner."""
    uid = _user(db, "intern", role)
    _, key = _key(db, uid, scope=["*"])
    _queue_secret_job(db)
    # Seed the runner row so poll/jobs can't short-circuit on 404.
    admin_uid = _user(db, "ops", "admin")
    _, admin_key = _key(db, admin_uid)
    assert _register(client, admin_key).status_code == 200

    assert _register(client, key).status_code == 403
    r = client.post("/api/runner/poll", headers=_bearer(key),
                    json={"name": "eu-worker-1", "capacity": 4})
    assert r.status_code == 403
    assert _SECRET not in r.text
    r = client.get("/api/runner/jobs", headers=_bearer(key),
                   params={"name": "eu-worker-1", "wait": 0})
    assert r.status_code == 403
    assert _SECRET not in r.text
    r = client.post("/api/runner/heartbeat", headers=_bearer(key),
                    json={"name": "eu-worker-1"})
    assert r.status_code == 403


# ── Runner name ↔ API key binding ───────────────────────────────────────────

def test_other_users_key_cannot_hijack_bound_runner(client, db):
    a_uid = _user(db, "svc-a", "runner")
    a_key_id, a_key = _key(db, a_uid)
    b_uid = _user(db, "svc-b", "runner")
    _, b_key = _key(db, b_uid)
    assert _register(client, a_key).status_code == 200
    _queue_secret_job(db)

    r = _register(client, b_key)
    assert r.status_code == 409
    assert "bound to another API key" in r.json()["detail"]
    assert db.get_runner_by_name("eu-worker-1")["api_key_id"] == a_key_id

    responses = [
        client.post("/api/runner/poll", headers=_bearer(b_key),
                    json={"name": "eu-worker-1", "capacity": 4}),
        client.get("/api/runner/jobs", headers=_bearer(b_key),
                   params={"name": "eu-worker-1", "wait": 0}),
        client.post("/api/runner/heartbeat", headers=_bearer(b_key),
                    json={"name": "eu-worker-1", "health": "unhealthy"}),
    ]
    for r in responses:
        assert r.status_code == 403, r.text
        assert "re-register" in r.json()["detail"]
        assert _SECRET not in r.text
    # The rejected heartbeat must not have drained A's runner.
    assert db.get_runner_by_name("eu-worker-1")["status"] == "online"

    # A still gets its job, secret included.
    jobs = client.post("/api/runner/poll", headers=_bearer(a_key),
                       json={"name": "eu-worker-1", "capacity": 4}).json()["jobs"]
    assert len(jobs) == 1 and _SECRET in json.dumps(jobs)

    # B can't submit results for A's job.
    r = client.post("/api/runner/results", headers=_bearer(b_key),
                    json=_results_payload(jobs[0]["job_id"]))
    assert r.status_code == 403
    assert db.get_runner_job(jobs[0]["job_id"])["state"] == "assigned"


def test_results_for_job_assigned_to_another_runner_forbidden(client, db):
    """Even with its own registered runner, a key can only report on jobs
    assigned to a runner bound to it."""
    a_uid = _user(db, "svc-a", "runner")
    _, a_key = _key(db, a_uid)
    b_uid = _user(db, "svc-b", "runner")
    _, b_key = _key(db, b_uid)
    assert _register(client, a_key, "w-a").status_code == 200
    assert _register(client, b_key, "w-b").status_code == 200
    job_id = _queue_assigned_job(db, "w-a")

    r = client.post("/api/runner/results", headers=_bearer(b_key),
                    json=_results_payload(job_id))
    assert r.status_code == 403

    r = client.post("/api/runner/results", headers=_bearer(a_key),
                    json=_results_payload(job_id))
    assert r.status_code == 200, r.text


def test_same_user_key_rotation_rebinds(client, db):
    uid = _user(db, "svc", "runner")
    old_id, old_key = _key(db, uid)
    new_id, new_key = _key(db, uid)
    assert _register(client, old_key).status_code == 200

    assert _register(client, new_key).status_code == 200
    assert db.get_runner_by_name("eu-worker-1")["api_key_id"] == new_id
    audit = db.list_audit_log(action="runner.rebind")
    assert len(audit) == 1
    assert old_id in audit[0]["details"] and new_id in audit[0]["details"]

    # The old key no longer drives the runner.
    r = client.post("/api/runner/poll", headers=_bearer(old_key),
                    json={"name": "eu-worker-1"})
    assert r.status_code == 403
    r = client.post("/api/runner/poll", headers=_bearer(new_key),
                    json={"name": "eu-worker-1"})
    assert r.status_code == 200


@pytest.mark.parametrize("how", ["revoked", "expired", "deleted"])
def test_rebind_allowed_once_bound_key_is_dead(client, db, how):
    a_uid = _user(db, "svc-a", "runner")
    a_key_id, a_key = _key(db, a_uid)
    b_uid = _user(db, "svc-b", "runner")
    b_key_id, b_key = _key(db, b_uid)
    assert _register(client, a_key).status_code == 200

    if how == "revoked":
        db.revoke_api_key(a_key_id)
    elif how == "expired":
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with db._connect() as conn:
            conn.execute("UPDATE api_keys SET expires_at=? WHERE id=?",
                         (past, a_key_id))
    else:
        with db._connect() as conn:
            conn.execute("DELETE FROM api_keys WHERE id=?", (a_key_id,))

    assert _register(client, b_key).status_code == 200
    assert db.get_runner_by_name("eu-worker-1")["api_key_id"] == b_key_id
    assert db.list_audit_log(action="runner.rebind")


def test_unbound_runner_must_reregister(client, db):
    """Rows with no key (pre-binding, or created out of band) can't be polled
    until a worker registers and claims them."""
    db.upsert_runner(name="legacy", tags=[], capabilities=[])
    uid = _user(db, "svc", "runner")
    key_id, key = _key(db, uid)

    r = client.post("/api/runner/poll", headers=_bearer(key), json={"name": "legacy"})
    assert r.status_code == 403
    assert "re-register" in r.json()["detail"]

    assert _register(client, key, "legacy").status_code == 200
    assert db.get_runner_by_name("legacy")["api_key_id"] == key_id
    r = client.post("/api/runner/poll", headers=_bearer(key), json={"name": "legacy"})
    assert r.status_code == 200


def test_admin_delete_allows_rebind(client, db):
    a_uid = _user(db, "svc-a", "runner")
    _, a_key = _key(db, a_uid)
    b_uid = _user(db, "svc-b", "runner")
    b_key_id, b_key = _key(db, b_uid)
    assert _register(client, a_key).status_code == 200
    assert _register(client, b_key).status_code == 409

    db.delete_runner("eu-worker-1")
    assert _register(client, b_key).status_code == 200
    assert db.get_runner_by_name("eu-worker-1")["api_key_id"] == b_key_id


def test_delete_requeues_assigned_jobs_so_new_owner_cannot_report(client, db):
    """Jobs A claimed go back to the queue when an admin deletes the runner,
    so B (re-registering the name) can't post results for them."""
    a_uid = _user(db, "svc-a", "runner")
    _, a_key = _key(db, a_uid)
    b_uid = _user(db, "svc-b", "runner")
    _, b_key = _key(db, b_uid)
    assert _register(client, a_key).status_code == 200
    job_id = _queue_assigned_job(db, "eu-worker-1")

    assert db.delete_runner("eu-worker-1") == 1
    job = db.get_runner_job(job_id)
    assert job["state"] == "queued" and job["assigned_to"] is None

    assert _register(client, b_key).status_code == 200
    r = client.post("/api/runner/results", headers=_bearer(b_key),
                    json=_results_payload(job_id))
    assert r.status_code in (403, 409)
    assert db.get_runner_job(job_id)["state"] == "queued"


# ── Races ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("how", ["revoked", "rebound"])
def test_long_poll_rechecks_key_before_claiming(client, db, monkeypatch, how):
    """Key revoked (or runner rebound) after the up-front check but before
    the claim: the long-poll must refuse instead of handing out the job."""
    uid = _user(db, "svc", "runner")
    key_id, key = _key(db, uid)
    assert _register(client, key).status_code == 200
    _queue_secret_job(db)

    # touch_runner_heartbeat runs between the binding check and the claim
    # loop — use it as the hook to change state mid-request.
    real_touch = db.touch_runner_heartbeat

    def touch_then_invalidate(name, ts):
        real_touch(name, ts)
        if how == "revoked":
            db.revoke_api_key(key_id)
        else:
            other_id, _ = _key(db, _user(db, "svc-b", "runner"))
            with db._connect() as conn:
                conn.execute("UPDATE runners SET api_key_id=? WHERE name=?",
                             (other_id, name))

    monkeypatch.setattr(db, "touch_runner_heartbeat", touch_then_invalidate)
    r = client.get("/api/runner/jobs", headers=_bearer(key),
                   params={"name": "eu-worker-1", "wait": 0})
    assert r.status_code == 403
    assert _SECRET not in r.text
    with db._connect() as conn:
        (state,) = conn.execute("SELECT state FROM runner_jobs").fetchone()
    assert state == "queued"


def test_register_conflict_when_binding_changes_after_check(db):
    """The conditional write refuses if another process bound the row
    between the route's check and the upsert."""
    from harness.db._runners import RunnerBindConflict
    db.upsert_runner(name="w", tags=[], capabilities=[], api_key_id="key-a")
    with pytest.raises(RunnerBindConflict):
        db.upsert_runner(name="w", tags=[], capabilities=[],
                         api_key_id="key-b", expected_api_key_id=None)
    assert db.get_runner_by_name("w")["api_key_id"] == "key-a"
    # Expected binding still in place → write goes through.
    db.upsert_runner(name="w", tags=[], capabilities=[],
                     api_key_id="key-b", expected_api_key_id="key-a")
    assert db.get_runner_by_name("w")["api_key_id"] == "key-b"


def test_register_conflict_when_insert_loses_race(db, monkeypatch):
    """Two registers both see no row; the second INSERT hits UNIQUE(name)."""
    from harness.db._runners import RunnerBindConflict
    db.upsert_runner(name="w", tags=[], capabilities=[], api_key_id="key-a")
    monkeypatch.setattr(db, "get_runner_by_name", lambda name: None)
    with pytest.raises(RunnerBindConflict):
        db.upsert_runner(name="w", tags=[], capabilities=[],
                         api_key_id="key-b", expected_api_key_id=None)


def test_register_route_maps_bind_race_to_409(client, db, monkeypatch):
    uid = _user(db, "svc", "runner")
    _, key = _key(db, uid)
    from harness.db._runners import RunnerBindConflict

    def lose_race(**kwargs):
        raise RunnerBindConflict(kwargs["name"])

    monkeypatch.setattr(db, "upsert_runner", lose_race)
    r = _register(client, key)
    assert r.status_code == 409
