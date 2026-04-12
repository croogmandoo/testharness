import sqlite3
import json
from typing import Optional
from harness.models import Run, TestResult, AppState

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           TEXT PRIMARY KEY,
    app          TEXT NOT NULL,
    environment  TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    status       TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE TABLE IF NOT EXISTS test_results (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL REFERENCES runs(id),
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    test_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    error_msg   TEXT,
    step_log    TEXT,
    screenshot  TEXT,
    duration_ms INTEGER,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_test_results_lookup
    ON test_results(app, environment, test_name, finished_at);
CREATE TABLE IF NOT EXISTS app_state (
    app         TEXT NOT NULL,
    environment TEXT NOT NULL,
    test_name   TEXT NOT NULL,
    state       TEXT NOT NULL,
    since       TEXT NOT NULL,
    PRIMARY KEY (app, environment, test_name)
);
"""

class Database:
    def __init__(self, path: str = "data/harness.db"):
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

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
            {"step": s.step, "status": s.status, "duration_ms": s.duration_ms, "error": s.error}
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

    def get_results_for_run(self, run_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE run_id=?", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

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

    def is_run_active(self, app: str, environment: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM runs WHERE app=? AND environment=? AND status='running'",
                (app, environment)
            ).fetchone()
            return row is not None

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
                apps[app] = {"app": app, "total": 0, "passing": 0, "failing": 0, "unknown": 0}
            apps[app]["total"] += row["cnt"]
            apps[app][row["state"]] += row["cnt"]
        with self._connect() as conn:
            last_runs = conn.execute(
                "SELECT app, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
        for row in last_runs:
            if row["app"] in apps:
                apps[row["app"]]["last_run"] = row["last_run"]
        return list(apps.values())
