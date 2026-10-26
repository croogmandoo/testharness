FROM python:3.11-slim-bookworm

# System deps required by Playwright's Chromium, plus `gosu` for the
# entrypoint's privilege drop.
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxshmfence1 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.  UID 10001 stays out of system-account range.
RUN groupadd --system --gid 10001 harness \
    && useradd --system --uid 10001 --gid harness \
               --home-dir /app --no-create-home --shell /usr/sbin/nologin harness

WORKDIR /app

# Install Python deps as root (writes into site-packages), then install
# Playwright browsers to a shared, world-readable location.  Doing the
# install as root means apt deps via --with-deps work; the path lets the
# runtime harness user execute the browser without re-downloading.
# Install from the resolved lock when present (same pattern as ci.yml) so
# the image gets the audited, pinned dependency set; fall back to
# requirements.txt if the lock has been removed. The trailing glob keeps the
# COPY valid when requirements.lock is absent.
COPY requirements.txt requirements.lock* ./
RUN if [ -f requirements.lock ]; then \
        pip install --no-cache-dir -r requirements.lock; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install chromium --with-deps \
    && chmod -R a+rX /opt/playwright

COPY --chown=harness:harness . .

# Writable runtime dirs (data dir + screenshots) — must be owned by harness.
RUN install -d -o harness -g harness /app/data /app/data/screenshots

# entrypoint.sh runs as root, chowns bind-mounted volumes to the harness
# uid (10001), then `gosu`-drops privileges before exec'ing CMD. If the
# operator pins a uid via `user:` in compose, the script detects the
# non-root start and skips the chown (operator owns permissions then).
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

# NOTE: we intentionally do NOT set `USER harness` here. The entrypoint
# starts as root so it can fix bind-mount ownership, then drops to
# uid 10001 before exec'ing the app. The application itself never runs
# as root.

EXPOSE 9552

# Healthcheck hits the existing /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:9552/health || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["python", "-m", "web.main"]
