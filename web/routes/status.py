"""Public read-only status page.

Unauthenticated. Shows a per-environment grid of app health (pass / fail /
unknown counts and most recent run time). Intentionally exposes no error
details, no logs, no test names — just the aggregate up/down signal.

Disable by setting `status_page.enabled: false` in config.yaml.
"""
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from web.templating import make_templates

router = APIRouter()
templates = make_templates()


def _summarise(summary_rows: list) -> str:
    """Roll per-test pass/fail counts up to a single per-app status."""
    if any(r.get("failing", 0) > 0 for r in summary_rows):
        return "failing"
    if all(r.get("total", 0) == r.get("passing", 0) and r.get("total", 0) > 0
           for r in summary_rows):
        return "passing"
    return "unknown"


@router.get("/status", response_class=HTMLResponse, include_in_schema=False)
async def status_page(request: Request):
    from web.main import get_db, get_config, get_apps

    config = get_config() or {}
    if (config.get("status_page") or {}).get("enabled", True) is False:
        raise HTTPException(status_code=404)

    db = get_db()
    envs = config.get("environments", {}) or {}

    by_env = []
    for env_key, env_cfg in envs.items():
        rows = db.get_app_summary(env_key)
        # Ensure configured apps appear even with no run history
        known = {r["app"] for r in rows}
        for app_def in get_apps():
            if (env_key in (app_def.get("environments") or {})
                    and app_def["app"] not in known):
                rows.append({
                    "app": app_def["app"],
                    "total": 0, "passing": 0, "failing": 0, "unknown": 0,
                    "last_run": None,
                })
        for row in rows:
            row["overall"] = (
                "failing" if row.get("failing", 0) > 0
                else "passing" if row.get("passing", 0) > 0
                                  and row.get("failing", 0) == 0
                                  and row.get("total", 0) > 0
                else "unknown"
            )
            history = db.get_app_run_history(row["app"], env_key, limit=10)
            row["history"] = list(reversed(history))
            if history:
                passing_runs = sum(1 for h in history if h.get("status") == "pass")
                row["uptime_pct"] = round((passing_runs / len(history)) * 100.0, 1)
            else:
                row["uptime_pct"] = None
        by_env.append({
            "key": env_key,
            "label": (env_cfg or {}).get("label", env_key),
            "rows": sorted(rows, key=lambda r: r["app"].lower()),
            "overall": _summarise(rows),
        })

    return templates.TemplateResponse(request, "status.html", {
        "by_env": by_env,
    })
