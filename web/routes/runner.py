"""Worker (edge runner) protocol — registration, job polling, result submission,
heartbeat. Workers authenticate with an existing API key (Bearer hth_…) so we
reuse the user/role/audit infrastructure without inventing a new credential
type. CSRF doesn't apply (Bearer-only, /api/* exempt).

See ``docs/superpowers/plans/2026-05-14-remote-worker.md`` for the design.
"""
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from web.auth import get_current_user

router = APIRouter(prefix="/api/runner")
_log = logging.getLogger(__name__)


# ── Schemas ─────────────────────────────────────────────────────────────────

class RunnerRegisterRequest(BaseModel):
    name: str
    tags: list[str] = []
    capabilities: list[str] = []   # e.g. ["api", "availability", "browser"]
    version: Optional[str] = None


class RunnerRegisterResponse(BaseModel):
    name: str
    poll_interval_seconds: int
    capacity: int
    server_time: str
    config_version: str


class RunnerPollRequest(BaseModel):
    name: str
    capacity: int = 1


class RunnerStepLog(BaseModel):
    step: str
    status: str
    duration_ms: int = 0
    error: Optional[str] = None
    screenshot: Optional[str] = None


class RunnerTestResult(BaseModel):
    test_name: str
    status: str                    # "pass" | "fail" | "error"
    error_msg: Optional[str] = None
    duration_ms: Optional[int] = None
    finished_at: Optional[str] = None
    step_log: list[RunnerStepLog] = []
    screenshot: Optional[str] = None


class RunnerResultsRequest(BaseModel):
    job_id: str
    run_status: str = "complete"   # "complete" | "error"
    error: Optional[str] = None
    results: list[RunnerTestResult]


class RunnerHeartbeatRequest(BaseModel):
    name: str


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/register", response_model=RunnerRegisterResponse)
async def runner_register(
    req: RunnerRegisterRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Worker announces itself on startup. Idempotent: re-registering with
    the same name updates tags/capabilities/version and resets last_seen_at.
    """
    from web.main import get_db
    db = get_db()
    if not req.name.strip():
        raise HTTPException(status_code=422, detail="name must be non-empty")

    # The current_user came from Bearer auth; pull the api_key_id by hash for audit.
    api_key_id = _api_key_id_for_request(db, request)

    runner = db.upsert_runner(
        name=req.name.strip(),
        tags=req.tags,
        capabilities=req.capabilities,
        version=req.version,
        api_key_id=api_key_id,
    )
    db.log_audit(
        "runner.register",
        user_id=current_user["id"],
        username=current_user.get("username"),
        target=runner["name"],
        details=f"tags={runner['tags']} caps={runner['capabilities']} v={runner.get('version') or '-'}",
    )
    return RunnerRegisterResponse(
        name=runner["name"],
        poll_interval_seconds=10,
        capacity=4,
        server_time=datetime.now(timezone.utc).isoformat(),
        config_version="1",
    )


@router.post("/poll")
async def runner_poll(
    req: RunnerPollRequest,
    current_user: dict = Depends(get_current_user),
):
    """Worker asks for next jobs. Returns 0..capacity job descriptors,
    immediately (short-poll v1). Updates the worker's last_seen_at."""
    from web.main import get_db
    db = get_db()
    runner = db.get_runner_by_name(req.name)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"runner {req.name!r} not registered")
    if runner.get("status") == "draining":
        # Draining workers receive no new jobs, but we still touch heartbeat
        db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
        return {"jobs": []}

    db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
    claimed = db.claim_runner_jobs(
        worker_name=req.name,
        worker_tags=runner.get("tags") or [],
        capacity=max(1, min(int(req.capacity), 16)),
    )
    jobs = []
    for j in claimed:
        try:
            app_def = json.loads(j["app_def_json"])
        except Exception:
            app_def = {}
        jobs.append({
            "job_id": j["id"],
            "run_id": j["run_id"],
            "environment": j["environment"],
            "app_def": app_def,
        })
    return {"jobs": jobs}


@router.post("/results")
async def runner_results(
    req: RunnerResultsRequest,
    current_user: dict = Depends(get_current_user),
):
    """Worker submits completed test results for a job it owns."""
    from web.main import get_db, get_config
    from harness.worker_dispatch import ingest_runner_results
    db = get_db()
    job = db.get_runner_job(req.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job: {req.job_id}")
    if job["state"] != "assigned":
        raise HTTPException(
            status_code=409,
            detail=f"job {req.job_id} is in state {job['state']!r}, expected 'assigned'",
        )
    # The worker that claimed it must be the one submitting (sanity check)
    # We don't enforce hard caller-name binding in v1 — admins reassign by
    # forcing state changes in the DB if needed.
    try:
        summary = await ingest_runner_results(
            db,
            get_config() or {},
            job_id=req.job_id,
            run_status=req.run_status,
            test_results=[r.model_dump() for r in req.results],
            error=req.error,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **summary}


@router.post("/heartbeat")
async def runner_heartbeat(
    req: RunnerHeartbeatRequest,
    current_user: dict = Depends(get_current_user),
):
    """Liveness ping. Workers call this periodically (~30s) between polls
    so the /admin/runners status display stays accurate even if the worker
    is idle (no queued jobs)."""
    from web.main import get_db
    db = get_db()
    if db.get_runner_by_name(req.name) is None:
        raise HTTPException(status_code=404, detail=f"runner {req.name!r} not registered")
    db.touch_runner_heartbeat(req.name, datetime.now(timezone.utc).isoformat())
    return {"ok": True, "server_time": datetime.now(timezone.utc).isoformat()}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _api_key_id_for_request(db, request: Request) -> Optional[str]:
    """Best-effort: pull the api_keys.id used by this Bearer token, for audit
    linkage. Returns None if the auth was session-based (admins testing) or
    the key can't be looked up."""
    import hashlib
    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer hth_"):
        token = auth[len("Bearer "):]
    elif request.headers.get("X-API-Key", "").startswith("hth_"):
        token = request.headers["X-API-Key"]
    if not token:
        return None
    prefix = token[4:12]
    key_hash = hashlib.sha256(token.encode()).hexdigest()
    for candidate in db.get_api_key_by_prefix(prefix):
        if candidate["key_hash"] == key_hash:
            return candidate["id"]
    return None
