"""Shared Jinja2Templates factory.

Why this exists: every route module used to do
    templates = Jinja2Templates(directory=...)
which left no central place to register cross-cutting template globals.
The most pressing case is browser cache-busting on /static/style.css —
without a version query param, an `up -d` to a new image keeps the old
CSS in the user's browser cache until they manually hard-refresh.

This module is the single source of truth for the templates instance.
Each route uses ``make_templates()`` and gets the ``asset_version``
global available to every template.
"""

import hashlib
import os
from functools import lru_cache

from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@lru_cache(maxsize=1)
def _asset_version() -> str:
    """Short content hash over /static/*.css + *.js used as a cache-buster.

    Computed once per process — when we ship a new image with updated
    static assets the process restarts and a fresh hash falls out. The
    hash covers every css/js file in the static dir so any change to any
    of them flips the query param and forces browsers to refetch all of
    them. That's a slight over-bust (changing one JS file evicts the CSS
    too) but simpler than maintaining per-asset manifests and matches
    the deploy granularity — we ship the whole image at once.

    Falls back to ``harness.__version__`` if the dir can't be read (e.g.
    in a degenerate dev setup with no static dir).
    """
    try:
        h = hashlib.sha256()
        names = sorted(
            n for n in os.listdir(_STATIC_DIR)
            if n.endswith((".css", ".js"))
        )
        for n in names:
            with open(os.path.join(_STATIC_DIR, n), "rb") as f:
                h.update(n.encode())  # include filename so renames are visible
                h.update(b"\0")
                h.update(f.read())
                h.update(b"\0")
        return h.hexdigest()[:10]
    except OSError:
        try:
            from harness import __version__
            return __version__
        except Exception:
            return "dev"


def safe_url(value) -> str:
    """Jinja filter: return ``value`` if it is an http(s) URL, else ``"#"``.

    Autoescaping stops attribute break-out but not ``javascript:`` /
    ``data:`` hrefs, so every href built from user-controlled URLs (app
    ``url:`` values) must go through this filter.
    """
    from harness.app_manager import is_http_url
    return value if is_http_url(value) else "#"


def make_templates() -> Jinja2Templates:
    """Build a Jinja2Templates with project-wide globals applied.

    Use this everywhere instead of ``Jinja2Templates(directory=...)`` so
    every template can reference ``{{ asset_version }}`` for cache-busting
    static asset URLs without each route's TemplateResponse having to
    thread the value through its context dict.
    """
    tmpl = Jinja2Templates(directory=_TEMPLATES_DIR)
    tmpl.env.globals["asset_version"] = _asset_version()
    tmpl.env.filters["safe_url"] = safe_url
    return tmpl
