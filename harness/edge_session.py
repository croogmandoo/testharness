"""Microsoft Edge session importer and storage state manager.

Supports importing active session states, cookies, and local storage from Microsoft Edge
or Chrome for Microsoft 365, Entra ID (Azure AD), and PowerApps synthetic monitoring.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

_log = logging.getLogger(__name__)

STORAGE_STATES_DIR = os.path.join("data", "storage_states")

# Standard Microsoft 365 / Entra ID session cookies
ENTRA_ID_COOKIE_NAMES = {
    "estsauth",
    "estsauthpersistent",
    "mspok",
    "signinstatecookie",
    "buid",
    "fpc",
    "esctx",
    "x-ms-gateway-slice",
}


def get_storage_state_path(app: str, environment: str) -> str:
    """Return the canonical storage state path for an app and environment."""
    return os.path.join(STORAGE_STATES_DIR, f"{app}_{environment}.json")


def parse_cookies_to_storage_state(
    raw_data: Union[str, list, dict],
    default_origin: str = "https://apps.powerapps.com",
) -> Dict[str, Any]:
    """Parse various cookie and session export formats into Playwright storage_state format.

    Supports:
      1. Standard Playwright storage_state dict: {"cookies": [...], "origins": [...]}
      2. Cookie-Editor / EditThisCookie JSON array of cookies: [{"name": ..., "value": ..., "domain": ...}]
      3. Netscape cookie file format string
    """
    if isinstance(raw_data, str):
        raw_str = raw_data.strip()
        # Try JSON first
        if raw_str.startswith("{") or raw_str.startswith("["):
            try:
                parsed = json.loads(raw_str)
                return parse_cookies_to_storage_state(parsed, default_origin=default_origin)
            except json.JSONDecodeError:
                pass
        # Fallback: parse Netscape format
        return _parse_netscape_cookies(raw_str, default_origin=default_origin)

    if isinstance(raw_data, dict):
        if "cookies" in raw_data:
            # Already a Playwright storage_state structure
            cookies = [_normalize_cookie(c) for c in raw_data.get("cookies", [])]
            origins = raw_data.get("origins", [])
            return {"cookies": cookies, "origins": origins}
        # If it's a key-value dictionary of cookies
        cookies = []
        for name, value in raw_data.items():
            cookies.append({
                "name": str(name),
                "value": str(value),
                "domain": ".powerapps.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            })
        return {"cookies": cookies, "origins": []}

    if isinstance(raw_data, list):
        # List of cookie objects (e.g. from Cookie-Editor / EditThisCookie)
        cookies = [_normalize_cookie(c) for c in raw_data if isinstance(c, dict) and "name" in c]
        return {"cookies": cookies, "origins": []}

    raise ValueError("Unsupported session state format. Expected Playwright JSON or cookie array.")


def _normalize_cookie(c: dict) -> dict:
    """Normalize cookie attributes to standard Playwright schema."""
    same_site_raw = c.get("sameSite") or c.get("same_site") or "Lax"
    if isinstance(same_site_raw, str):
        val = same_site_raw.capitalize()
        same_site = val if val in ("Strict", "Lax", "None") else "Lax"
    else:
        same_site = "Lax"

    expires = c.get("expirationDate") if "expirationDate" in c else c.get("expires", -1)
    try:
        expires = float(expires) if expires is not None else -1
    except (ValueError, TypeError):
        expires = -1

    return {
        "name": str(c.get("name", "")),
        "value": str(c.get("value", "")),
        "domain": str(c.get("domain", "")),
        "path": str(c.get("path", "/")),
        "expires": expires,
        "httpOnly": bool(c.get("httpOnly", c.get("http_only", False))),
        "secure": bool(c.get("secure", True)),
        "sameSite": same_site,
    }


def _parse_netscape_cookies(text: str, default_origin: str) -> Dict[str, Any]:
    """Parse Netscape/curl cookie file format into Playwright storage state."""
    cookies = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            domain, flag, path, secure, expiration, name, value = parts[:7]
            try:
                exp = float(expiration)
            except (ValueError, TypeError):
                exp = -1
            cookies.append({
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "expires": exp,
                "httpOnly": False,
                "secure": secure.lower() in ("true", "1"),
                "sameSite": "Lax",
            })
    return {"cookies": cookies, "origins": []}


def save_storage_state(app: str, environment: str, storage_state: dict) -> str:
    """Save parsed storage state atomically to disk."""
    os.makedirs(STORAGE_STATES_DIR, exist_ok=True)
    target_path = get_storage_state_path(app, environment)
    temp_path = f"{target_path}.tmp.{os.getpid()}"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(storage_state, f, indent=2)
    os.replace(temp_path, target_path)
    return target_path


def import_edge_session(
    app: str,
    environment: str,
    raw_content: Union[str, list, dict],
) -> Dict[str, Any]:
    """Parse, validate, and import an Edge/M365 session state for an application."""
    parsed = parse_cookies_to_storage_state(raw_content)
    path = save_storage_state(app, environment, parsed)
    info = inspect_storage_state(path)
    info["path"] = path
    return info


def inspect_storage_state(path_or_app: str, environment: Optional[str] = None) -> Dict[str, Any]:
    """Inspect a stored storage_state file and return diagnostic metrics."""
    if environment is not None:
        path = get_storage_state_path(path_or_app, environment)
    else:
        path = path_or_app

    if not os.path.exists(path):
        return {
            "exists": False,
            "cookie_count": 0,
            "has_entra_id_auth": False,
            "status": "missing",
        }

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return {
            "exists": True,
            "error": str(e),
            "status": "corrupt",
            "cookie_count": 0,
        }

    cookies = data.get("cookies", [])
    now = time.time()
    earliest_exp: Optional[float] = None
    has_entra_id = False
    m365_tokens = []
    domains = set()

    for c in cookies:
        name = c.get("name", "")
        domain = c.get("domain", "")
        if domain:
            domains.add(domain)
        if name.lower() in ENTRA_ID_COOKIE_NAMES:
            has_entra_id = True
            m365_tokens.append(name)
        exp = c.get("expires", -1)
        if exp and exp > 0:
            if earliest_exp is None or exp < earliest_exp:
                earliest_exp = exp

    status = "valid"
    days_remaining = None
    expires_str = None
    if earliest_exp is not None:
        rem_sec = earliest_exp - now
        days_remaining = max(0.0, rem_sec / 86400.0)
        dt = datetime.fromtimestamp(earliest_exp, tz=timezone.utc)
        expires_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        if rem_sec <= 0:
            status = "expired"
        elif days_remaining < 3:
            status = "expiring_soon"

    mtime = os.path.getmtime(path)
    updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()

    return {
        "exists": True,
        "path": path,
        "cookie_count": len(cookies),
        "origins_count": len(data.get("origins", [])),
        "has_entra_id_auth": has_entra_id,
        "entra_id_tokens": sorted(list(set(m365_tokens))),
        "domains": sorted(list(domains)),
        "status": status,
        "days_remaining": days_remaining,
        "expires_at": expires_str,
        "updated_at": updated_at,
    }


def delete_storage_state(app: str, environment: str) -> bool:
    """Delete saved storage state file for an app and environment."""
    path = get_storage_state_path(app, environment)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
