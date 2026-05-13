"""Daily Parquet export of harness telemetry into a local directory or
Azure Blob / ADLS Gen2 container.

Layout produced (Hive partitioning — what Synapse, Fabric, Databricks, ADF,
and Power BI all expect natively):

    <prefix>/runs/year=YYYY/month=MM/day=DD/runs-YYYY-MM-DD.parquet
    <prefix>/test_results/year=YYYY/month=MM/day=DD/test_results-YYYY-MM-DD.parquet
    <prefix>/app_state/year=YYYY/month=MM/day=DD/app_state-YYYY-MM-DD.parquet
    <prefix>/audit_log/year=YYYY/month=MM/day=DD/audit_log-YYYY-MM-DD.parquet

The export job is scheduled by `harness.auto_scheduler.add_analytics_export_job`
and wired in via `web/main.py`. See
`docs/superpowers/plans/2026-05-13-analytics-azure-export.md` for the design
rationale and BI-team setup notes in `docs/guides/analytics.md`.

Dependencies are lazy-imported:
  - pyarrow: required for any export (Parquet writer)
  - azure-storage-blob: only when target.type == "azure_blob"
"""
from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

_log = logging.getLogger(__name__)

# Tables this exporter knows how to render. Each entry maps a table name to
# (sql_for_incremental, sql_for_full_snapshot) — exactly one is used per run.
_DEFAULT_TABLES = ("runs", "test_results", "app_state", "audit_log")


# ── Target abstraction ─────────────────────────────────────────────────────

class ExportTarget(ABC):
    """Writes a Parquet blob at a Hive-partitioned path."""

    @abstractmethod
    def write_parquet(self, table: str, partition_date: date,
                      arrow_table) -> str:
        """Persist `arrow_table` and return the location it was written to
        (filesystem path, blob URL, etc.) for logging/audit."""


class LocalFilesystemTarget(ExportTarget):
    """For tests, dev, and on-prem deployments without object storage."""

    def __init__(self, root_path: str):
        self.root_path = root_path

    def write_parquet(self, table: str, partition_date: date, arrow_table) -> str:
        import pyarrow.parquet as pq
        path = self._partition_path(table, partition_date)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pq.write_table(arrow_table, path, compression="snappy")
        return path

    def _partition_path(self, table: str, d: date) -> str:
        partition_dir = os.path.join(
            self.root_path,
            table,
            f"year={d.year:04d}",
            f"month={d.month:02d}",
            f"day={d.day:02d}",
        )
        return os.path.join(partition_dir, f"{table}-{d.isoformat()}.parquet")


class AzureBlobTarget(ExportTarget):
    """Writes Parquet blobs into an Azure Blob / ADLS Gen2 container.

    Auth precedence:
      1. `connection_string` — full Azure connection string
      2. `account_url` + `sas_token` — pre-signed access
      3. `account_url` alone — falls back to DefaultAzureCredential
         (managed identity, az cli login, env vars, etc.)
    """

    def __init__(self, container: str, prefix: str = "",
                 connection_string: Optional[str] = None,
                 account_url: Optional[str] = None,
                 sas_token: Optional[str] = None):
        self.container = container
        self.prefix = prefix.rstrip("/")
        self._connection_string = connection_string
        self._account_url = account_url
        self._sas_token = sas_token
        self._service_client = None

    def _client(self):
        if self._service_client is not None:
            return self._service_client
        from azure.storage.blob import BlobServiceClient
        if self._connection_string:
            self._service_client = BlobServiceClient.from_connection_string(
                self._connection_string
            )
        elif self._account_url and self._sas_token:
            url = self._account_url.rstrip("?")
            sas = self._sas_token.lstrip("?")
            self._service_client = BlobServiceClient(f"{url}?{sas}")
        elif self._account_url:
            from azure.identity import DefaultAzureCredential
            self._service_client = BlobServiceClient(
                self._account_url, credential=DefaultAzureCredential()
            )
        else:
            raise ValueError(
                "AzureBlobTarget requires one of: connection_string, "
                "account_url+sas_token, or account_url (with DefaultAzureCredential)"
            )
        return self._service_client

    def _blob_path(self, table: str, d: date) -> str:
        # Hive partition layout — Synapse/Fabric prune scans on these
        parts = [
            *([self.prefix] if self.prefix else []),
            table,
            f"year={d.year:04d}",
            f"month={d.month:02d}",
            f"day={d.day:02d}",
            f"{table}-{d.isoformat()}.parquet",
        ]
        return "/".join(parts)

    def write_parquet(self, table: str, partition_date: date, arrow_table) -> str:
        import io
        import pyarrow.parquet as pq
        buf = io.BytesIO()
        pq.write_table(arrow_table, buf, compression="snappy")
        buf.seek(0)
        blob_name = self._blob_path(table, partition_date)
        container_client = self._client().get_container_client(self.container)
        container_client.upload_blob(name=blob_name, data=buf, overwrite=True)
        return f"azure://{self.container}/{blob_name}"


