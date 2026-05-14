"""Run / test-result storage and history queries.

Methods call ``self._connect()`` — mix into a class that provides it
(see ``harness.db._base.Database``).
"""
import json
from typing import Optional

from harness.models import Run, TestResult


class RunsMixin:
    def insert_run(self, run: Run) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runs (id, app, environment, triggered_by, status) VALUES (?,?,?,?,?)",
                (run.id, run.app, run.environment, run.triggered_by, run.status)
            )

    def update_run_status(self, run_id: str, status: str, *,
                          started_at: Optional[str] = None,
                          finished_at: Optional[str] = None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, started_at=COALESCE(?,started_at), "
                "finished_at=COALESCE(?,finished_at) WHERE id=?",
                (status, started_at, finished_at, run_id)
            )

    def get_run(self, run_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def insert_test_result(self, tr: TestResult) -> None:
        step_log_json = json.dumps([
            {"step": s.step, "status": s.status, "duration_ms": s.duration_ms,
             "error": s.error, "screenshot": s.screenshot}
            for s in tr.step_log
        ])
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO test_results "
                "(id, run_id, app, environment, test_name, status, error_msg, "
                "step_log, screenshot, duration_ms, finished_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tr.id, tr.run_id, tr.app, tr.environment, tr.test_name,
                 tr.status, tr.error_msg, step_log_json,
                 tr.screenshot, tr.duration_ms, tr.finished_at)
            )

    def set_test_result_screenshot_path(self, result_id: str, path: str) -> None:
        """Record a server-side screenshot URL on a test_results row.
        Used by the worker-results ingest pipeline (feature #2)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE test_results SET screenshot_path=? WHERE id=?",
                (path, result_id),
            )

    def get_results_for_run(self, run_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE run_id=?", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_results_for_app(self, app: str, environment: str, limit: int = 20, offset: int = 0) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE app=? AND environment=? "
                "ORDER BY finished_at DESC LIMIT ? OFFSET ?",
                (app, environment, limit, offset)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_run_history_batch(self, app: str, environment: str, test_names: list, limit: int = 10) -> dict:
        """Return {test_name: [status, ...]} for multiple tests in one query."""
        if not test_names:
            return {}
        placeholders = ",".join("?" * len(test_names))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT test_name, status FROM test_results "
                f"WHERE app=? AND environment=? AND test_name IN ({placeholders}) "
                f"ORDER BY finished_at DESC",
                [app, environment] + list(test_names)
            ).fetchall()
        result = {name: [] for name in test_names}
        for row in rows:
            hist = result[row["test_name"]]
            if len(hist) < limit:
                hist.append(row["status"])
        return result
