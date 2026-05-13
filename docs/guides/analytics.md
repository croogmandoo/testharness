# Analytics Export — Azure / BI Guide

The harness writes a **daily Parquet snapshot** of its telemetry to a
location of your choosing: a local directory, or an **Azure Blob /
ADLS Gen2 container**. From there your BI / DIO team attaches external
tables in Synapse, shortcut tables in Fabric, or direct Parquet readers
in Power BI / Databricks. The harness produces the data; downstream
tools own reporting.

Files land in the **Hive partition layout** every Azure analytics tool
expects natively:

```
<container>/<prefix>/<table>/year=YYYY/month=MM/day=DD/<table>-YYYY-MM-DD.parquet
```

Example:

```
synthetics/harness/runs/year=2026/month=05/day=12/runs-2026-05-12.parquet
synthetics/harness/test_results/year=2026/month=05/day=12/test_results-2026-05-12.parquet
synthetics/harness/app_state/year=2026/month=05/day=12/app_state-2026-05-12.parquet
synthetics/harness/audit_log/year=2026/month=05/day=12/audit_log-2026-05-12.parquet
```

---

## Enable the export

Add an `analytics:` section to `config.yaml`:

```yaml
analytics:
  enabled: true                          # master switch
  export_cron: "0 1 * * *"               # daily 01:00 UTC (standard cron, 0=Sun)
  tables: [runs, test_results, app_state, audit_log]   # default = all

  target:
    type: azure_blob
    container: synthetics                # blob container name (must already exist)
    prefix: harness/                     # path prefix inside the container

    # ── Pick exactly ONE auth method ────────────────────────────────────
    connection_string: "$AZURE_STORAGE_CONNECTION_STRING"
    # account_url: "https://acme.blob.core.windows.net"   # + DefaultAzureCredential
    # account_url + sas_token: "?sv=...&sig=..."          # SAS URL
```

Store `AZURE_STORAGE_CONNECTION_STRING` via the harness's encrypted secret
store (`/secrets`) — it's then resolved into config at startup without
ever appearing in `os.environ`.

The job registers on the next harness restart. Confirm via the logs:

```
auto_scheduler: analytics export scheduled '0 1 * * *'
```

---

## Auth: pick the right path for your deployment

| Scenario | Recommended auth |
|---|---|
| Harness on-prem, storage in Azure | `connection_string` (via Secrets store) |
| Harness running in Azure (AKS, App Service, Container Apps) | `account_url` + DefaultAzureCredential (Managed Identity) |
| BI team owns the storage account and only wants to grant write access | `account_url` + `sas_token` scoped to the container, `Write+Add+Create` perms |
| Local-only / on-prem-only | `target.type: local`, `target.path: /var/lib/harness/exports` |

The Azure SDK speaks both classic Blob and ADLS Gen2 transparently —
ADLS Gen2 (hierarchical namespace enabled) is recommended because it
matches Fabric Lakehouse defaults and gives you proper directory
semantics for the Hive partitions.

---

## Connecting from Azure tools

### Synapse — serverless SQL pool

```sql
-- Create an external table over the runs partition
CREATE EXTERNAL TABLE dbo.harness_runs (
    id            VARCHAR(64),
    app           VARCHAR(256),
    environment   VARCHAR(64),
    triggered_by  VARCHAR(64),
    status        VARCHAR(32),
    started_at    DATETIME2,
    finished_at   DATETIME2,
    duration_ms   BIGINT,
    year          INT,
    month         INT,
    day           INT
)
WITH (
    LOCATION  = 'harness/runs/**',
    DATA_SOURCE = analytics_storage,
    FILE_FORMAT = parquet_snappy
);

-- Synapse infers `year`/`month`/`day` from the partition path automatically.
-- Query example: SLO numerator for the last 30 days, by app
SELECT
    app,
    environment,
    SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS complete_runs,
    COUNT(*) AS total_runs
FROM dbo.harness_runs
WHERE CONCAT(year, '-', FORMAT(month, '00'), '-', FORMAT(day, '00')) >=
      FORMAT(DATEADD(day, -30, GETUTCDATE()), 'yyyy-MM-dd')
GROUP BY app, environment;
```

### Fabric Lakehouse — shortcut

1. Open your Lakehouse → **New shortcut** → **Azure Data Lake Storage Gen2**
2. Point at `https://<account>.dfs.core.windows.net/<container>/<prefix>`
3. Each top-level folder (`runs`, `test_results`, `app_state`, `audit_log`)
   appears as a table; partitions are auto-detected
4. Use the SQL endpoint to query, or load into a semantic model for Power BI

### Power BI — direct connect

- Data source: **Azure Data Lake Storage Gen2**
- URL: `https://<account>.dfs.core.windows.net/<container>/<prefix>/<table>/`
- File format: Parquet
- Power Query auto-discovers the Hive partition columns

### Databricks

