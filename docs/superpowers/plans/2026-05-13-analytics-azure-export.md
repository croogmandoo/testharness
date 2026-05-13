# Analytics Export to Azure (Parquet → ADLS Gen2)

**Date:** 2026-05-13
**Status:** In progress

## Goal

Stand up a daily, scheduled Parquet export of harness telemetry into an Azure
Blob / ADLS Gen2 container, partitioned in the Hive layout that every Azure
analytics tool (Synapse serverless SQL, Fabric Lakehouse, Power BI, Databricks,
ADF) expects natively. The harness's BI/DIO team can then attach external
tables / shortcut tables and report against the data without us building any
dashboards.

## Non-goals

- Real-time streaming (event bus — that's Option C, a later stage)
- BI dashboarding inside the harness (BI team owns reporting; we ship the data)
- Schema drift management — DB schema is stable; if we change it, we bump the
  export's schema version (a column) and notify BI

## Architecture

```
APScheduler ── daily 01:00 UTC ──► export_all_tables(db, target, date)
                                       │
                                       ▼ (per table)
                            harness/analytics.py
                              build pyarrow Table
                              from DB rows
                                       │
                                       ▼
                          ExportTarget.write_parquet()
                              ├─ LocalFilesystemTarget  (dev / on-prem)
                              └─ AzureBlobTarget        (prod)
                                       │
                                       ▼
              <container>/<prefix>/<table>/year=YYYY/month=MM/day=DD/<table>-<date>.parquet
```

## Tables exported

| Table | Cardinality | Strategy | Use case |
|---|---|---|---|
| `runs` | ~thousands/day | Incremental: `finished_at` in [yesterday 00:00, today 00:00) UTC | Availability %, MTTD, throughput |
| `test_results` | 10× `runs` | Incremental, same window | Latency, error analysis, regression spotting |
| `app_state` | ~hundreds | Full snapshot daily, tagged with `as_of_date` | Current-state dashboard, SLO tracking |
| `audit_log` | ~tens to hundreds | Incremental by `timestamp` | Compliance / change-management evidence |

Partitioning: `year=YYYY/month=MM/day=DD/` — Hive-style. Synapse partition
elimination prunes scans efficiently when filtering on date.

## Config schema

```yaml
analytics:
  enabled: true                       # master switch
  export_cron: "0 1 * * *"            # daily 01:00 UTC; standard cron, 0=Sunday
  tables: [runs, test_results, app_state, audit_log]   # default = all
  target:
    type: azure_blob                  # "azure_blob" | "local"
    # ── azure_blob fields ────────────────────────────────────────────────
    container: "synthetics"           # blob container name (must exist)
    prefix: "harness/"                # path prefix inside container
    # auth — pick exactly one:
    connection_string: "$AZURE_STORAGE_CONNECTION_STRING"     # simplest
    # account_url: "https://acme.blob.core.windows.net"       # + DefaultAzureCredential
    # account_url + sas_token: "?sv=..."                      # SAS URL
    # ── local fields ─────────────────────────────────────────────────────
    # path: "data/exports/"
```

## Auth: which path for which deployment

- **Harness on-prem, BI in Azure** — use `connection_string` from the storage
  account. Store it in the encrypted secrets store; reference as `$AZURE_STORAGE_CONNECTION_STRING`.
- **Harness in Azure (AKS, App Service, Container Apps)** — use `account_url`
  only, rely on `DefaultAzureCredential` which auto-discovers Managed Identity
  / workload identity. No secret in config.
- **Limited blob scope (different team owns the storage)** — use SAS URL
  scoped to the container with `Write/Add/Create` permissions.

The implementation uses `azure-storage-blob` which speaks both Blob and ADLS
Gen2 transparently — ADLS Gen2 with hierarchical namespace is the
recommended setup (matches Fabric Lakehouse and Synapse defaults).

## Files

| File | Change |
|---|---|
| `harness/analytics.py` | NEW — `ExportTarget` abstract, `LocalFilesystemTarget`, `AzureBlobTarget`, `build_target`, `export_all_tables`, schema converters |
| `harness/auto_scheduler.py` | Add `add_analytics_export_job(scheduler, …)` |
| `harness/config.py` | Validate `analytics:` section shape |
| `web/main.py` | Wire export job into `_start_scheduler()` |
| `requirements.txt` | Add `pyarrow`, `azure-storage-blob` (Azure SDK optional unless target.type == azure_blob) |
| `tests/test_analytics.py` | NEW — target factory, local-fs roundtrip, partition path |
| `tests/test_analytics_export.py` | NEW — end-to-end with mocked DB rows |
| `docs/guides/analytics.md` | NEW — BI team guide: connect Synapse / Fabric / Power BI / Databricks |
| `config.yaml` (example) | Sample `analytics:` block (commented-out by default) |

## Parquet schema (preserved types)

`harness/db.py` stores timestamps as ISO-8601 strings. The exporter parses
them back into `pyarrow.timestamp("ms", tz="UTC")` so downstream tools see
real timestamps, not strings.

```python
# runs.parquet
id:           string
app:          string
environment:  string
triggered_by: string
status:       string
started_at:   timestamp[ms, UTC]
finished_at:  timestamp[ms, UTC]
duration_ms:  int64   # finished_at - started_at, NULL if active

# test_results.parquet
id:           string
run_id:       string
app:          string
environment:  string
test_name:    string
status:       string  # 'pass' | 'fail' | 'error'
error_msg:    string  # nullable
duration_ms:  int64   # nullable
finished_at:  timestamp[ms, UTC]
step_count:   int32   # derived from step_log JSON
step_log:     string  # raw JSON (BI teams can json_parse() in queries)
screenshot:   string  # nullable, relative path

# app_state.parquet (daily snapshot)
app:          string
environment:  string
test_name:    string
state:        string
since:        timestamp[ms, UTC]
as_of_date:   date32   # the export date

# audit_log.parquet
id:           string
timestamp:    timestamp[ms, UTC]
user_id:      string
username:     string
action:       string
target:       string
details:      string
```

## Manual export endpoint (deferred)

A `POST /admin/exports/run` button (admin only) for one-off exports is a
natural follow-up — useful for backfills and for verifying the BI pipeline
end-to-end before turning on the schedule. Out of scope for this PR;
schedule-only for now.

## Risks + rollback

- **Azure outage** — exports fail silently (logged WARN); the harness keeps
  running. The next scheduled run picks up where it left off — and since
  the source data lives in SQLite, no analytics data is lost, just delayed.
- **pyarrow install size** — adds ~50 MB to the image. Acceptable for the
  data utility it unlocks. Lazy-import inside `harness/analytics.py` so the
  harness still boots if the dep is somehow missing in dev.
- **azure-storage-blob is heavy** — only imported when `target.type == "azure_blob"`.
  Local-fs target has zero extra runtime cost.
- **Partition over-write** — same-day re-exports overwrite the day's file by
  design (idempotent backfills). To preserve history, append a timestamp to
  the filename — not the default behavior.
- **Rollback** — `analytics.enabled: false` (or omit the section) silences
  the export job. The job never registers, no Azure calls, no perf impact.

## Stage-4 follow-up — edge runner

Captured here per the user's note: the harness currently runs all checks
from one host. For target apps behind network ACLs (jump boxes, private
endpoints, customer VPCs) we'll need a **distributed runner** model:

```
┌──────────────────────────┐
│ Harness control plane    │ (this codebase)
│ - app definitions        │
│ - results DB             │
│ - dashboard + alerts     │
└───────────┬──────────────┘
            │ (signed JWT, websocket / poll)
   ┌────────┴────────┐
   ▼                 ▼
┌──────────┐    ┌──────────┐
│ Runner   │    │ Runner   │
│ region A │    │ region B │  ← run on Azure Container Apps,
└──────────┘    └──────────┘    edge sites, customer VPCs
```

Shape (no commitments yet):
- New `runners` table: `id`, `name`, `last_seen_at`, `regions`, `pubkey`
- Tag-based dispatch: app YAML adds `run_on: [region-a, region-b]` or
  `tags: [restricted]` and the control plane assigns matching runners
- Runner is a thin Python binary that polls `/api/runner/next-job`, runs
  the test locally, POSTs results back to `/api/runner/results`
- Auth: short-lived JWT from `/api/runner/register` (mTLS optional)
- Health: runner heartbeat → control-plane page in `/admin/runners`

This is **stage 4** in the platform progression and is a multi-week project
(easily 1–2k LOC + new auth + new UI). Deferred — call it out in the next
roadmap review.

## Deployment plan

1. Implement + tests
2. Build CI green (pyarrow has Python 3.11 wheels; azure-storage-blob is pure Python)
3. Hand BI: storage account name, container name, expected partition layout
4. BI creates external tables / shortcut tables, validates against a backfill
5. Flip `analytics.enabled: true` in production config
6. First scheduled run lands the next day
