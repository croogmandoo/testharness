"""Database base — schema, connection, init and online-backup.

The Database class composes its method surface from per-domain mixins
living in this package (see ``_api_keys.py``, ``_audit.py``,
``_runs.py``, etc). Tests still import ``from harness.db import Database``
unchanged; the package's ``__init__.py`` re-exports it.
"""
import sqlite3

from ._api_keys import APIKeysMixin
from ._app_state import AppStateMixin
from ._audit import AuditMixin
from ._runner_jobs import RunnerJobsMixin
from ._runners import RunnersMixin
from ._runs import RunsMixin
from ._secrets import SecretsMixin
from ._users import UsersMixin

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

class Database(APIKeysMixin, AppStateMixin, AuditMixin, RunnerJobsMixin,
               RunnersMixin, RunsMixin, SecretsMixin, UsersMixin):
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
                # ca-cert hardening: enforce unique names so the admin UI
                # doesn't show two rows that look identical. Existing DBs
                # with pre-existing duplicates will silently keep them (the
                # try/except below swallows the IntegrityError), but new
                # inserts will be blocked at the SQL layer.
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ca_certs_name ON ca_certs(name)",
            ):
                try:
                    conn.execute(migration)
                    conn.commit()
                except Exception:
                    pass

    def backup_to_path(self, dest_path: str) -> None:
        """Take a consistent online snapshot of the live DB to dest_path.

        Uses SQLite's BACKUP API (`source.backup(dest)`), which is safe to
        call while the harness is running — readers/writers are not blocked
        and the resulting file is guaranteed to be a valid, atomic copy.
        """
        import sqlite3 as _sql
        with self._connect() as src, _sql.connect(dest_path) as dst:
            src.backup(dst)