def build_target(cfg: dict) -> ExportTarget:
    """Construct an ExportTarget from an `analytics.target` config block."""
    if not isinstance(cfg, dict):
        raise ValueError("analytics.target must be a mapping")
    ttype = (cfg.get("type") or "local").lower()
    if ttype == "local":
        path = cfg.get("path") or "data/exports"
        return LocalFilesystemTarget(path)
    if ttype == "azure_blob":
        container = cfg.get("container")
        if not container:
            raise ValueError("analytics.target.container is required for azure_blob")
        return AzureBlobTarget(
            container=container,
            prefix=cfg.get("prefix", ""),
            connection_string=cfg.get("connection_string"),
            account_url=cfg.get("account_url"),
            sas_token=cfg.get("sas_token"),
        )
    raise ValueError(f"Unknown analytics.target.type: {ttype!r}")


# ── Schema converters ──────────────────────────────────────────────────────

def _parse_ts(value: Optional[str]):
    """Parse an ISO-8601 string (with or without trailing 'Z') into a
    timezone-aware UTC datetime, or return None."""
    if not value:
        return None
    try:
        s = value.rstrip("Z") if isinstance(value, str) else value
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _duration_ms(started_at: Optional[datetime],
                 finished_at: Optional[datetime]) -> Optional[int]:
    if started_at is None or finished_at is None:
        return None
    delta = finished_at - started_at
    return int(delta.total_seconds() * 1000)


def _runs_arrow_table(rows: list, partition_date: date):
    import pyarrow as pa
    started = [_parse_ts(r.get("started_at")) for r in rows]
    finished = [_parse_ts(r.get("finished_at")) for r in rows]
    return pa.Table.from_pydict(
        {
            "id": [r["id"] for r in rows],
            "app": [r["app"] for r in rows],
            "environment": [r["environment"] for r in rows],
            "triggered_by": [r.get("triggered_by") for r in rows],
            "status": [r["status"] for r in rows],
            "started_at": started,
            "finished_at": finished,
            "duration_ms": [_duration_ms(s, f) for s, f in zip(started, finished)],
        },
        schema=pa.schema([
            pa.field("id", pa.string()),
            pa.field("app", pa.string()),
            pa.field("environment", pa.string()),
            pa.field("triggered_by", pa.string()),
            pa.field("status", pa.string()),
            pa.field("started_at", pa.timestamp("ms", tz="UTC")),
            pa.field("finished_at", pa.timestamp("ms", tz="UTC")),
            pa.field("duration_ms", pa.int64()),
        ]),
    )


def _test_results_arrow_table(rows: list, partition_date: date):
    import pyarrow as pa

    def _step_count(step_log_raw):
        if not step_log_raw:
            return 0
        try:
            return len(json.loads(step_log_raw))
        except (TypeError, ValueError):
            return 0

    return pa.Table.from_pydict(
        {
            "id": [r["id"] for r in rows],
            "run_id": [r["run_id"] for r in rows],
            "app": [r["app"] for r in rows],
            "environment": [r["environment"] for r in rows],
            "test_name": [r["test_name"] for r in rows],
            "status": [r["status"] for r in rows],
            "error_msg": [r.get("error_msg") for r in rows],
            "duration_ms": [r.get("duration_ms") for r in rows],
            "finished_at": [_parse_ts(r.get("finished_at")) for r in rows],
            "step_count": [_step_count(r.get("step_log")) for r in rows],
            "step_log": [r.get("step_log") for r in rows],
            "screenshot": [r.get("screenshot") for r in rows],
        },
        schema=pa.schema([
            pa.field("id", pa.string()),
            pa.field("run_id", pa.string()),
            pa.field("app", pa.string()),
            pa.field("environment", pa.string()),
            pa.field("test_name", pa.string()),
            pa.field("status", pa.string()),
            pa.field("error_msg", pa.string()),
            pa.field("duration_ms", pa.int64()),
            pa.field("finished_at", pa.timestamp("ms", tz="UTC")),
            pa.field("step_count", pa.int32()),
            pa.field("step_log", pa.string()),
            pa.field("screenshot", pa.string()),
        ]),
    )


