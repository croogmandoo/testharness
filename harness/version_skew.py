"""Worker / central version-skew detection.

Workers report ``HARNESS_WORKER_VERSION`` on register + heartbeat. The
central harness exposes its own ``__version__`` (see ``harness/__init__.py``).
This module compares the two and decides:

* "ok"          — same major, minor diff within ``warning_minors``
* "stale"       — same major but worker is further than ``warning_minors`` behind
* "incompatible" — different major version. Dispatch path REFUSES jobs.

We're deliberately permissive about parsing: a missing or unparseable
version is treated as 0.0.0 (incompatible by default would lock everyone
out on upgrade, so we tag it "ok" and surface "unknown" in the UI instead).

Format expected: ``MAJOR.MINOR.PATCH``, dotted-decimal, ignoring any
``-suffix`` (e.g. "1.2.3-rc1" → (1, 2, 3)). Patch number is parsed but not
consulted for skew decisions.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

_log = logging.getLogger("harness.version_skew")

# Default knob — overridable via config.worker.version_skew_warning_minors
DEFAULT_WARNING_MINORS = 2


_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_version(v: Optional[str]) -> Optional[tuple[int, int, int]]:
    """Parse a SemVer-ish string into (major, minor, patch). Returns None
    for empty / unparseable input — callers should treat None as "unknown,
    skip skew enforcement"."""
    if not v or not isinstance(v, str):
        return None
    m = _VERSION_RE.match(v)
    if not m:
        return None
    try:
        major = int(m.group(1))
        minor = int(m.group(2) or 0)
        patch = int(m.group(3) or 0)
    except (TypeError, ValueError):
        return None
    return (major, minor, patch)


def classify_skew(
    worker_version: Optional[str],
    central_version: str,
    *,
    warning_minors: int = DEFAULT_WARNING_MINORS,
) -> str:
    """Return one of ``"ok" | "stale" | "incompatible" | "unknown"``.

    * ``"unknown"``      — worker_version is missing or unparseable
    * ``"incompatible"`` — major versions differ (dispatch refuses)
    * ``"stale"``        — same major; worker minor is < central minor - warning_minors
    * ``"ok"``           — everything else

    A worker NEWER than central is always ``"ok"`` (no upper bound) — we
    expect rolling deployments where workers may briefly be ahead.
    """
    wv = parse_version(worker_version)
    cv = parse_version(central_version)
    if wv is None or cv is None:
        return "unknown"
    if wv[0] != cv[0]:
        return "incompatible"
    # Same major. Stale only if worker is OLDER and gap exceeds threshold.
    if wv[1] < cv[1] - max(0, int(warning_minors)):
        return "stale"
    return "ok"


def is_version_stale(
    worker_version: Optional[str],
    central_version: str,
    *,
    warning_minors: int = DEFAULT_WARNING_MINORS,
) -> bool:
    """Boolean form of ``classify_skew(...) == "stale"`` — convenient for
    setting the ``runners.version_stale`` column."""
    return classify_skew(worker_version, central_version,
                         warning_minors=warning_minors) == "stale"


def is_major_incompatible(
    worker_version: Optional[str],
    central_version: str,
) -> bool:
    """True iff the worker's MAJOR version differs from central's. The
    dispatch path uses this as a hard refusal: incompatible workers receive
    no jobs even though they remain registered (so the admin UI can show
    why their workload is dropping to zero)."""
    return classify_skew(worker_version, central_version) == "incompatible"


def incompatibility_notice(
    worker_version: Optional[str],
    central_version: str,
) -> Optional[str]:
    """Human-readable notice when the worker is incompatible. Returned to
    the worker in heartbeat responses so it can log/surface the reason for
    not receiving jobs. None if compatible."""
    if not is_major_incompatible(worker_version, central_version):
        return None
    return (
        f"worker version {worker_version!r} is incompatible with central "
        f"version {central_version!r}: MAJOR versions differ. This worker "
        f"will receive no jobs until upgraded."
    )


def central_version() -> str:
    """Single source of truth for the central's reported version. Module-
    level import would create a cycle (harness/__init__ imports models, which
    eventually imports config which… anyway, lazy is safer)."""
    try:
        import harness
        return getattr(harness, "__version__", "0.0.0")
    except Exception:
        return "0.0.0"
