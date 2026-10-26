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
