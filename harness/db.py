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
    last_login_at TEXT,
    oauth_provider_id TEXT,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    locked_until       TEXT
);
CREATE TABLE IF NOT EXISTS secrets (
    id              TEXT PRIMARY KEY,
    name            TEXT UNIQUE NOT NULL,
    encrypted_value TEXT NOT NULL,
    description     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    updated_by      TEXT REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS ca_certs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    pem_content TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    added_by    TEXT REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL,
    expires_at   TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    scope        TEXT NOT NULL DEFAULT '["*"]'   -- JSON array of scope strings
);
CREATE INDEX IF NOT EXISTS idx_api_keys_prefix ON api_keys(key_prefix);
CREATE INDEX IF NOT EXISTS idx_api_keys_user   ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_runs_env_status
    ON runs(environment, status);
CREATE INDEX IF NOT EXISTS idx_runs_app_env
    ON runs(app, environment, started_at DESC);
CREATE TABLE IF NOT EXISTS audit_log (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    user_id     TEXT,
    username    TEXT,
    action      TEXT NOT NULL,
    target      TEXT,
    details     TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user
    ON audit_log(user_id, timestamp DESC);
CREATE TABLE IF NOT EXISTS stale_alerts (
    app              TEXT NOT NULL,
    environment      TEXT NOT NULL,
    last_alerted_at  TEXT NOT NULL,
    PRIMARY KEY (app, environment)
);
CREATE TABLE IF NOT EXISTS runners (
    id            TEXT PRIMARY KEY,
    name          TEXT UNIQUE NOT NULL,
    tags          TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
    capabilities  TEXT NOT NULL DEFAULT '[]',     -- JSON array of strings
    version       TEXT,
    api_key_id    TEXT REFERENCES api_keys(id),
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'unknown' -- online/offline/draining
);
CREATE TABLE IF NOT EXISTS runner_jobs (
    id           TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL REFERENCES runs(id),
    runner_name  TEXT,                  -- pinned worker name, or NULL for tag dispatch
    target_tag   TEXT,                  -- tag-based dispatch, or NULL for any
    app_def_json TEXT NOT NULL,         -- fully-resolved app def (secrets in plaintext)
    environment  TEXT NOT NULL,
    state        TEXT NOT NULL,         -- queued/assigned/complete/timeout/cancelled/failed
    queued_at    TEXT NOT NULL,
    assigned_to  TEXT,                  -- worker name that claimed it
    assigned_at  TEXT,
    completed_at TEXT,
    error        TEXT,
    retry_count  INTEGER NOT NULL DEFAULT 0   -- number of times reaper requeued this job
);
CREATE INDEX IF NOT EXISTS idx_runner_jobs_dispatch
    ON runner_jobs(state, runner_name, target_tag);
CREATE INDEX IF NOT EXISTS idx_runner_jobs_run_id
    ON runner_jobs(run_id);
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
            # Additive migrations for pre-existing databases. SQLite's ALTER
            # TABLE ... ADD COLUMN is idempotent only via the try/except dance
            # because there's no "ADD COLUMN IF NOT EXISTS".
            for migration in (
                "ALTER TABLE users ADD COLUMN oauth_provider_id TEXT",
                "ALTER TABLE users ADD COLUMN failed_login_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE users ADD COLUMN locked_until TEXT",
                # workers-v2: retry tracking on runner_jobs (feature #1)
                "ALTER TABLE runner_jobs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
                # workers-v2: per-key scopes (feature #5). Existing keys default
                # to '["*"]' (all scopes) for backward compatibility.
                "ALTER TABLE api_keys ADD COLUMN scope TEXT NOT NULL DEFAULT '[\"*\"]'",
                # workers-v2: link test_results to screenshot file paths (feature #2)
                "ALTER TABLE test_results ADD COLUMN screenshot_path TEXT",
                # workers-v2-1: version-skew tracking — set on register/heartbeat
                # when worker is more than ``worker.version_skew_warning_minors``
                # minor versions behind central.
                "ALTER TABLE runners ADD COLUMN version_stale INTEGER NOT NULL DEFAULT 0",
                # workers-v2-1: worker self-reported health (volume-check loop).
                # NULL means "no health signal yet" (treat as healthy for back-
                # compat). 'healthy' | 'unhealthy'.
                "ALTER TABLE runners ADD COLUMN health TEXT",
            ):
                try:
                    conn.execute(migration)
                    conn.commit()
                except Exception:
                    pass

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

    # ── Audit log ──────────────────────────────────────────────────────────

    def log_audit(self, action: str, *,
                  user_id: Optional[str] = None,
                  username: Optional[str] = None,
                  target: Optional[str] = None,
                  details: Optional[str] = None) -> str:
        """Append an audit event.  Always best-effort: SQL errors are swallowed
        so that audit-log failures never block the action being audited."""
        entry_id = str(uuid.uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log (id, timestamp, user_id, username, "
                    "action, target, details) VALUES (?,?,?,?,?,?,?)",
                    (entry_id, ts, user_id, username, action, target, details),
                )
        except Exception:  # pragma: no cover — see comment above
            pass
        return entry_id

    def list_audit_log(self, limit: int = 200,
                       user_id: Optional[str] = None,
                       action: Optional[str] = None) -> list:
        clauses, args = [], []
        if user_id:
            clauses.append("user_id=?"); args.append(user_id)
        if action:
            clauses.append("action=?"); args.append(action)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM audit_log {where} "
                "ORDER BY timestamp DESC LIMIT ?",
                args,
            ).fetchall()
        return [dict(r) for r in rows]

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

    def backup_to_path(self, dest_path: str) -> None:
        """Take a consistent online snapshot of the live DB to dest_path.

        Uses SQLite's BACKUP API (`source.backup(dest)`), which is safe to
        call while the harness is running — readers/writers are not blocked
        and the resulting file is guaranteed to be a valid, atomic copy.
        """
        import sqlite3 as _sql
        with self._connect() as src, _sql.connect(dest_path) as dst:
            src.backup(dst)

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

    # ── Users ──────────────────────────────────────────────────────────────

    def insert_user(self, user: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO users (id, username, display_name, email, password_hash, "
                "role, auth_provider, role_override, is_active, created_at, last_login_at, "
                "oauth_provider_id) "
                "VALUES (:id,:username,:display_name,:email,:password_hash,"
                ":role,:auth_provider,:role_override,:is_active,:created_at,:last_login_at,"
                ":oauth_provider_id)",
                {**user, "oauth_provider_id": user.get("oauth_provider_id")},
            )

    def get_user_by_oauth_provider_id(self, provider: str, provider_id: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE auth_provider=? AND oauth_provider_id=?",
                (provider, provider_id),
            ).fetchone()
            return dict(row) if row else None

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

    def delete_user(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))

    def increment_failed_login(self, user_id: str, lock_until: "str | None") -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = failed_login_count + 1, "
                "locked_until = ? WHERE id = ?",
                (lock_until, user_id)
            )

    def reset_failed_login(self, user_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET failed_login_count = 0, locked_until = NULL WHERE id = ?",
                (user_id,)
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

    # ── Secrets ────────────────────────────────────────────────────────────────

    def upsert_secret(self, name: str, encrypted_value: str,
                      description: Optional[str] = None,
                      user_id: Optional[str] = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO secrets (id, name, encrypted_value, description, created_at, updated_at, updated_by) "
                "VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "encrypted_value=excluded.encrypted_value, description=excluded.description, "
                "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (str(uuid.uuid4()), name, encrypted_value, description, now, now, user_id),
            )

    def get_secret(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM secrets WHERE name=?", (name,)
            ).fetchone()
            return dict(row) if row else None

    def delete_secret(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM secrets WHERE name=?", (name,))

    def list_secrets(self) -> list:
        """Returns all secrets. encrypted_value is excluded."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, description, created_at, updated_at, updated_by "
                "FROM secrets ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── CA Certs ───────────────────────────────────────────────────────────────

    def insert_ca_cert(self, row: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO ca_certs (id, name, pem_content, created_at, added_by) "
                "VALUES (:id, :name, :pem_content, :created_at, :added_by)",
                row,
            )

    def list_ca_certs(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ca_certs ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_ca_cert(self, cert_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ca_certs WHERE id=?", (cert_id,)
            ).fetchone()
            return dict(row) if row else None

    def delete_ca_cert(self, cert_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM ca_certs WHERE id=?", (cert_id,))
    # ── API Keys ────────────────────────────────────────────────────────────

    def insert_api_key(self, row: dict) -> None:
        # ``scope`` is optional for back-compat with callers that pre-date
        # feature #5. Default to wildcard JSON.
        row = {**row}
        row.setdefault("scope", '["*"]')
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO api_keys "
                "(id, user_id, name, key_prefix, key_hash, expires_at, "
                "created_at, last_used_at, is_active, scope) "
                "VALUES (:id,:user_id,:name,:key_prefix,:key_hash,:expires_at,"
                ":created_at,:last_used_at,:is_active,:scope)",
                row,
            )

    def get_api_key_by_prefix(self, prefix: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE key_prefix=?", (prefix,)
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_api_key(self, key_id: str, user_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            if user_id is None:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=?", (key_id,)
                )
            else:
                conn.execute(
                    "UPDATE api_keys SET is_active=0 WHERE id=? AND user_id=?",
                    (key_id, user_id),
                )

    def list_api_keys_for_user(self, user_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id=? ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def list_all_api_keys(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT k.*, u.username FROM api_keys k "
                "JOIN users u ON k.user_id = u.id "
                "ORDER BY k.created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_api_key_last_used(self, key_id: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (timestamp, key_id),
            )

    def get_last_screenshot(self, app: str, environment: str, test_name: str):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT screenshot FROM test_results "
                "WHERE app=? AND environment=? AND test_name=? "
                "AND screenshot IS NOT NULL "
                "ORDER BY finished_at DESC LIMIT 1",
                (app, environment, test_name),
            ).fetchone()
            return row["screenshot"] if row else None

    # ── Runners ─────────────────────────────────────────────────────────────

    def upsert_runner(self, *, name: str, tags: list, capabilities: list,
                       version: Optional[str] = None,
                       api_key_id: Optional[str] = None,
                       version_stale: Optional[int] = None) -> dict:
        """Insert or update a runner row. Sets first_seen_at on insert,
        last_seen_at on every call, and marks status='online'.

        ``version_stale`` (0|1) is written when supplied — callers compute it
        from ``harness.version_skew.is_version_stale``. None preserves the
        existing value on update / defaults to 0 on insert.
        """
        now = datetime.now(timezone.utc).isoformat()
        tags_json = json.dumps(sorted(set(tags or [])))
        caps_json = json.dumps(sorted(set(capabilities or [])))
        existing = self.get_runner_by_name(name)
        vs = int(bool(version_stale)) if version_stale is not None else 0
        with self._connect() as conn:
            if existing is None:
                rid = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO runners (id, name, tags, capabilities, version, "
                    "api_key_id, first_seen_at, last_seen_at, status, version_stale) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (rid, name, tags_json, caps_json, version,
                     api_key_id, now, now, "online", vs),
                )
            else:
                # Only overwrite version_stale when caller supplied a value;
                # otherwise leave whatever was last set.
                if version_stale is None:
                    conn.execute(
                        "UPDATE runners SET tags=?, capabilities=?, version=?, "
                        "api_key_id=COALESCE(?, api_key_id), last_seen_at=?, "
                        "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                        "WHERE name=?",
                        (tags_json, caps_json, version, api_key_id, now, name),
                    )
                else:
                    conn.execute(
                        "UPDATE runners SET tags=?, capabilities=?, version=?, "
                        "api_key_id=COALESCE(?, api_key_id), last_seen_at=?, "
                        "version_stale=?, "
                        "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                        "WHERE name=?",
                        (tags_json, caps_json, version, api_key_id, now, vs, name),
                    )
        return self.get_runner_by_name(name)

    def set_runner_version_stale(self, name: str, stale: bool) -> None:
        """Mark a runner row's version_stale flag (0 or 1). Used by the
        heartbeat path which doesn't otherwise mutate the row's metadata."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET version_stale=? WHERE name=?",
                (1 if stale else 0, name),
            )

    def set_runner_health(self, name: str, health: Optional[str]) -> None:
        """Record the worker's self-reported health: 'healthy' | 'unhealthy'
        | None. Heartbeat handlers also auto-toggle ``status`` based on this
        value (unhealthy → draining; healthy → online) — see runner.py."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET health=? WHERE name=?", (health, name),
            )

    def get_runner_by_name(self, name: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runners WHERE name=?", (name,)
            ).fetchone()
            if not row:
                return None
            r = dict(row)
            r["tags"] = json.loads(r.get("tags") or "[]")
            r["capabilities"] = json.loads(r.get("capabilities") or "[]")
            return r

    def list_runners(self) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runners ORDER BY name"
            ).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            r["tags"] = json.loads(r.get("tags") or "[]")
            r["capabilities"] = json.loads(r.get("capabilities") or "[]")
            result.append(r)
        return result

    def touch_runner_heartbeat(self, name: str, timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET last_seen_at=?, "
                "status=CASE WHEN status='draining' THEN 'draining' ELSE 'online' END "
                "WHERE name=?",
                (timestamp, name),
            )

    def set_runner_status(self, name: str, status: str) -> None:
        """Set status to 'online' | 'offline' | 'draining'."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runners SET status=? WHERE name=?", (status, name),
            )

    def delete_runner(self, name: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM runners WHERE name=?", (name,))

    # ── Runner jobs (dispatch queue) ────────────────────────────────────────

    def queue_runner_job(self, *, run_id: str, app_def_json: str,
                          environment: str,
                          runner_name: Optional[str] = None,
                          target_tag: Optional[str] = None) -> str:
        """Insert a queued job. Exactly one of runner_name / target_tag should
        be set (or both NULL = any runner). Returns the job id."""
        job_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runner_jobs "
                "(id, run_id, runner_name, target_tag, app_def_json, environment, "
                "state, queued_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (job_id, run_id, runner_name, target_tag, app_def_json,
                 environment, "queued", now),
            )
        return job_id

    def claim_runner_jobs(self, *, worker_name: str, worker_tags: list,
                           capacity: int) -> list:
        """Atomically claim up to `capacity` queued jobs that match this worker
        — pinned to its name OR tag-dispatched and matching one of its tags.
        Returns the claimed job rows (with state='assigned')."""
        if capacity <= 0:
            return []
        tags = list(worker_tags or [])
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            tag_placeholders = ",".join("?" * len(tags)) if tags else "NULL"
            tag_clause = (
                f"target_tag IN ({tag_placeholders})" if tags else "0"
            )
            sql = (
                f"SELECT id FROM runner_jobs WHERE state='queued' "
                f"AND (runner_name = ? "
                f"     OR (runner_name IS NULL AND target_tag IS NULL) "
                f"     OR (runner_name IS NULL AND {tag_clause})) "
                f"ORDER BY queued_at LIMIT ?"
            )
            params: list = [worker_name] + tags + [capacity]
            candidate_ids = [r["id"] for r in conn.execute(sql, params).fetchall()]
            claimed = []
            for jid in candidate_ids:
                cur = conn.execute(
                    "UPDATE runner_jobs SET state='assigned', assigned_to=?, assigned_at=? "
                    "WHERE id=? AND state='queued'",
                    (worker_name, now, jid),
                )
                if cur.rowcount == 1:
                    row = conn.execute(
                        "SELECT * FROM runner_jobs WHERE id=?", (jid,),
                    ).fetchone()
                    claimed.append(dict(row))
            return claimed

    def complete_runner_job(self, job_id: str,
                             error: Optional[str] = None) -> Optional[dict]:
        """Mark a job complete (or errored). Returns the job row."""
        now = datetime.now(timezone.utc).isoformat()
        state = "complete" if error is None else "error"
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs SET state=?, completed_at=?, error=? WHERE id=?",
                (state, now, error, job_id),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_runner_job(self, job_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def list_runner_jobs_for_runner(self, name: str,
                                     limit: int = 50) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runner_jobs WHERE assigned_to=? "
                "ORDER BY queued_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_inflight_jobs_for_runner(self, name: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM runner_jobs WHERE assigned_to=? "
                "AND state='assigned'",
                (name,),
            ).fetchone()
        return row[0] if row else 0

    def find_stuck_runner_jobs(self, *, older_than: str) -> list:
        """Return runner_jobs in 'assigned' (claimed) state whose
        ``assigned_at`` is older than the supplied ISO timestamp. Used by the
        stuck-job reaper to detect workers that crashed mid-job.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runner_jobs WHERE state='assigned' "
                "AND assigned_at IS NOT NULL AND assigned_at < ? "
                "ORDER BY assigned_at",
                (older_than,),
            ).fetchall()
        return [dict(r) for r in rows]

    def requeue_runner_job(self, job_id: str) -> Optional[dict]:
        """Flip a stuck 'assigned' job back to 'queued' so another worker can
        claim it. Clears assignment metadata and bumps retry_count by 1.
        Returns the row after the update (or None if not found)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs "
                "SET state='queued', assigned_to=NULL, assigned_at=NULL, "
                "    retry_count = retry_count + 1 "
                "WHERE id=? AND state='assigned'",
                (job_id,),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
        return dict(row) if row else None

    def fail_runner_job(self, job_id: str, error: str) -> Optional[dict]:
        """Permanently mark a job 'failed' (retry budget exhausted). The
        associated Run is moved to 'error' so the dashboard surfaces it."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE runner_jobs SET state='failed', completed_at=?, error=? "
                "WHERE id=?",
                (now, error, job_id),
            )
            row = conn.execute(
                "SELECT * FROM runner_jobs WHERE id=?", (job_id,),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE runs SET status='error', finished_at=? WHERE id=?",
                    (now, row["run_id"]),
                )
        return dict(row) if row else None
