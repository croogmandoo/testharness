"""Outbound corporate proxy resolution and configuration."""
import os
import urllib.parse
from typing import Optional, Dict, Any


def get_outbound_proxy_config(config: Optional[dict] = None) -> Optional[Dict[str, Any]]:
    """Determine the active outbound proxy settings from config or env vars.

    Returns a dict formatted for Playwright and HTTP clients:
    {
        "server": "http://proxy.corp.net:8080",
        "bypass": "localhost,127.0.0.1",   # optional
        "username": "user",                # optional
        "password": "pass",                # optional
        "url": "http://user:pass@proxy.corp.net:8080" # full url with credentials
    }
    or None if no proxy is configured.
    """
    cfg = config or {}
    proxy_section = cfg.get("proxy", {})
    outbound_cfg = proxy_section.get("outbound", {})

    server = None
    bypass = None
    username = None
    password = None

    if outbound_cfg.get("enabled"):
        server = (
            outbound_cfg.get("https_proxy")
            or outbound_cfg.get("http_proxy")
            or outbound_cfg.get("server")
        )
        bypass = outbound_cfg.get("no_proxy") or outbound_cfg.get("bypass")
        username = outbound_cfg.get("username")
        password = outbound_cfg.get("password")

    if not server:
        # Fall back to standard environment variables
        server = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("http_proxy")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("all_proxy")
        )
        bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")

    if not server:
        return None

    parsed = urllib.parse.urlsplit(server)
    if parsed.username and not username:
        username = urllib.parse.unquote(parsed.username)
    if parsed.password and not password:
        password = urllib.parse.unquote(parsed.password)

    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    scheme = parsed.scheme or "http"
    server_clean = f"{scheme}://{netloc}"

    if username and password:
        auth_netloc = f"{urllib.parse.quote(username)}:{urllib.parse.quote(password)}@{netloc}"
        full_url = f"{scheme}://{auth_netloc}"
    elif username:
        auth_netloc = f"{urllib.parse.quote(username)}@{netloc}"
        full_url = f"{scheme}://{auth_netloc}"
    else:
        full_url = server_clean

    res: Dict[str, Any] = {
        "server": server_clean,
        "url": full_url,
    }
    if bypass:
        res["bypass"] = bypass
    if username:
        res["username"] = username
    if password:
        res["password"] = password

    return res


def get_playwright_proxy_args(config: Optional[dict] = None) -> Optional[Dict[str, str]]:
    """Return kwargs for Playwright launch(proxy=...)."""
    cfg = get_outbound_proxy_config(config)
    if not cfg:
        return None
    playwright_dict = {"server": cfg["server"]}
    if "bypass" in cfg:
        playwright_dict["bypass"] = cfg["bypass"]
    if "username" in cfg:
        playwright_dict["username"] = cfg["username"]
    if "password" in cfg:
        playwright_dict["password"] = cfg["password"]
    return playwright_dict


def get_httpx_proxy_url(config: Optional[dict] = None) -> Optional[str]:
    """Return proxy URL string for httpx.AsyncClient(proxy=...)."""
    cfg = get_outbound_proxy_config(config)
    return cfg["url"] if cfg else None
