from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Any, Optional
from harness.models import Run
from harness.runner import run_app
from harness.app_manager import get_known_vars
from web.auth import get_current_user, require_role

router = APIRouter(prefix="/api")


class RunRequest(BaseModel):
    app: Optional[str] = None
    environment: str
    triggered_by: str = "api"


@router.post("/runs", status_code=202)
async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks,
                      current_user: dict = Depends(require_role("admin", "runner"))):
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
async def get_run(run_id: str, current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = db.get_results_for_run(run_id)
    return {**run, "results": results}


@router.get("/apps")
async def list_apps(environment: str = "production",
                    current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    return db.get_app_summary(environment)


@router.get("/vars")
async def list_vars(current_user: dict = Depends(get_current_user)):
    """Return all $VAR names referenced in app YAML files. Never returns values."""
    from web.main import get_apps_dir
    return {"vars": get_known_vars(apps_dir=get_apps_dir())}


@router.get("/results/{app}/{environment}")
async def get_results(app: str, environment: str, limit: int = 20,
                      current_user: dict = Depends(get_current_user)):
    from web.main import get_db
    db = get_db()
    return db.get_results_for_app(app, environment, limit)


# ---- App management mutation endpoints ----


class AppDefRequest(BaseModel):
    app_def: dict[str, Any]


@router.post("/apps", status_code=201)
async def create_app_def(req: AppDefRequest,
                         current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.write_app(req.app_def, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": req.app_def.get("app", ""), "file": str(path)}


@router.put("/apps/{app_name}", status_code=200)
async def update_app_def(app_name: str, req: AppDefRequest,
                         current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    if req.app_def.get("app") and req.app_def["app"] != app_name:
        raise HTTPException(
            status_code=422,
            detail=f"app_def.app must match the URL app name '{app_name}'"
        )
    try:
        path = mgr.update_app(app_name, req.app_def, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}", status_code=200)
async def archive_app_def(app_name: str,
                          current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.archive_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"archived": str(path)}


@router.post("/apps/{app_name}/restore", status_code=200)
async def restore_app_def(app_name: str,
                          current_user: dict = Depends(require_role("admin", "runner"))):
    from web.main import get_apps_dir, reload_apps
    import harness.app_manager as mgr
    try:
        path = mgr.restore_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        reload_apps()
    except Exception:
        pass  # in-memory app list may be briefly stale; harmless for single-operator use
    return {"app": app_name, "file": str(path)}


@router.delete("/apps/{app_name}/permanent", status_code=204)
async def delete_app_permanently(app_name: str,
                                 current_user: dict = Depends(require_role("admin", "runner"))) -> None:
    from web.main import get_apps_dir
    import harness.app_manager as mgr
    try:
        mgr.delete_archived_app(app_name, apps_dir=get_apps_dir())
    except mgr.AppManagerError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
