# Alerting

The harness sends alerts on three classes of event:

| Event | Trigger | Channels |
|---|---|---|
| **FAIL** | A test transitions from passing → failing | Teams · Slack · Discord · email · webhook |
| **RESOLVE** | A test transitions from failing → passing | same as above |
| **STALE** | An app's most-recent completed run is older than the staleness threshold | same as above |

Alerts are state-based, not run-based.  The harness only fires on
*transitions*, so a flapping test won't spam you.

---

## Channels

Configure under `alerts:` in `config.yaml`.  Every channel is independent
— enable as many as you want.

```yaml
alerts:
  teams:    { webhook_url: "$TEAMS_WEBHOOK_URL" }
  slack:    { webhook_url: "$SLACK_WEBHOOK_URL" }
  discord:  { webhook_url: "$DISCORD_WEBHOOK_URL" }
  email:
    smtp_host: "mail.example.com"
    smtp_port: 587
    from:     "harness@example.com"
    to:       ["ops@example.com"]
    username: "$SMTP_USERNAME"
    password: "$SMTP_PASSWORD"
  webhook:
    url: "$GENERIC_WEBHOOK_URL"
    format: "pagerduty"            # "pagerduty" | "opsgenie" | omit for standard JSON
    routing_key: "$PD_ROUTING_KEY" # used with format: "pagerduty"
    secret: "$WEBHOOK_SECRET"      # optional HMAC-SHA256 signature in X-Harness-Signature
    headers:
      Authorization: "Bearer $TOKEN"
```

Multiple webhooks can also be configured as a list:
```yaml
alerts:
  webhook:
    - url: "https://events.pagerduty.com/v2/enqueue"
      format: "pagerduty"
      routing_key: "$PD_KEY"
    - url: "https://api.opsgenie.com/v2/alerts"
      format: "opsgenie"
      headers:
        Authorization: "GenieKey $OPSGENIE_KEY"
```

### Generic & Custom Webhook Payloads

By default, the webhook receives a structured JSON POST for completed runs and incident state transitions:

```json
{
  "event": "run_complete",
  "run_id": "01HZ...",
  "app": "My API",
  "environment": "production",
  "status": "complete",
  "triggered_by": "schedule",
  "finished_at": "2026-05-07T18:42:00+00:00",
  "tests": [
    { "name": "Health check", "status": "pass", "duration_ms": 142 },
    { "name": "Login flow",   "status": "fail", "duration_ms": 1200 }
  ]
}
```

#### Custom Jinja2 Payload Templates
You can customize the outgoing webhook JSON structure to match any internal event schema using `template`:

```yaml
alerts:
  webhook:
    url: "https://events.internal.corp/synthetics"
    headers:
      X-Service-Name: "testharness"
    template: |
      {
        "event_type": "SYNTHETIC_ALERT",
        "service": "{{ app }}",
        "env": "{{ environment }}",
        "status": "{{ status }}",
        "run_id": "{{ run_id }}"
      }
```

#### Incident Alert Webhook Dispatch
Incident alerts (`FAIL`, `RESOLVE`, `STALE`) also automatically dispatch to configured `alerts.webhook` endpoints with state metadata (`alert_type`, `app`, `environment`, `test_name`, `error_msg`, `text`).

---

## Maintenance windows

Suppress alert *dispatch* during planned downtime.  Run records,
dashboards, audit log, and metrics still see what happens — only the
external notifications are silenced.

### Recurring (cron-based)

```yaml
alerts:
  maintenance_windows:
    - name: "Weekly maintenance"
      cron: "0 2 * * 6"             # standard cron: Sat 02:00 UTC
      duration_minutes: 120         # window stays open 2 hours
```

### Fixed range

```yaml
alerts:
  maintenance_windows:
    - name: "Holiday freeze"
      start: "2026-12-23T00:00:00Z"
      end:   "2026-12-27T23:59:59Z"
```

The two flavors can be mixed in the same list.

> **Cron note:** Standard cron uses `0=Sunday` for the day-of-week field.
> APScheduler internally uses `0=Monday`; the harness translates so you
> always write standard cron.  `0 2 * * 6` is Saturday 02:00 UTC.

---

## Stale-run alerts

The harness checks once an hour whether each `(app, environment)` has had
a completed run within the staleness threshold.  If not, it dispatches a
STALE alert.

### Defaults

| Knob | Default | What it controls |
|---|---|---|
| `alerts.stale_threshold_minutes` | `1440` (24h) | Run age after which the app is considered stale |
| `alerts.stale_realert_hours` | `24` | Minimum gap between repeat alerts for the same app/env |
| `alerts.stale_check_enabled` | `true` | Master switch |

Apps with **zero run history** are skipped — we don't alert on apps you
haven't started using yet.

### How dedup works

When a STALE alert fires, the harness records `(app, env, now)` in the
`stale_alerts` table.  The next hourly check skips this pair until
`stale_realert_hours` has elapsed.

When a fresh run completes successfully, `db.clear_stale_alert(app, env)`
removes the dedup row, so the next time the app goes silent you get
notified again immediately instead of waiting another `realert` window.

### Maintenance + staleness

Stale-run alerts are also gated by maintenance windows.  Don't expect
3am pages from "no recent runs" if the harness itself is in scheduled
maintenance.

---

## Alert message format

Plain-text payload across all channels:

```
[FAIL] My API (production) — Login flow failed

Error: Expected status 200, got 503
```

```
[RESOLVED] My API (production) — Login flow is now passing
```

```
[STALE] My API (production) — no run in 28h, threshold 24h

Details: last run finished at 2026-05-06T14:00:00+00:00
```

---

## Disabling alerts entirely

Comment out every channel under `alerts:`.  The runner will dispatch to
nothing.  State transitions are still recorded in the database and audit
log, so you can review them in `/admin/audit` or via SQL.
