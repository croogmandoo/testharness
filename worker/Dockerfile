# Remote worker image. Builds on the same base + Playwright/Chromium layer as
# the central harness so browser tests work identically. Difference is purely
# the CMD — this image runs the worker loop instead of the FastAPI server.
#
# Build:
#   docker build -f worker/Dockerfile -t ghcr.io/croogmandoo/testharness-worker:latest .
#
# Run:
#   docker run -d --name harness-worker --restart unless-stopped \
#     -e HARNESS_URL=https://harness.example.com \
#     -e HARNESS_API_KEY=hth_... \
#     -e HARNESS_WORKER_NAME=eu-worker-1 \
#     -e HARNESS_WORKER_TAGS=eu,media \
#     ghcr.io/croogmandoo/testharness-worker:latest

FROM python:3.11-slim-bookworm

# System deps required by Playwright's Chromium + gosu for the entrypoint.
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

# Non-root runtime user; same uid as the central harness for consistency.
RUN groupadd --system --gid 10001 harness \
    && useradd --system --uid 10001 --gid harness \
               --home-dir /app --no-create-home --shell /usr/sbin/nologin harness

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install chromium --with-deps \
    && chmod -R a+rX /opt/playwright

COPY --chown=harness:harness harness/ ./harness/
COPY --chown=harness:harness worker/ ./worker/

# Worker has minimal writable state — but if HARNESS_SCREENSHOT_DIR is used
# downstream (v2), make sure /app/data is writable.
RUN install -d -o harness -g harness /app/data
USER harness

# Stripped-down entrypoint: just drops privileges (already are 10001 here).
# No bind-mount permission fixing needed because the worker doesn't normally
# have host volumes.
CMD ["python", "-m", "worker.worker"]
