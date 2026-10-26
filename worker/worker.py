"""Remote worker — registers with a central harness, polls for queued jobs,
runs them locally, submits results back.

Reuses every test-execution module from harness/ — only the orchestration
loop is worker-specific. The harness's runner module's `_execute_test`
function handles api / availability / browser tests identically whether
called from the in-process scheduler or from here.

Configuration is environment-variable only (twelve-factor):

    HARNESS_URL              — central harness, e.g. https://harness.example.com
    HARNESS_API_KEY          — Bearer token, hth_...
    HARNESS_WORKER_NAME      — unique name for this worker (required)
    HARNESS_WORKER_TAGS      — comma-separated tag list (default: empty)
    HARNESS_WORKER_VERSION   — reported back at registration (default: harness version)
    HARNESS_CAPABILITIES     — comma-separated (default: api,availability,browser)
    HARNESS_POLL_INTERVAL    — seconds between polls (default: 10)
    HARNESS_LONG_POLL_WAIT   — server-side block on GET /jobs (default: 30)
    HARNESS_HEARTBEAT_INTERVAL — seconds between heartbeats (default: 30)
    HARNESS_CAPACITY         — max concurrent jobs (default: 4)
    HARNESS_VERIFY_TLS       — "true"/"false" (default: true)
    HARNESS_OUTBOX_PATH      — local buffer for failed result uploads
                                (default: data/worker_outbox.jsonl)
    HARNESS_OUTBOX_MAX       — max entries in the outbox (default: 1000)
    HARNESS_SCREENSHOT_DIR   — local screenshot directory to upload
                                (default: data/worker_screenshots)
    HARNESS_MAX_SCREENSHOT_BYTES — reject screenshots larger than this
                                (default: 5_242_880 = 5 MiB)

Run:
    python -m worker.worker
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from harness.models import StepResult, TestResult

_log = logging.getLogger("harness.worker")


# ── Config ──────────────────────────────────────────────────────────────────

class WorkerConfig:
    def __init__(self):
        self.harness_url = _required_env("HARNESS_URL").rstrip("/")
        self.api_key = _required_env("HARNESS_API_KEY")
        self.name = _required_env("HARNESS_WORKER_NAME")
        self.tags = _csv_env("HARNESS_WORKER_TAGS", default=[])
        self.capabilities = _csv_env(
            "HARNESS_CAPABILITIES",
            default=["api", "availability", "browser"],
        )
        self.version = os.environ.get("HARNESS_WORKER_VERSION", "1.0.0")
        self.poll_interval = int(os.environ.get("HARNESS_POLL_INTERVAL", "10"))
        self.long_poll_wait = int(os.environ.get("HARNESS_LONG_POLL_WAIT", "30"))
        self.heartbeat_interval = int(os.environ.get("HARNESS_HEARTBEAT_INTERVAL", "30"))
        self.capacity = int(os.environ.get("HARNESS_CAPACITY", "4"))
        verify_str = os.environ.get("HARNESS_VERIFY_TLS", "true").lower()
        self.verify_tls = verify_str not in ("false", "0", "no", "off")
        self.outbox_path = os.environ.get(
            "HARNESS_OUTBOX_PATH", "data/worker_outbox.jsonl",
        )
        self.outbox_max = int(os.environ.get("HARNESS_OUTBOX_MAX", "1000"))
        self.screenshot_dir = os.environ.get(
            "HARNESS_SCREENSHOT_DIR", "data/worker_screenshots",
        )
        self.max_screenshot_bytes = int(
            os.environ.get("HARNESS_MAX_SCREENSHOT_BYTES", str(5 * 1024 * 1024))
        )


def _required_env(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        sys.stderr.write(
            f"\n[worker] required env var {key} is not set\n"
            "See worker/README.md for the full env-var contract.\n\n"
        )
        sys.exit(2)
    return val


def _csv_env(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key, "")
    if not raw.strip():
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


# ── Central API helpers ─────────────────────────────────────────────────────

def _headers(cfg: WorkerConfig) -> dict:
    return {"Authorization": f"Bearer {cfg.api_key}", "Accept": "application/json"}


async def register(client: httpx.AsyncClient, cfg: WorkerConfig) -> dict:
    resp = await client.post(
        f"{cfg.harness_url}/api/runner/register",
        json={
            "name": cfg.name,
            "tags": cfg.tags,
            "capabilities": cfg.capabilities,
            "version": cfg.version,
        },
        headers=_headers(cfg),
    )
    resp.raise_for_status()
    body = resp.json()
    # workers-v2-1: log any major-version-mismatch notice the central sent
    # along on registration. The notice repeats on every heartbeat (see
    # ``heartbeat`` above) so ops keep seeing it.
    notice = body.get("notice") if isinstance(body, dict) else None
    if notice:
        _log.warning("worker[%s] central notice on register: %s", cfg.name, notice)
    return body


async def heartbeat(client: httpx.AsyncClient, cfg: WorkerConfig,
                     health: Optional[str] = None) -> None:
    """Send a liveness ping. ``health`` is the worker's self-reported
    'healthy'|'unhealthy' (workers-v2-1). The central uses this to auto-
    drain a worker that reports unhealthy and auto-restore one that
    recovers. The response may carry a ``notice`` string when the central
    has refused to dispatch jobs (major-version mismatch) — we log it at
    WARNING so ops see it on every heartbeat until the worker is upgraded.
    """
    payload: dict = {"name": cfg.name, "version": cfg.version}
    if health is not None:
        payload["health"] = health
    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/heartbeat",
            json=payload,
            headers=_headers(cfg),
        )
        resp.raise_for_status()
        try:
            body = resp.json() or {}
        except ValueError:
            body = {}
        notice = body.get("notice")
        if notice:
            _log.warning("worker[%s] central notice: %s", cfg.name, notice)
    except httpx.HTTPError as e:
        _log.warning("heartbeat failed: %s", e)


async def poll_for_jobs(client: httpx.AsyncClient, cfg: WorkerConfig,
                         capacity: int) -> tuple[list[dict], Optional[int]]:
    """Ask the central for queued jobs. Returns (jobs, retry_after_seconds).

    Uses the long-poll ``GET /api/runner/jobs?wait=…`` endpoint added in
    workers-v2 — the connection blocks server-side until a job is available
    or ``cfg.long_poll_wait`` seconds elapse. On 204 No Content, falls back
    to fixed-interval polling using ``Retry-After`` (or ``cfg.poll_interval``).
    """
    wait = max(0, min(int(cfg.long_poll_wait), 60))
    # Client read timeout must outlast the server's wait by a buffer (~10s)
    # to account for header round-trip and small clock skew.
    read_timeout = wait + 10
    try:
        resp = await client.get(
            f"{cfg.harness_url}/api/runner/jobs",
            params={"name": cfg.name, "wait": wait, "capacity": capacity},
            headers=_headers(cfg),
            timeout=httpx.Timeout(read_timeout, connect=10.0),
        )
        if resp.status_code == 204:
            retry_after = resp.headers.get("Retry-After")
            try:
                return [], int(retry_after) if retry_after else None
            except (ValueError, TypeError):
                return [], None
        if resp.status_code == 404:
            # Server is older / endpoint missing — fall back to POST /poll
            return await _poll_for_jobs_short(client, cfg, capacity), None
        resp.raise_for_status()
        return resp.json().get("jobs", []), None
    except httpx.HTTPError as e:
        _log.warning("long-poll failed: %s — falling back to short poll", e)
        try:
            return await _poll_for_jobs_short(client, cfg, capacity), None
        except httpx.HTTPError as e2:
            _log.warning("poll failed: %s", e2)
            return [], None


async def _poll_for_jobs_short(client: httpx.AsyncClient, cfg: WorkerConfig,
                                capacity: int) -> list[dict]:
    """Legacy short-poll fallback when the long-poll endpoint is unavailable."""
    resp = await client.post(
        f"{cfg.harness_url}/api/runner/poll",
        json={"name": cfg.name, "capacity": capacity},
        headers=_headers(cfg),
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json().get("jobs", [])


async def submit_results(client: httpx.AsyncClient, cfg: WorkerConfig,
                          job_id: str, run_status: str,
                          results: list[dict],
                          error: Optional[str] = None,
                          screenshots: Optional[list[dict]] = None) -> None:
    """POST results to /api/runner/results. On connection error or 5xx,
    persist the payload to the local outbox for later replay. On success,
    opportunistically flush any queued outbox entries.

    A 4xx (client error) is logged but NOT buffered — replaying a malformed
    payload won't help. Buffering is reserved for transient failures.
    """
    # workers-v2-1: pre-flight volume health check. If unhealthy, log loudly
    # but STILL attempt the POST — the outbox is the recovery path. The HB
    # loop will pick up the same state on its next tick and update central.
    pre_issues = _check_volume_health(cfg)
    if pre_issues:
        _log.error(
            "submit_results pre-flight: volume health degraded for job %s: %s",
            job_id, "; ".join(pre_issues),
        )

    payload = {
        "job_id": job_id,
        "run_status": run_status,
        "error": error,
        "results": results,
    }
    if screenshots:
        payload["screenshots"] = screenshots
    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/results",
            json=payload,
            headers=_headers(cfg),
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if 500 <= e.response.status_code < 600:
            _log.warning(
                "submit_results 5xx for job %s (%s) — buffering for replay",
                job_id, e.response.status_code,
            )
            outbox_append(cfg, payload)
        else:
            _log.error(
                "submit_results 4xx for job %s (%s) — not retrying: %s",
                job_id, e.response.status_code, e,
            )
        return
    except httpx.HTTPError as e:
        # Connection error, timeout, DNS, etc. — buffer for replay.
        _log.warning(
            "submit_results network error for job %s: %s — buffering for replay",
            job_id, e,
        )
        outbox_append(cfg, payload)
        return

    # Success path — try to flush the outbox (one entry).
    await outbox_flush_one(client, cfg)


# ── Volume health (workers-v2-1) ────────────────────────────────────────────
#
# A worker's two on-disk dependencies — the outbox parent dir and the
# screenshot dir — must be writable for the worker to function. We poll
# both once per heartbeat tick. If either is unhealthy:
#   1. Log ERROR with the issue list.
#   2. Attempt one recreate via os.makedirs(exist_ok=True).
#   3. If STILL unhealthy, flip the worker-side flag — the next heartbeat
#      payload carries health="unhealthy" so the central can drain this
#      worker from dispatch. Recovery on the next healthy tick restores it.
# Submit-results path also runs a check before each POST; failures are
# logged but the POST still attempts (the outbox is the recovery fallback).

def _check_volume_health(cfg: WorkerConfig) -> list[str]:
    """Verify the outbox parent dir and screenshot dir exist + are writable.
    Returns a list of human-readable issue strings — empty means healthy.

    Method: ``os.makedirs(exist_ok=True)`` then write+delete a tiny temp
    file under each directory. We DO NOT rely on os.access(W_OK) — it
    famously lies on root-squashed NFS and overlay filesystems. The temp
    write is the authoritative test.
    """
    import tempfile

    issues: list[str] = []

    def _probe(label: str, path: str) -> Optional[str]:
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as e:
                return f"{label} parent {parent!r} not a directory and cannot be created: {e}"
        if not os.path.isdir(parent):
            return f"{label} parent {parent!r} is not a directory"
        try:
            fd, tmp = tempfile.mkstemp(
                prefix=".health-", suffix=".tmp", dir=parent,
            )
        except OSError as e:
            return f"{label} parent {parent!r} not writable: {e}"
        try:
            os.write(fd, b"ok")
        except OSError as e:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return f"{label} parent {parent!r} write probe failed: {e}"
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            # File written but not unlinked — not fatal, but worth flagging.
            pass
        return None

    # Outbox parent is what we actually write to (the file itself may not
    # exist yet); probe the parent.
    issue = _probe("outbox", cfg.outbox_path)
    if issue:
        issues.append(issue)
    # Screenshot dir IS the directory the worker writes into — probe a temp
    # file directly inside it. Use a sentinel filename path under it so the
    # same _probe helper works.
    issue = _probe("screenshots", os.path.join(cfg.screenshot_dir, ".sentinel"))
    if issue:
        issues.append(issue)
    return issues


def _attempt_volume_recreate(cfg: WorkerConfig, issues: list[str]) -> None:
    """One-shot best-effort: make the outbox parent and screenshot dir
    exist. Used after _check_volume_health flags problems — if the recreate
    fixes things the next probe will come back clean."""
    for path in (os.path.dirname(cfg.outbox_path) or ".", cfg.screenshot_dir):
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            _log.error("volume-health: makedirs(%s) failed: %s", path, e)


# Process-wide flag set by ``heartbeat_loop`` after a failed health check +
# recreate retry. Tests + ``submit_results`` may read it to surface state
# in the next heartbeat / log line.
_unhealthy: bool = False


def is_worker_unhealthy() -> bool:
    """Returns the current cached health flag. Useful for tests and for the
    heartbeat payload assembly."""
    return _unhealthy


def _set_unhealthy(value: bool) -> None:
    global _unhealthy
    _unhealthy = bool(value)


# ── Outbox: durable buffer for failed result uploads (feature #3) ───────────

def outbox_append(cfg: WorkerConfig, payload: dict) -> None:
    """Append one JSON-serialisable payload to the outbox. If the file is
    already at ``cfg.outbox_max`` entries, drop the oldest line and warn."""
    import json as _json
    try:
        os.makedirs(os.path.dirname(cfg.outbox_path) or ".", exist_ok=True)
    except OSError as e:
        _log.error("outbox: cannot create dir for %s: %s", cfg.outbox_path, e)
        return

    # Count existing lines
    existing: list[str] = []
    if os.path.exists(cfg.outbox_path):
        try:
            with open(cfg.outbox_path, "r", encoding="utf-8") as f:
                existing = [ln for ln in f.read().splitlines() if ln.strip()]
        except OSError as e:
            _log.error("outbox: read failed: %s", e)

    while len(existing) >= cfg.outbox_max:
        dropped = existing.pop(0)
        _log.warning(
            "outbox: capacity %d reached — dropping oldest entry (%d bytes)",
            cfg.outbox_max, len(dropped),
        )

    existing.append(_json.dumps(payload, separators=(",", ":")))
    try:
        with open(cfg.outbox_path, "w", encoding="utf-8") as f:
            f.write("\n".join(existing) + "\n")
    except OSError as e:
        _log.error("outbox: write failed: %s", e)


async def outbox_flush_one(client: httpx.AsyncClient, cfg: WorkerConfig) -> bool:
    """Try to replay the oldest entry. On HTTP success, remove it from the
    file; on failure, leave it for the next attempt. Returns True if an
    entry was successfully flushed."""
    import json as _json
    if not os.path.exists(cfg.outbox_path):
        return False
    try:
        with open(cfg.outbox_path, "r", encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError as e:
        _log.error("outbox: read failed during flush: %s", e)
        return False
    if not lines:
        return False
    head = lines[0]
    try:
        payload = _json.loads(head)
    except ValueError:
        _log.warning("outbox: head entry is malformed JSON — discarding")
        lines = lines[1:]
        with open(cfg.outbox_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        return False

    try:
        resp = await client.post(
            f"{cfg.harness_url}/api/runner/results",
            json=payload,
            headers=_headers(cfg),
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        if 400 <= e.response.status_code < 500:
            # Permanent — drop it so the rest of the queue isn't blocked.
            _log.error(
                "outbox: head entry %s — dropping (4xx)",
                e.response.status_code,
            )
            lines = lines[1:]
            with open(cfg.outbox_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + ("\n" if lines else ""))
            return True  # made progress (one entry removed)
        _log.info("outbox: replay failed (%s) — keeping in queue", e.response.status_code)
        return False
    except httpx.HTTPError as e:
        _log.info("outbox: replay network error — keeping in queue: %s", e)
        return False

    lines = lines[1:]
    try:
        with open(cfg.outbox_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    except OSError as e:
        _log.error("outbox: truncate failed after successful replay: %s", e)
        return True
    _log.info("outbox: replayed 1 entry (%d remaining)", len(lines))
    return True


# ── Job execution ───────────────────────────────────────────────────────────

def _test_result_to_dict(tr: TestResult) -> dict:
    """Serialize a TestResult into the wire format expected by /api/runner/results."""
    return {
        "test_name": tr.test_name,
        "status": tr.status,
        "error_msg": tr.error_msg,
        "duration_ms": tr.duration_ms,
        "finished_at": tr.finished_at,
        "step_log": [
            {
                "step": s.step,
                "status": s.status,
                "duration_ms": s.duration_ms,
                "error": s.error,
                "screenshot": None,  # raw bytes go in the top-level screenshots[] block
            }
            for s in (tr.step_log or [])
        ],
        # The worker records the local path it wrote to disk; this is
        # informational. The actual upload happens via the top-level
        # ``screenshots`` block — see ``_collect_screenshots`` below.
        "screenshot": tr.screenshot,
    }


def _collect_screenshots(tr_list: list[TestResult],
                          cfg: WorkerConfig) -> list[dict]:
    """Walk every TestResult + StepResult, base64-encode any on-disk
    screenshot artifact, and return a list of {step_name, content_base64,
    mime_type} entries suitable for the /api/runner/results payload.

    Entries exceeding ``cfg.max_screenshot_bytes`` are dropped with a
    warning — the central enforces the same limit and would 413 them
    anyway. Best-effort: missing files are logged and skipped.
    """
    import base64
    import os

    out: list[dict] = []
    for tr in tr_list:
        # Per-test screenshot
        if tr.screenshot and os.path.isfile(tr.screenshot):
            entry = _encode_screenshot_file(tr.test_name, tr.screenshot, cfg)
            if entry is not None:
                out.append(entry)
        # Per-step screenshots
        for step in (tr.step_log or []):
            if step.screenshot and os.path.isfile(step.screenshot):
                entry = _encode_screenshot_file(step.step, step.screenshot, cfg)
                if entry is not None:
                    out.append(entry)
    return out


def _encode_screenshot_file(step_name: str, path: str,
                             cfg: WorkerConfig) -> Optional[dict]:
    import base64
    import os

    try:
        size = os.path.getsize(path)
    except OSError as e:
        _log.warning("screenshots: stat %s failed: %s", path, e)
        return None
    if size > cfg.max_screenshot_bytes:
        _log.warning(
            "screenshots: %s is %d bytes (max %d) — skipping upload",
            path, size, cfg.max_screenshot_bytes,
        )
        return None
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError as e:
        _log.warning("screenshots: read %s failed: %s", path, e)
        return None
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    mime_map = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif"}
    mime = mime_map.get(ext, "application/octet-stream")
    return {
        "step_name": step_name,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "mime_type": mime,
    }


async def execute_job(
    job: dict, cfg: WorkerConfig,
) -> tuple[str, list[dict], Optional[str], list[TestResult]]:
    """Run all tests in `job["app_def"]` against `job["environment"]`.
    Returns ``(run_status, results_dicts, error, raw_test_results)`` — the
    last element is the list of TestResult dataclasses, needed by
    ``_collect_screenshots`` to walk step screenshots without re-parsing
    the wire payload.
    """
    from harness.api import run_api_test
    from harness.browser import run_availability_test, run_browser_test
    from harness.loader import resolve_base_url
    from harness.runner import _apply_slo_threshold

    app_def = job["app_def"]
    environment = job["environment"]
    job_id = job["job_id"]
    run_id = job["run_id"]

    _log.info("worker[%s] running job %s app=%s env=%s",
              cfg.name, job_id, app_def.get("app"), environment)

    try:
        base_url = resolve_base_url(app_def, environment)
    except Exception as e:
        return ("error", [], f"resolve_base_url: {e}")

    browser_cfg = {"headless": True, "timeout_ms": 30000}
    results_out: list[dict] = []
    raw_results: list[TestResult] = []

    # Sequential mode is forced when any test has `extract:` — but since the
    # central already resolved `$VAR` references, capture chaining is the only
    # remaining reason. We always run sequentially in v1 to keep the worker
    # simple; parallelism is a future optimization.
    captures: dict = {}
    for test_def in app_def.get("tests", []):
        # Worker-side capture chaining mirrors harness.runner._execute_test
        from harness.config import resolve_env_vars
        # environ={}: only captures are substituted here. The worker's own
        # env (HARNESS_API_KEY, ...) must never be reachable from app YAML.
        resolved_td = resolve_env_vars(test_def, strict=True, extra_vars=captures,
                                       environ={})
        ttype = resolved_td.get("type", "availability")
        max_retries = int(resolved_td.get("retry", 0))
        timeout_ms = browser_cfg.get("timeout_ms", 30000)

        result: Optional[TestResult] = None
        for _attempt in range(max_retries + 1):
            try:
                if ttype == "api":
                    result = await run_api_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td, ssl_ctx=None,
                    )
                elif ttype == "browser":
                    test_timeout_ms = resolved_td.get("timeout_ms", timeout_ms)
                    result = await run_browser_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td,
                        headless=browser_cfg.get("headless", True),
                        timeout_ms=test_timeout_ms,
                    )
                else:
                    result = await run_availability_test(
                        run_id, app_def["app"], environment, base_url,
                        resolved_td, ssl_ctx=None,
                    )
                _apply_slo_threshold(resolved_td, result)
                if result.status not in ("fail", "error"):
                    break
            except Exception as e:
                result = TestResult(
                    run_id=run_id, app=app_def["app"], environment=environment,
                    test_name=resolved_td.get("name", "unknown"),
                    status="error",
                    error_msg=f"Worker exception: {type(e).__name__}: {e}",
                    finished_at=datetime.now(timezone.utc).isoformat(),
                )
                _log.exception("worker[%s] test %r raised", cfg.name,
                               resolved_td.get("name"))

        if result is not None and result.captures:
            captures.update(result.captures)
        if result is not None:
            results_out.append(_test_result_to_dict(result))
            raw_results.append(result)

    return ("complete", results_out, None, raw_results)


# ── Main loop ───────────────────────────────────────────────────────────────

_shutdown_event: Optional[asyncio.Event] = None


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown_event.set)
        except NotImplementedError:  # Windows
            pass


async def heartbeat_loop(client: httpx.AsyncClient, cfg: WorkerConfig) -> None:
    """Periodic heartbeat. workers-v2-1: every tick also runs a volume
    health probe. If unhealthy, log ERROR with the issue list, attempt one
    recreate, then re-probe; if still bad set the worker-side _unhealthy
    flag and inform central via the heartbeat ``health`` field. Recovery
    is automatic — the very next clean probe flips the flag back."""
    while _shutdown_event is None or not _shutdown_event.is_set():
        issues = _check_volume_health(cfg)
        if issues:
            _log.error(
                "worker[%s] volume health degraded: %s",
                cfg.name, "; ".join(issues),
            )
            _attempt_volume_recreate(cfg, issues)
            issues = _check_volume_health(cfg)
            if issues:
                _log.error(
                    "worker[%s] volume health still degraded after recreate: %s",
                    cfg.name, "; ".join(issues),
                )
                _set_unhealthy(True)
            else:
                _log.info("worker[%s] volume health recovered after recreate", cfg.name)
                _set_unhealthy(False)
        else:
            if is_worker_unhealthy():
                _log.info("worker[%s] volume health recovered", cfg.name)
            _set_unhealthy(False)

        await heartbeat(
            client, cfg,
            health="unhealthy" if is_worker_unhealthy() else "healthy",
        )
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=cfg.heartbeat_interval)
            return  # shutdown requested
        except asyncio.TimeoutError:
            continue


async def poll_loop(client: httpx.AsyncClient, cfg: WorkerConfig) -> None:
    semaphore = asyncio.Semaphore(cfg.capacity)
    inflight: set[asyncio.Task] = set()

    async def _run_one(job: dict) -> None:
        async with semaphore:
            try:
                status, results, error, raw = await execute_job(job, cfg)
                screenshots = _collect_screenshots(raw, cfg)
                await submit_results(
                    client, cfg, job["job_id"], status, results, error,
                    screenshots=screenshots,
                )
            except Exception:
                _log.exception("execute_job failed for %s", job.get("job_id"))

    while _shutdown_event is None or not _shutdown_event.is_set():
        # Available slots = capacity - currently in flight
        active_count = len([t for t in inflight if not t.done()])
        remaining = cfg.capacity - active_count
        if remaining <= 0:
            # Saturated — wait for at least one in-flight task to complete before polling central
            if inflight:
                try:
                    await asyncio.wait(
                        inflight,
                        return_when=asyncio.FIRST_COMPLETED,
                        timeout=1.0,
                    )
                except Exception:
                    pass
            continue

        jobs, retry_after = await poll_for_jobs(client, cfg, remaining)
        for job in jobs:
            task = asyncio.create_task(_run_one(job))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
        if jobs:
            # Got work — go straight back for more without sleeping.
            continue
        # No jobs. If server advertised Retry-After honour it (capped); else
        # use the configured short-poll interval as the back-off floor. Long-
        # poll itself already absorbed up to ``long_poll_wait`` seconds.
        backoff = retry_after if retry_after is not None else cfg.poll_interval
        backoff = max(1, min(backoff, 300))
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=backoff)
            break  # shutdown requested
        except asyncio.TimeoutError:
            continue

    # On shutdown, wait briefly for in-flight jobs to complete (best effort)
    if inflight:
        _log.info("worker[%s] waiting for %d in-flight jobs to finish",
                  cfg.name, len(inflight))
        try:
            await asyncio.wait_for(asyncio.gather(*inflight, return_exceptions=True),
                                   timeout=60.0)
        except asyncio.TimeoutError:
            _log.warning("worker[%s] some jobs didn't finish in time", cfg.name)


async def amain() -> int:
    # Honor config.yaml's `logging:` block when present; falls back to sensible
    # defaults (rotating data/logs/harness.log + console) if config is missing
    # or unreadable, so the worker can still start in minimal environments.
    try:
        from harness.config import load_config
        from harness.logging_config import configure as _configure_logging
        _cfg = load_config("config.yaml") if os.path.exists("config.yaml") else {}
        _configure_logging(_cfg)
    except Exception:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
    cfg = WorkerConfig()
    # amain() is invoked under asyncio.run() so we're guaranteed a running loop;
    # asyncio.get_event_loop() was removed in Python 3.14.
    _install_signal_handlers(asyncio.get_running_loop())

    _log.info(
        "worker[%s] starting — central=%s tags=%s capacity=%d",
        cfg.name, cfg.harness_url, cfg.tags, cfg.capacity,
    )

    async with httpx.AsyncClient(verify=cfg.verify_tls, timeout=30.0) as client:
        try:
            reg = await register(client, cfg)
        except httpx.HTTPError as e:
            _log.error("worker[%s] registration failed: %s", cfg.name, e)
            return 3
        _log.info("worker[%s] registered (server poll_interval=%ds capacity=%d)",
                  cfg.name, reg.get("poll_interval_seconds"), reg.get("capacity"))

        await asyncio.gather(
            heartbeat_loop(client, cfg),
            poll_loop(client, cfg),
        )

    _log.info("worker[%s] exiting cleanly", cfg.name)
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
