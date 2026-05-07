FROM python:3.11-slim-bookworm

# System deps required by Playwright's Chromium
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
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.  UID 10001 stays out of system-account range.
RUN groupadd --system --gid 10001 harness \
    && useradd --system --uid 10001 --gid harness \
               --home-dir /app --no-create-home --shell /usr/sbin/nologin harness

WORKDIR /app

# Install Python deps as root (writes into site-packages), then chown later.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Playwright browsers are downloaded into the user's home — install as harness
# so the runtime user owns them and doesn't try to re-download on first run.
RUN install -d -o harness -g harness /home/harness \
    && chown -R harness:harness /home/harness
USER harness
RUN playwright install chromium --with-deps 2>/dev/null || playwright install chromium
USER root

COPY --chown=harness:harness . .

# Writable runtime dirs (data dir + screenshots) — must be owned by harness.
RUN install -d -o harness -g harness /app/data /app/data/screenshots

USER harness

EXPOSE 9552

# Healthcheck hits the existing /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://127.0.0.1:9552/health || exit 1

CMD ["python", "-m", "web.main"]
