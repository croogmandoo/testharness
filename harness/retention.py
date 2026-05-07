"""Retention / pruning of old run history.

Deletes completed runs and their test_results older than `retention_days`,
plus orphaned screenshot files. Active / in-progress runs are preserved.
"""
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

_log = logging.getLogger(__name__)


def _cutoff_iso(retention_days: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    return cutoff.isoformat()


def prune_old_runs(db, retention_days: Optional[int],
                   screenshots_dir: str = "data/screenshots") -> dict:
    """Delete runs/results older than retention_days and remove their screenshots.

    A `retention_days` of None, 0, or negative disables pruning and returns zeros.
    Screenshot files are removed *after* the DB delete so a crash mid-prune
    leaves orphan files (recoverable) rather than dangling DB references.
    """
    if not retention_days or retention_days <= 0:
        return {"runs_deleted": 0, "results_deleted": 0, "screenshots_deleted": 0}

    cutoff = _cutoff_iso(retention_days)
    screenshot_files = db.list_screenshots_older_than(cutoff)
    counts = db.prune_runs_older_than(cutoff)

    screenshots_deleted = 0
    for fname in screenshot_files:
        path = os.path.join(screenshots_dir, fname)
        try:
            os.remove(path)
            screenshots_deleted += 1
        except FileNotFoundError:
            pass
        except OSError as e:
            _log.warning("retention: could not remove %s: %s", path, e)

    counts["screenshots_deleted"] = screenshots_deleted
    _log.info(
        "retention: pruned %d runs, %d results, %d screenshots older than %s",
        counts["runs_deleted"], counts["results_deleted"],
        screenshots_deleted, cutoff,
    )
    return counts
