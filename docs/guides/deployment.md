# Deploying with Docker

The harness ships with a `Dockerfile` and `docker-compose.yml` that let you run it on any machine with Docker installed. The container includes Playwright's Chromium browser so browser tests work out of the box.

---

## Prerequisites

- Docker 24+ and Docker Compose v2 (`docker compose` not `docker-compose`)
- A `.env` file in the project root with any secrets your app YAML files reference

---

## Quick Start

```bash
# 1. Clone the repo
git clone <repo-url>
cd webtestingharness

# 2. Create your .env file
cp .env.example .env   # or create it manually
# Edit .env — add SLACK_WEBHOOK_URL, ADMIN_PASSWORD, etc.

# 3. Start the harness
docker compose up -d

# 4. Check it's healthy
curl http://localhost:9552/health
# → {"status": "ok"}
```

Open `http://localhost:9552` in your browser. On first run you'll be prompted to create an admin account.

---

## File Layout

```
.
├── Dockerfile
├── docker-compose.yml
├── .env                  # secrets — never commit this
├── apps/                 # your app YAML definitions (bind-mounted)
├── config.yaml           # harness config (bind-mounted)
└── data/                 # SQLite DB, screenshots, CA bundle (bind-mounted)
    └── screenshots/
```

The three bind-mounts mean **all state lives outside the container**. You can destroy and recreate the container without losing data.

---

## Configuration

### `config.yaml`

The config file is bind-mounted from `./config.yaml` into the container. Create it in the project root before starting:

```yaml
default_environment: production

environments:
  production:
    label: Production
  staging:
    label: Staging

browser:
  headless: true
  timeout_ms: 30000

alerts:
  slack:
    webhook_url: "$SLACK_WEBHOOK_URL"
  discord:
    webhook_url: "$DISCORD_WEBHOOK_URL"
  webhook:
    url: "$WEBHOOK_URL"
    secret: "$WEBHOOK_SECRET"   # optional HMAC-SHA256 signing key

auth:
  session_hours: 8
  secure_cookie: true           # set true when behind HTTPS
  github:
    client_id: "$GITHUB_CLIENT_ID"
    client_secret: "$GITHUB_CLIENT_SECRET"
    default_role: read_only
```

### `.env` file

Environment variables are passed into the container via `env_file: .env` in `docker-compose.yml`. Any `$VAR` reference in `config.yaml` or app YAML files is resolved from this file.

```bash
# .env
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
WEBHOOK_URL=https://ci.example.com/harness-hook
WEBHOOK_SECRET=mysupersecret

GITHUB_CLIENT_ID=abc123
GITHUB_CLIENT_SECRET=xyz456

# App-specific secrets
SONARR_PASSWORD=admin
RADARR_API_KEY=...

# Per-environment secrets (optional — falls back to the base var if not set)
SONARR_PASSWORD#staging=staging-pass
```

---

## Port

The harness listens on **port 9552** inside the container. The `docker-compose.yml` maps it to the same port on the host:

```yaml
ports:
  - "9552:9552"
```

Change the left side to use a different host port:

```yaml
ports:
  - "8080:9552"
```

---

## Healthcheck

The compose file includes a healthcheck that polls `/health` every 30 seconds:

```yaml
healthcheck:
  test: ["CMD", "python", "-c",
         "import urllib.request; urllib.request.urlopen('http://localhost:9552/health')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 15s
```

Check health status:

```bash
docker compose ps
```

---

## Common Commands

```bash
# Start in background
docker compose up -d

# View logs (follow)
docker compose logs -f

# Stop
docker compose down

# Rebuild after code changes
docker compose build
docker compose up -d

# Open a shell inside the container
docker compose exec app bash
```

---

## Reverse Proxy (HTTPS)

For production, put the harness behind nginx or Caddy. Set `auth.secure_cookie: true` in `config.yaml` when serving over HTTPS so the session cookie is marked `Secure`.

**Example nginx snippet:**

```nginx
server {
    listen 443 ssl;
    server_name harness.example.com;

    # ... ssl_certificate, ssl_certificate_key ...

    location / {
        proxy_pass         http://localhost:9552;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-Proto https;
    }
}
```

---

## Upgrading

```bash
git pull
docker compose build
docker compose up -d
```

The SQLite database in `data/` is backward-compatible — the harness runs schema migrations automatically on startup.

---

## Troubleshooting

**Container exits immediately:**
```bash
docker compose logs app
```
Common causes: missing `config.yaml`, malformed YAML, or a required env var not set.

**`/health` returns connection refused:**
The container may still be starting. Browser tests start Playwright on the first run, which takes a few seconds. Wait for the healthcheck to pass (`docker compose ps`).

**Browser tests fail inside Docker:**
All required Chromium system dependencies are installed in the `Dockerfile`. If you add custom Playwright flags, make sure `headless: true` is set in `config.yaml` — a display server is not available inside the container.

**Screenshots not saving:**
Ensure `./data` is writable by the process running inside the container. The container runs as root by default; this is rarely an issue with the default setup.
