import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from web.auth import get_current_user
from web.csrf import csrf_token_for
from web.templating import make_templates

router = APIRouter()
templates = make_templates()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, environment: str = None,
                    current_user: dict = Depends(get_current_user)):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()
    envs = config.get("environments", {})

    set_cookie = False
    if environment:
        env = environment
        set_cookie = True
    else:
        cached_env = request.cookies.get("selected_environment")
        if cached_env and (not envs or cached_env in envs):
            env = cached_env
        else:
            env = config.get("default_environment", "production")

    if envs and env not in envs:
        env = config.get("default_environment") or list(envs.keys())[0]

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

    # Merge tags from app YAML into summary rows + last-N run history (for the
    # tile sparkline).  Sparkline draws oldest-first left-to-right; DB returns
    # newest-first so we reverse here.
    tag_map = {a["app"]: a.get("tags", []) for a in get_apps()}
    for row in summary:
        row["tags"] = tag_map.get(row["app"], [])
        history = db.get_app_run_history(row["app"], env, limit=7)
        row["history"] = list(reversed(history))

    # Tag chips for the dashboard filter — union of all tags across visible apps,
    # sorted alphabetically for stable rendering.
    all_tags = sorted({t for row in summary for t in row["tags"]})

    resp = templates.TemplateResponse(request, "dashboard.html", {
        "summary": summary,
        "all_tags": all_tags,
        "environment": env,
        "environments": envs,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
    })
    if set_cookie:
        resp.set_cookie(
            key="selected_environment",
            value=env,
            max_age=30 * 86400,
            samesite="lax",
            httponly=False,
        )
    return resp


@router.get("/app/{app}/{environment}", response_class=HTMLResponse)
async def app_detail(request: Request, app: str, environment: str, run_id: str = None,
                     current_user: dict = Depends(get_current_user)):
    from web.main import get_db, get_config, get_apps
    db = get_db()
    config = get_config()

    runs = db.get_recent_runs(app, environment)

    selected_run = None
    test_results = []
    history = {}
    sparkline_data = {}

    if runs:
        selected_run = next((r for r in runs if r["id"] == run_id), runs[0])
        test_results = db.get_results_for_run(selected_run["id"])
        for tr in test_results:
            tr["step_log"] = json.loads(tr["step_log"] or "[]")
        test_names = [tr["test_name"] for tr in test_results]
        history = db.get_run_history_batch(app, environment, test_names)

        # Compute SVG sparkline point lists for each test
        for test_name, statuses in history.items():
            n = len(statuses)
            if n < 2:
                continue
            # statuses is most-recent-first; reverse to draw left=oldest, right=newest
            ordered = list(reversed(statuses))
            points = []
            for i, s in enumerate(ordered):
                x = round(i / (n - 1) * 76 + 2)
                y = 2 if s == "pass" else 18
                points.append({"x": x, "y": y, "status": s})
            sparkline_data[test_name] = points

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

    resp = templates.TemplateResponse(request, "detail.html", {
        "app": app,
        "environment": environment,
        "environments": config.get("environments", {}),
        "runs": runs,
        "selected_run": selected_run,
        "test_results": test_results,
        "history": history,
        "sparkline_data": sparkline_data,
        "is_live": is_live,
        "pending_test_names": pending_test_names,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
    })
    resp.set_cookie(
        key="selected_environment",
        value=environment,
        max_age=30 * 86400,
        samesite="lax",
        httponly=False,
    )
    return resp