def _app_state_arrow_table(rows: list, partition_date: date):
    import pyarrow as pa
    return pa.Table.from_pydict(
        {
            "app": [r["app"] for r in rows],
            "environment": [r["environment"] for r in rows],
            "test_name": [r["test_name"] for r in rows],
            "state": [r["state"] for r in rows],
            "since": [_parse_ts(r.get("since")) for r in rows],
            "as_of_date": [partition_date for _ in rows],
        },
        schema=pa.schema([
            pa.field("app", pa.string()),
            pa.field("environment", pa.string()),
            pa.field("test_name", pa.string()),
            pa.field("state", pa.string()),
            pa.field("since", pa.timestamp("ms", tz="UTC")),
            pa.field("as_of_date", pa.date32()),
        ]),
    )


def _audit_log_arrow_table(rows: list, partition_date: date):
    import pyarrow as pa
    return pa.Table.from_pydict(
        {
            "id": [r["id"] for r in rows],
            "timestamp": [_parse_ts(r.get("timestamp")) for r in rows],
            "user_id": [r.get("user_id") for r in rows],
            "username": [r.get("username") for r in rows],
            "action": [r["action"] for r in rows],
            "target": [r.get("target") for r in rows],
            "details": [r.get("details") for r in rows],
        },
        schema=pa.schema([
            pa.field("id", pa.string()),
            pa.field("timestamp", pa.timestamp("ms", tz="UTC")),
            pa.field("user_id", pa.string()),
            pa.field("username", pa.string()),
            pa.field("action", pa.string()),
            pa.field("target", pa.string()),
            pa.field("details", pa.string()),
        ]),
    )


# ── DB queries ─────────────────────────────────────────────────────────────

def _day_bounds_utc(d: date) -> tuple[str, str]:
    """Return ISO-8601 strings for the UTC start and end of day `d`."""
    start = datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _fetch_runs_for_day(db, d: date) -> list:
    start_iso, end_iso = _day_bounds_utc(d)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE finished_at >= ? AND finished_at < ? "
            "ORDER BY finished_at",
            (start_iso, end_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def _fetch_test_results_for_day(db, d: date) -> list:
    start_iso, end_iso = _day_bounds_utc(d)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM test_results WHERE finished_at >= ? AND finished_at < ? "
            "ORDER BY finished_at",
            (start_iso, end_iso),
        ).fetchall()
    return [dict(r) for r in rows]


def _fetch_app_state_snapshot(db) -> list:
    with db._connect() as conn:
        rows = conn.execute("SELECT * FROM app_state ORDER BY app, environment, test_name").fetchall()
    return [dict(r) for r in rows]


def _fetch_audit_log_for_day(db, d: date) -> list:
    start_iso, end_iso = _day_bounds_utc(d)
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE timestamp >= ? AND timestamp < ? "
            "ORDER BY timestamp",
            (start_iso, end_iso),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Public API ─────────────────────────────────────────────────────────────

def export_all_tables(db, target: ExportTarget,
                      partition_date: Optional[date] = None,
                      tables: Optional[Iterable[str]] = None) -> dict:
    """Export the requested tables for `partition_date` (default: yesterday UTC).

    Returns a dict mapping table name → location written (or None if empty
    table was skipped). Errors per-table are logged and reported, but the
    function does not raise — one bad table shouldn't stall the others.
    """
    if partition_date is None:
        partition_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    if tables is None:
        tables = _DEFAULT_TABLES

    results: dict = {}
    for table in tables:
        try:
            location = _export_one_table(db, target, table, partition_date)
            results[table] = location
            if location:
                _log.info("analytics: exported %s -> %s", table, location)
            else:
                _log.info("analytics: %s had 0 rows for %s, skipped", table, partition_date)
        except Exception as e:  # noqa: BLE001 — per-table isolation is intentional
            _log.exception("analytics: failed to export %s: %s", table, e)
            results[table] = None
    return results


def _export_one_table(db, target: ExportTarget, table: str,
                      partition_date: date) -> Optional[str]:
    if table == "runs":
        rows = _fetch_runs_for_day(db, partition_date)
        if not rows:
            return None
        arrow = _runs_arrow_table(rows, partition_date)
    elif table == "test_results":
        rows = _fetch_test_results_for_day(db, partition_date)
        if not rows:
            return None
        arrow = _test_results_arrow_table(rows, partition_date)
    elif table == "app_state":
        rows = _fetch_app_state_snapshot(db)
        if not rows:
            return None
        arrow = _app_state_arrow_table(rows, partition_date)
    elif table == "audit_log":
        rows = _fetch_audit_log_for_day(db, partition_date)
        if not rows:
            return None
        arrow = _audit_log_arrow_table(rows, partition_date)
    else:
        raise ValueError(f"Unknown analytics table: {table!r}")
    return target.write_parquet(table, partition_date, arrow)
