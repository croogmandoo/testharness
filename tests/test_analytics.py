"""Tests for harness.analytics — target factory, partition layout, schema
preservation, and end-to-end export to local filesystem."""
from datetime import date, datetime, timezone

import pytest

from harness.analytics import (
    AzureBlobTarget,
    LocalFilesystemTarget,
    build_target,
)


# ── Target factory ─────────────────────────────────────────────────────────

def test_build_target_local_default(tmp_path):
    t = build_target({"type": "local", "path": str(tmp_path)})
    assert isinstance(t, LocalFilesystemTarget)
    assert t.root_path == str(tmp_path)


def test_build_target_local_default_path():
    t = build_target({"type": "local"})
    assert isinstance(t, LocalFilesystemTarget)
    assert t.root_path == "data/exports"


def test_build_target_defaults_to_local_when_type_missing():
    t = build_target({})
    assert isinstance(t, LocalFilesystemTarget)


def test_build_target_azure_blob_with_connection_string():
    t = build_target({
        "type": "azure_blob",
        "container": "synthetics",
        "prefix": "harness/",
        "connection_string": "DefaultEndpointsProtocol=https;AccountName=x;AccountKey=y;",
    })
    assert isinstance(t, AzureBlobTarget)
    assert t.container == "synthetics"
    assert t.prefix == "harness"  # trailing slash stripped


def test_build_target_azure_blob_requires_container():
    with pytest.raises(ValueError, match="container is required"):
        build_target({"type": "azure_blob"})


def test_build_target_unknown_type_raises():
    with pytest.raises(ValueError, match="Unknown analytics.target.type"):
        build_target({"type": "s3", "bucket": "x"})


# ── Partition layout ───────────────────────────────────────────────────────

def test_local_target_partition_path(tmp_path):
    t = LocalFilesystemTarget(str(tmp_path))
    path = t._partition_path("runs", date(2026, 5, 13))
    assert path == str(
        tmp_path / "runs" / "year=2026" / "month=05" / "day=13" / "runs-2026-05-13.parquet"
    )


def test_azure_blob_path_layout():
    t = AzureBlobTarget(
        container="synthetics",
        prefix="harness",
        connection_string="DefaultEndpointsProtocol=https;AccountName=x;AccountKey=y;",
    )
    path = t._blob_path("test_results", date(2026, 5, 13))
    assert path == (
        "harness/test_results/year=2026/month=05/day=13/test_results-2026-05-13.parquet"
    )


def test_azure_blob_path_no_prefix():
    t = AzureBlobTarget(
        container="synthetics",
        prefix="",
        connection_string="DefaultEndpointsProtocol=https;AccountName=x;AccountKey=y;",
    )
    path = t._blob_path("runs", date(2026, 1, 1))
    assert path == "runs/year=2026/month=01/day=01/runs-2026-01-01.parquet"


def test_azure_blob_requires_credentials():
    """AzureBlobTarget must raise a clear error when no auth is configured."""
    t = AzureBlobTarget(container="synthetics", prefix="x")
    with pytest.raises(ValueError, match="connection_string"):
        t._client()


# ── Schema converters round-trip Parquet ───────────────────────────────────

def test_runs_arrow_table_preserves_timestamps_and_computes_duration():
    import pyarrow as pa
    from harness.analytics import _runs_arrow_table

    rows = [
        {
            "id": "r1",
            "app": "Sonarr",
            "environment": "production",
            "triggered_by": "schedule",
            "status": "complete",
            "started_at": "2026-05-13T10:00:00+00:00",
            "finished_at": "2026-05-13T10:01:30+00:00",
        },
        {
            "id": "r2",
            "app": "Radarr",
            "environment": "production",
            "triggered_by": "ui",
            "status": "running",
            "started_at": "2026-05-13T10:00:00+00:00",
            "finished_at": None,
        },
    ]
    tbl = _runs_arrow_table(rows, date(2026, 5, 13))
    assert tbl.schema.field("started_at").type == pa.timestamp("ms", tz="UTC")
    durations = tbl.column("duration_ms").to_pylist()
    assert durations[0] == 90 * 1000   # 1:30 = 90 sec = 90000 ms
    assert durations[1] is None        # active run, no finished_at


def test_test_results_arrow_table_includes_step_count():
    import json
    from harness.analytics import _test_results_arrow_table

    rows = [{
        "id": "tr1",
        "run_id": "r1",
        "app": "App",
        "environment": "production",
        "test_name": "login",
        "status": "pass",
        "error_msg": None,
        "duration_ms": 1500,
        "finished_at": "2026-05-13T10:01:00+00:00",
        "step_log": json.dumps([
            {"step": "navigate", "status": "pass"},
            {"step": "click", "status": "pass"},
            {"step": "assert", "status": "pass"},
        ]),
        "screenshot": None,
    }]
    tbl = _test_results_arrow_table(rows, date(2026, 5, 13))
    assert tbl.column("step_count").to_pylist() == [3]


def test_test_results_step_count_tolerates_bad_json():
    from harness.analytics import _test_results_arrow_table
    rows = [{
        "id": "tr1", "run_id": "r1", "app": "A", "environment": "p",
        "test_name": "t", "status": "pass",
        "error_msg": None, "duration_ms": 1, "finished_at": None,
        "step_log": "not valid json", "screenshot": None,
    }]
    tbl = _test_results_arrow_table(rows, date(2026, 5, 13))
    assert tbl.column("step_count").to_pylist() == [0]


def test_app_state_arrow_table_attaches_as_of_date():
    import pyarrow as pa
    from harness.analytics import _app_state_arrow_table

    rows = [{
        "app": "App",
        "environment": "production",
        "test_name": "t1",
        "state": "passing",
        "since": "2026-05-01T00:00:00+00:00",
    }]
    d = date(2026, 5, 13)
    tbl = _app_state_arrow_table(rows, d)
    assert tbl.column("as_of_date").to_pylist() == [d]
    assert tbl.schema.field("since").type == pa.timestamp("ms", tz="UTC")
