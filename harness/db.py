import sqlite3
import json
import uuid
from datetime import datetime, timezone
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
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    display_name  TEXT,
    email         TEXT,
    password_hash TEXT,
    role          TEXT NOT NULL,
    auth_provider TEXT NOT NULL DEFAULT 'local',
    role_override INTEGER DEFAULT 0,
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
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

    def get_results_for_run(self, run_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE run_id=?", (run_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def get_results_for_app(self, app: str, environment: str, limit: int = 20) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM test_results WHERE app=? AND environment=? "
                "ORDER BY finished_at DESC LIMIT ?",
                (app, environment, limit)
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
                apps[app] = {"app": app, "total": 0, "passing": 0, "failing": 0, "unknown": 0, "last_run": None}
            apps[app]["total"] += row["cnt"]
            apps[app][row["state"]] += row["cnt"]
        with self._connect() as conn:
            last_runs = conn.execute(
                "SELECT app, id, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
        for row in last_runs:
            if row["app"] in apps:
                apps[row["app"]]["last_run"] = row["last_run"]
                apps[row["app"]]["last_run_id"] = row["id"]
        # Ensure last_run_id is always present (None for apps with no completed run)
        for app_dict in apps.values():
            app_dict.setdefault("last_run_id", None)
        with self._connect() as conn:
            active_rows = conn.execute(
                "SELECT app, id FROM runs "
                "WHERE environment=? AND status IN ('pending','running')",
                (environment,)
            ).fetchall()
        active_map = {row["app"]: row["id"] for row in active_rows}
        for app_dict in apps.values():
            app_dict["active_run_id"] = active_map.get(app_dict["app"])
        return list(apps.values())

    # ── Users ──────────────────────────────────────────────────────────────

    def insert_user(self, user: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, display_name, email, password_hash, "
                "role, auth_provider, role_override, is_active, created_at, last_login_at) "
                "VALUES (:id,:username,:display_name,:email,:password_hash,"
                ":role,:auth_provider,:role_override,:is_active,:created_at,:last_login_at)",
                user,
            )

    def get_user_by_username(self, username: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE id=?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    def count_users(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def list_users(self) -> list:
        """Returns all users. password_hash is excluded."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,username,display_name,email,role,auth_provider,"
                "role_override,is_active,created_at,last_login_at FROM users "
                "ORDER BY username"
            ).fetchall()
            return [dict(r) for r in rows]

    def update_user(self, user_id: str, **kwargs) -> None:
        """Update specified fields. Accepted keys: display_name, email, role,
        role_override, is_active, password_hash."""
        allowed = {"display_name", "email", "role", "role_override",
                   "is_active", "password_hash"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        pairs = list(updates.items())
        sets = ", ".join(f"{k}=?" for k, _ in pairs)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE users SET {sets} WHERE id=?",
                [v for _, v in pairs] + [user_id],
            )

    def update_user_last_login(self, user_id: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at=? WHERE id=?",
                (timestamp, user_id),
            )

    def upsert_ldap_user(self, username: str, display_name: str,
                         email: Optional[str], role: str) -> dict:
        """Create or update an LDAP user. Skips role update if role_override=1.
        Returns the up-to-date user dict (no password_hash)."""
        import uuid
        from datetime import datetime, timezone
        existing = self.get_user_by_username(username)
        if existing is None:
            new_id = str(uuid.uuid4())
            self.insert_user({
                "id": new_id,
                "username": username,
                "display_name": display_name,
                "email": email,
                "password_hash": None,
                "role": role,
                "auth_provider": "ldap",
                "role_override": 0,
                "is_active": 1,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "last_login_at": None,
            })
        else:
            updates: dict = {"display_name": display_name, "email": email}
            if not existing.get("role_override"):
                updates["role"] = role
            self.update_user(existing["id"], **updates)
        user = self.get_user_by_username(username)
        user.pop("password_hash", None)
        return user
