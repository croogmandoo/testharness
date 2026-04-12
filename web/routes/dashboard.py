from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, environment: str = None):
    from web.main import get_db, get_config
    db = get_db()
    config = get_config()
    env = environment or config.get("default_environment", "production")
    envs = config.get("environments", {})
    summary = db.get_app_summary(env)
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "environment": env,
        "environments": envs,
    })


@router.get("/app/{app}/{environment}", response_class=HTMLResponse)
async def app_detail(request: Request, app: str, environment: str, run_id: str = None):
    from web.main import get_db, get_config
    import sqlite3
    db = get_db()
    config = get_config()

    # Get recent runs for this app+environment
    conn = sqlite3.connect(db.path)
    conn.row_factory = sqlite3.Row
    runs = conn.execute(
        "SELECT * FROM runs WHERE app=? AND environment=? ORDER BY started_at DESC LIMIT 10",
        (app, environment)
    ).fetchall()
    runs = [dict(r) for r in runs]

    selected_run = None
    test_results = []
    if runs:
        selected = next((r for r in runs if r["id"] == run_id), runs[0])
        selected_run = selected
        test_results = db.get_results_for_run(selected["id"])
        import json
        for tr in test_results:
            tr["step_log"] = json.loads(tr["step_log"] or "[]")
    conn.close()

    history = {}
    conn2 = sqlite3.connect(db.path)
    conn2.row_factory = sqlite3.Row
    for tr in test_results:
        rows = conn2.execute(
            "SELECT status FROM test_results WHERE app=? AND environment=? AND test_name=? "
            "ORDER BY finished_at DESC LIMIT 10",
            (app, environment, tr["test_name"])
        ).fetchall()
        history[tr["test_name"]] = [r["status"] for r in rows]
    conn2.close()

    return templates.TemplateResponse("detail.html", {
        "request": request,
        "app": app,
        "environment": environment,
        "environments": config.get("environments", {}),
        "runs": runs,
        "selected_run": selected_run,
        "test_results": test_results,
        "history": history,
    })
