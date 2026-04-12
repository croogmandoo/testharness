from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from harness.models import Run
from harness.runner import run_app

router = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    app: Optional[str] = None
    environment: str
    triggered_by: str = "api"


@router.post("/runs", status_code=202)
async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()
    all_apps = get_apps()

    target_apps = [a for a in all_apps if req.app is None or a["app"] == req.app]
    if req.app and not target_apps:
        # Check for an active run before returning 404 — the app may not be in the config
        # but could still be running (e.g. config reloaded after a run was queued).
        if db.is_run_active(req.app, req.environment):
            raise HTTPException(
                status_code=409,
                detail=f"A run for {req.app} ({req.environment}) is already in progress"
            )
        raise HTTPException(status_code=404, detail=f"App '{req.app}' not found")

    # Pre-insert Run records so callers get real IDs back immediately.
    # The background task updates status from 'pending' → 'running' → 'complete'.
    queued = []
    for app_def in target_apps:
        if db.is_run_active(app_def["app"], req.environment):
            if req.app:
                raise HTTPException(
                    status_code=409,
                    detail=f"A run for {app_def['app']} ({req.environment}) is already in progress"
                )
            continue  # skip already-running apps in "run all" mode
        run = Run(app=app_def["app"], environment=req.environment, triggered_by=req.triggered_by)
        db.insert_run(run)
        background_tasks.add_task(run_app, app_def, req.environment, req.triggered_by, db, config,
                                  run_id=run.id)
        queued.append(run.id)

    return {"run_id": queued[0] if queued else None, "run_ids": queued, "apps": [a["app"] for a in target_apps]}


@router.get("/runs/{run_id}")
async def get_run(run_id: str):
    from web.main import get_db
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = db.get_results_for_run(run_id)
    return {**run, "results": results}


@router.get("/apps")
async def list_apps(environment: str = "production"):
    from web.main import get_db
    db = get_db()
    return db.get_app_summary(environment)


@router.get("/results/{app}/{environment}")
async def get_results(app: str, environment: str, limit: int = 20):
    from web.main import get_db
    db = get_db()
    return db.get_results_for_app(app, environment, limit)