```python
df_runs = spark.read.parquet(
    "abfss://synthetics@acme.dfs.core.windows.net/harness/runs/"
)
df_runs.createOrReplaceTempView("harness_runs")
```

---

## Schema reference

### `runs.parquet`

| column | type | notes |
|---|---|---|
| `id` | string | uuid4 |
| `app` | string | matches `app:` field in app YAML |
| `environment` | string | e.g. `production`, `staging` |
| `triggered_by` | string | `ui` \| `api` \| `schedule` \| custom |
| `status` | string | `pending` \| `running` \| `complete` |
| `started_at` | timestamp[ms, UTC] | |
| `finished_at` | timestamp[ms, UTC] | null while active |
| `duration_ms` | int64 | `finished_at - started_at`, null while active |

Partition: only `status='complete'`-eligible runs (rows where `finished_at`
falls in the export day). Active runs are picked up by the next day's
export once they complete.

### `test_results.parquet`

| column | type | notes |
|---|---|---|
| `id` | string | uuid4 |
| `run_id` | string | FK → `runs.id` |
| `app` | string | denormalized from `runs.app` |
| `environment` | string | denormalized |
| `test_name` | string | from YAML |
| `status` | string | `pass` \| `fail` \| `error` |
| `error_msg` | string | nullable; populated on fail/error |
| `duration_ms` | int64 | per-test wall time |
| `finished_at` | timestamp[ms, UTC] | |
| `step_count` | int32 | derived from `step_log` JSON length |
| `step_log` | string | raw JSON — use `JSON_VALUE(step_log, '$[0].step')` to drill in |
| `screenshot` | string | nullable; relative path to image (not in this export) |

### `app_state.parquet` — daily snapshot

| column | type | notes |
|---|---|---|
| `app` | string | |
| `environment` | string | |
| `test_name` | string | |
| `state` | string | `passing` \| `failing` \| `unknown` |
| `since` | timestamp[ms, UTC] | when the test entered its current state |
| `as_of_date` | date32 | partition date — useful for SCD-style queries |

This is a **full snapshot**, not incremental. Each day's partition
contains every `(app, environment, test_name)` row. Build SLO/availability
reports by `GROUP BY app, environment, as_of_date`.

### `audit_log.parquet`

| column | type | notes |
|---|---|---|
| `id` | string | |
| `timestamp` | timestamp[ms, UTC] | |
| `user_id` | string | nullable |
| `username` | string | denormalized snapshot |
| `action` | string | e.g. `user.create`, `secret.upsert`, `api_key.revoke` |
| `target` | string | what was acted on |
| `details` | string | free-form |

Useful for compliance / change-management evidence and for joining
"who triggered this run at this time".

---

## Backfills

Day-level partitions are idempotent — re-exporting the same day overwrites
the file. To backfill historically while testing the BI integration,
trigger the export from a Python shell on the harness host:

```bash
docker exec harness python -c "
from harness.db import Database
from harness.analytics import build_target, export_all_tables
from harness.config import load_config
from datetime import date, timedelta

cfg = load_config('config.yaml')
db = Database('data/harness.db')
target = build_target(cfg['analytics']['target'])

# Backfill the last 30 days
today = date.today()
for i in range(1, 31):
    d = today - timedelta(days=i)
    results = export_all_tables(db, target, partition_date=d)
    print(d, results)
"
```

---

## Operations

- **Storage costs:** Parquet+Snappy compresses test_results ~10×. A harness
  running 20 apps × ~50 tests every 15 min produces roughly 5–10 MB/day.
  Tier the container to **Hot** for the most-recent ~90 days, **Cool** for
  90–365, **Archive** beyond. Synapse can still read Cool transparently
  (small latency cost); Archive requires rehydration.
- **Retention:** the harness's own `retention_days` setting only affects
  the SQLite DB. The Parquet exports persist independently — BI team
  controls their own lifecycle policy on the blob container.
- **Monitoring the export itself:** the `analytics::export` APScheduler
  job logs at INFO on success, EXCEPTION on failure. Surface via your
  log aggregator. The `/admin/audit` log does **not** record exports
  (it's only for user-driven admin actions); we can wire it in if you
  want exports auditable.

---

## What's NOT in the export today (yet)

- **Screenshots** — they live on disk under `data/screenshots/`. Not
  exported. If BI wants them for visual regression review, the cleanest
  approach is a sidecar blob upload triggered from `harness/runner.py`.
- **Real-time stream** — for sub-day latency, Option C (event bus —
  Kafka/EventHub/Service Bus) is the next stage. The daily export is the
  warehouse-of-record; events would feed dashboards/alerts that can't wait.
- **Aggregates / pre-computed SLO tables** — current export is raw rows
  only. BI should compute aggregates in their warehouse so they own the
  definitions (window, exclusions, etc.). If you want the harness to
  pre-compute SLOs, that's a separate roadmap item.
