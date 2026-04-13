import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, environment: str = None):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()
    env = environment or config.get("default_environment", "production")
    envs = config.get("environments", {})
    summary = db.get_app_summary(env)

    # Ensure every configured app appears, even if it has never been run
    known = {row["app"] for row in summary}
    for app_def in get_apps():
        if app_def["app"] not in known:
            summary.append({
                "app": app_def["app"],
                "total": 0,
                "passing": 0,
                "failing": 0,
                "unknown": 0,
                "last_run": None,
                "last_run_id": None,
                "active_run_id": None,
            })

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "environment": env,
        "environments": envs,
    })


@router.get("/app/{app}/{environment}", response_class=HTMLResponse)
async def app_detail(request: Request, app: str, environment: str, run_id: str = None):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()

    runs = db.get_recent_runs(app, environment)

    selected_run = None
    test_results = []
    history = {}

    if runs:
        selected_run = next((r for r in runs if r["id"] == run_id), runs[0])
        test_results = db.get_results_for_run(selected_run["id"])
        for tr in test_results:
            tr["step_log"] = json.loads(tr["step_log"] or "[]")
        test_names = [tr["test_name"] for tr in test_results]
        history = db.get_run_history_batch(app, environment, test_names)

    is_live = bool(selected_run and selected_run["status"] in ("pending", "running"))
    pending_test_names = []
    if is_live:
        completed_names = {tr["test_name"] for tr in test_results}
        app_def = next((a for a in get_apps() if a["app"] == app), None)
        if app_def:
            pending_test_names = [
                t["name"] for t in app_def.get("tests", [])
                if t["name"] not in completed_names
            ]

    return templates.TemplateResponse(request, "detail.html", {
        "request": request,
        "app": app,
        "environment": environment,
        "environments": config.get("environments", {}),
        "runs": runs,
        "selected_run": selected_run,
        "test_results": test_results,
        "history": history,
        "is_live": is_live,
        "pending_test_names": pending_test_names,
    })
