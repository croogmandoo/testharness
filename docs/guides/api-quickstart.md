# API Quickstart

The Testing Harness exposes a REST API that accepts API key authentication — useful for CI pipelines, scripts, and external tooling.

## 1. Generate an API Key

1. Sign in to the harness web UI.
2. Click **API Keys** in the top navigation.
3. Enter a name (e.g. `CI pipeline`) and choose an expiry.
4. Click **Create Key**.
5. **Copy the key immediately** — it is shown only once. It starts with `hth_`.

## 2. Authenticate

Every request needs either:

```
Authorization: Bearer hth_your_key_here
```

or:

```
X-API-Key: hth_your_key_here
```

Both headers work on all `/api/*` endpoints.

## 3. List Apps

```bash
curl -s \
  -H "Authorization: Bearer hth_your_key_here" \
  http://localhost:9552/api/apps | jq .
```

Response:

```json
[
  {
    "name": "Sonarr",
    "environments": ["production", "staging"],
    "last_run_id": "abc123",
    "summary": { "pass": 2, "fail": 0, "error": 0 }
  }
]
```

## 4. Trigger a Test Run

```bash
curl -s -X POST \
  -H "Authorization: Bearer hth_your_key_here" \
  -H "Content-Type: application/json" \
  -d '{"app": "Sonarr", "environment": "production"}' \
  http://localhost:9552/api/runs | jq .
```

Response:

```json
{ "run_id": "abc123def456" }
```

## 5. Poll for Results

```bash
curl -s \
  -H "Authorization: Bearer hth_your_key_here" \
  http://localhost:9552/api/runs/abc123def456 | jq .
```

Response when complete:

```json
{
  "id": "abc123def456",
  "app": "Sonarr",
  "environment": "production",
  "status": "complete",
  "results": [
    { "test_name": "login-flow", "status": "pass", "duration_ms": 3201 },
    { "test_name": "homepage-loads", "status": "pass", "duration_ms": 812 }
  ]
}
```

Poll until `status` is `"complete"` or `"error"`. A run in progress returns `"running"`.

## 6. Revoking a Key

Go to **API Keys** in the UI and click **Revoke** next to the key. Revoked keys return `401` immediately.

## Full API Reference

See [`../reference/api.md`](../reference/api.md) for all endpoints, query parameters, and response schemas.
