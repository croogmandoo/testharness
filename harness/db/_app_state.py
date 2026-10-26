"""App-state, history, dashboard summary, retention and stale-alert
queries.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
from typing import Optional

from harness.models import AppState


class AppStateMixin:
    def upsert_app_state(self, state: AppState) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO app_state (app, environment, test_name, state, since) "
                "VALUES (?,?,?,?,?) ON CONFLICT(app,environment,test_name) "
                "DO UPDATE SET state=excluded.state, since=excluded.since",
                (state.app, state.environment, state.test_name, state.state, state.since)
            )

    def get_app_state(self, app: str, environment: str, test_name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM app_state WHERE app=? AND environment=? AND test_name=?",
                (app, environment, test_name)
            ).fetchone()
            return dict(row) if row else None

    def list_screenshots_older_than(self, cutoff_iso: str) -> list:
        """Filenames of screenshots attached to test_results finished before cutoff."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT screenshot FROM test_results "
                "WHERE finished_at IS NOT NULL AND finished_at < ? "
                "AND screenshot IS NOT NULL AND screenshot != ''",
                (cutoff_iso,)
            ).fetchall()
        return [r["screenshot"] for r in rows]

    def prune_runs_older_than(self, cutoff_iso: str) -> dict:
        """Delete completed runs (and their test_results) finished before cutoff.

        Active or in-progress runs (no finished_at) are left alone.
        Returns counts: {"runs_deleted", "results_deleted"}.
        """
        with self._connect() as conn:
            results = conn.execute(
                "DELETE FROM test_results WHERE finished_at IS NOT NULL "
                "AND finished_at < ?",
                (cutoff_iso,)
            )
            results_deleted = results.rowcount
            runs = conn.execute(
                "DELETE FROM runs WHERE finished_at IS NOT NULL "
                "AND finished_at < ?",
                (cutoff_iso,)
            )
            runs_deleted = runs.rowcount
        return {"runs_deleted": runs_deleted, "results_deleted": results_deleted}

    def get_app_run_history(self, app: str, environment: str,
                            limit: int = 7) -> list:
        """Return the most recent completed runs as a list of pass/fail tags
        suitable for a dashboard sparkline.

        Each entry: {"id", "finished_at", "status"}.  status is "pass" if every
        test in the run passed, "fail" otherwise.  Sorted newest-first.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT r.id, r.finished_at, "
                "       SUM(CASE WHEN tr.status NOT IN ('pass','passing') "
                "                THEN 1 ELSE 0 END) AS fail_count "
                "FROM runs r LEFT JOIN test_results tr ON tr.run_id = r.id "
                "WHERE r.app = ? AND r.environment = ? AND r.status = 'complete' "
                "GROUP BY r.id, r.finished_at "
                "ORDER BY r.finished_at DESC LIMIT ?",
                (app, environment, int(limit)),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "finished_at": row["finished_at"],
                "status": "fail" if (row["fail_count"] or 0) > 0 else "pass",
            }
            for row in rows
        ]

    # ── Stale-run alert dedup ──────────────────────────────────────────────

    def get_last_stale_alert_at(self, app: str, environment: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_alerted_at FROM stale_alerts "
                "WHERE app=? AND environment=?",
                (app, environment),
            ).fetchone()
        return row["last_alerted_at"] if row else None

    def mark_stale_alerted(self, app: str, environment: str,
                           timestamp_iso: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO stale_alerts (app, environment, last_alerted_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(app, environment) DO UPDATE SET last_alerted_at = excluded.last_alerted_at",
                (app, environment, timestamp_iso),
            )

    def clear_stale_alert(self, app: str, environment: str) -> None:
        """Called when a fresh run lands — clears the dedup so the next time
        the app goes stale we alert again."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM stale_alerts WHERE app=? AND environment=?",
                (app, environment),
            )

    def get_app_last_finished_at(self, app: str, environment: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT MAX(finished_at) AS finished_at FROM runs "
                "WHERE app=? AND environment=? AND status='complete'",
                (app, environment),
            ).fetchone()
        return row["finished_at"] if row and row["finished_at"] else None

    def is_run_active(self, app: str, environment: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE app=? AND environment=? AND status='running'",
                (app, environment)
            ).fetchone()
            return row is not None

    def get_recent_runs(self, app: str, environment: str, limit: int = 10) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE app=? AND environment=? ORDER BY started_at DESC LIMIT ?",
                (app, environment, limit)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_app_summary(self, environment: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT app, state, COUNT(*) as cnt FROM app_state "
                "WHERE environment=? GROUP BY app, state",
                (environment,)
            ).fetchall()
            apps: dict = {}
            for row in rows:
                app = row["app"]
                if app not in apps:
                    apps[app] = {
                        "app": app, "total": 0,
                        "passing": 0, "failing": 0, "unknown": 0,
                        "last_run": None,
                    }
                apps[app]["total"] += row["cnt"]
                apps[app][row["state"]] += row["cnt"]

            last_runs = conn.execute(
                "SELECT app, id, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
            for row in last_runs:
                if row["app"] in apps:
                    apps[row["app"]]["last_run"] = row["last_run"]
                    apps[row["app"]]["last_run_id"] = row["id"]
            for app_dict in apps.values():
                app_dict.setdefault("last_run_id", None)

            active_rows = conn.execute(
                "SELECT app, id FROM runs "
                "WHERE environment=? AND status IN ('pending','running')",
                (environment,)
            ).fetchall()
            active_map = {row["app"]: row["id"] for row in active_rows}
            for app_dict in apps.values():
                app_dict["active_run_id"] = active_map.get(app_dict["app"])
            return list(apps.values())

    def get_last_screenshot(self, app: str, environment: str, test_name: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT screenshot FROM test_results "
                "WHERE app=? AND environment=? AND test_name=? "
                "AND screenshot IS NOT NULL AND status='pass' "
                "ORDER BY finished_at DESC LIMIT 1",
                (app, environment, test_name),
            ).fetchone()
            return row["screenshot"] if row else None
