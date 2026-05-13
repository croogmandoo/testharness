"""Admin home page — consolidates the individual admin tools into one card
grid, and exposes a read-only view of the loaded configuration.

The individual /admin/* and /users, /api-keys, /secrets routes still work
and remain in the codebase — this is just a discoverability layer and an
audit-friendly home base for an admin user.
"""
import os
import re
from datetime import datetime, timezone, timedelta

import yaml
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from web.auth import require_role
from web.csrf import csrf_token_for, verify_csrf

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
        **extra,
    }


# ── /admin (the card grid) ──────────────────────────────────────────────────

def _gather_card_stats(db, config: dict) -> dict:
    """Compute the headline stats shown on each admin card.

    Each is a single cheap query — the card grid renders in well under
    100ms even with thousands of users/runs/audit entries.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    soon = (now + timedelta(days=30)).isoformat()

    stats: dict = {}

    # Users
    users = db.list_users()
    stats["users_active"] = sum(1 for u in users if u.get("is_active"))
    stats["users_disabled"] = len(users) - stats["users_active"]

    # Workers
    runners = db.list_runners()
    online = 0
    draining = 0
    offline = 0
    threshold = now - timedelta(seconds=90)
    for r in runners:
        if r.get("status") == "draining":
            draining += 1
            continue
        last_seen = r.get("last_seen_at")
        if last_seen:
            try:
                ts = datetime.fromisoformat(last_seen.rstrip("Z"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts > threshold:
                    online += 1
                    continue
            except ValueError:
                pass
        offline += 1
    stats["workers_online"] = online
    stats["workers_draining"] = draining
    stats["workers_offline"] = offline
    stats["workers_total"] = len(runners)

    # Audit
    with db._connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE timestamp >= ?",
            (today_start,),
        ).fetchone()[0]
    stats["audit_total"] = total
    stats["audit_today"] = today

    # Last DB backup (from the audit log)
    with db._connect() as conn:
        row = conn.execute(
            "SELECT timestamp FROM audit_log WHERE action='db.backup' "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
    stats["last_backup_at"] = row["timestamp"] if row else None

    # API keys
    api_keys = db.list_all_api_keys()
    stats["api_keys_active"] = sum(1 for k in api_keys if k.get("is_active"))
    stats["api_keys_expiring_soon"] = sum(
        1 for k in api_keys
        if k.get("is_active") and k.get("expires_at")
        and k["expires_at"] < soon
    )
    stats["api_keys_total"] = len(api_keys)

    # Secrets
    stats["secrets_count"] = len(db.list_secrets())

    # LDAP
    ldap_cfg = (config.get("auth") or {}).get("ldap") or {}
    stats["ldap_enabled"] = bool(ldap_cfg.get("enabled"))
    stats["ldap_server"] = ldap_cfg.get("server", "")

    # CA Certs
    stats["ca_certs_count"] = len(db.list_ca_certs())

    # Maintenance windows
    from harness.maintenance import is_in_maintenance
    active_window = is_in_maintenance(config)
    stats["maintenance_active"] = active_window
    stats["maintenance_windows_defined"] = len(
        (config.get("alerts") or {}).get("maintenance_windows") or []
    )

    # Analytics export
    stats["analytics_enabled"] = bool((config.get("analytics") or {}).get("enabled"))

    return stats


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request,
                    current_user: dict = Depends(require_role("admin"))):
    from web.main import get_db, get_config
    db = get_db()
    config = get_config()
    stats = _gather_card_stats(db, config)
    return templates.TemplateResponse(
        request, "admin_dashboard.html",
        _ctx(request, current_user, stats=stats),
    )


# ── /admin/config (read-only viewer) ────────────────────────────────────────

# Keys whose values are redacted regardless of nesting depth. We redact by
# *key name* because the resolver substitutes secrets in place, so a webhook
# URL on disk says "$TEAMS_WEBHOOK_URL" but in-memory it's the live URL.
_REDACT_KEY_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        r"password",
        r"secret",
        r"token",
        r"api_?key",
        r"webhook_url",
        r"connection_string",
        r"sas_token",
        r"client_secret",
        r"signing_key",
        r"smtp_username",
        r"smtp_password",
    )
]


def _redact_value(v):
    if isinstance(v, str):
        if not v:
            return v
        # Show enough to recognize the kind of value without leaking it
        if len(v) <= 8:
            return "****"
        return f"{v[:4]}…{v[-2:]} (redacted, {len(v)} chars)"
    return "**** (redacted)"


def _redact(obj, parent_key: str = ""):
    """Recursively redact values for any key matching the patterns above."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(p.search(k) for p in _REDACT_KEY_PATTERNS):
                out[k] = _redact_value(v)
            else:
                out[k] = _redact(v, parent_key=k)
        return out
    if isinstance(obj, list):
        return [_redact(item, parent_key=parent_key) for item in obj]
    return obj


@router.get("/admin/config", response_class=HTMLResponse)
async def admin_config_view(request: Request,
                            current_user: dict = Depends(require_role("admin"))):
    from web.main import get_config, get_apps_dir
    config = get_config()
    redacted = _redact(config)
    rendered = yaml.safe_dump(
        redacted, sort_keys=True, default_flow_style=False, allow_unicode=True,
    )
    config_path = os.path.abspath("config.yaml")
    return templates.TemplateResponse(
        request, "admin_config.html",
        _ctx(request, current_user,
             config_yaml=rendered,
             config_path=config_path,
             apps_dir=get_apps_dir(),
             redact_keys=[p.pattern for p in _REDACT_KEY_PATTERNS]),
    )


@router.post("/admin/config/reload")
async def admin_config_reload(
    request: Request,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    """Re-read config.yaml + reload apps. Limited reload — some changes
    (scheduling, auth, LDAP server-level) still require a full restart.
    Logged as 'config.reload' in the audit log."""
    from web.main import reload_config_and_apps, get_db
    db = get_db()
    try:
        details = reload_config_and_apps()
        db.log_audit(
            "config.reload",
            user_id=current_user["id"],
            username=current_user.get("username"),
            details=details,
        )
    except Exception as exc:
        db.log_audit(
            "config.reload",
            user_id=current_user["id"],
            username=current_user.get("username"),
            details=f"failed: {exc}",
        )
        raise
    return RedirectResponse("/admin/config", status_code=303)
