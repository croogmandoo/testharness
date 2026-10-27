# Running Ephemeral Test Harness Containers in Azure DevOps Pipelines

This guide demonstrates how to run **Testharness** as an ephemeral container directly inside Azure DevOps build agents. This pattern is ideal for **Pull Request (PR) validation**, pre-merge smoke testing, and verifying local preview/ephemeral containers before promoting code.

---

## 1. Overview & Architecture

Instead of connecting to an existing standalone Testharness server, Azure DevOps runs the official Docker image (`ghcr.io/croogmandoo/testharness:latest`) as a sidecar or runner step:

```mermaid
flowchart LR
    A[Azure DevOps Agent] --> B[Spin up Preview App Container]
    A --> C[Spin up Testharness Container]
    C -->|Run HTTP / API / Browser Journey| B
    C --> D[Capture Screenshots & Diff Artifacts]
    D --> E[PublishPipelineArtifact@1 to Azure DevOps]
```

**Benefits:**
- **Zero Infrastructure Footprint**: No long-running server required; environments are created and destroyed per pipeline run.
- **Full Playwright Support**: The official container ships with headless Chromium and all required OS libraries pre-configured.
- **PR Gating**: Block merging if PR visual regressions exceed thresholds or API assertions fail.

---

## 2. Docker Compose Pattern in Azure Pipelines

The most reliable way to test multi-container apps in Azure Pipelines is with Docker Compose.

### Directory Structure in your repository:
```text
your-repo/
├── azure-pipelines.yml
├── docker-compose.test.yml
└── test-definitions/
    └── pr-smoke.yaml
```

### `test-definitions/pr-smoke.yaml`:
```yaml
app: pr-preview
url: http://app:8080
tests:
  - name: "Health check"
    type: availability
    expect_status: 200

  - name: "Login & Dashboard Flow"
    type: browser
    steps:
      - navigate: /login
      - fill:
          field: "#username"
          value: "testuser"
      - fill:
          field: "#password"
          value: "secret123"
      - click: "button[type=submit]"
      - wait_for_selector: ".dashboard-header"
      - assert_text: "Welcome"
      - screenshot: null
```

### `docker-compose.test.yml`:
```yaml
version: "3.8"

services:
  # 1. Your application under test
  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      - NODE_ENV=test

  # 2. Ephemeral Testharness instance
  testharness:
    image: ghcr.io/croogmandoo/testharness:latest
    depends_on:
      - app
    volumes:
      - ./test-definitions:/app/apps:ro
      - ./harness-data:/app/data
    environment:
      - HARNESS_PORT=9552
      - TESTHARNESS_SECRET_KEY=1111111111111111111111111111111111111111111=
```

---

## 3. Azure DevOps Pipeline YAML (`azure-pipelines.yml`)

```yaml
trigger:
  branches:
    include:
      - main
      - master
pr:
  branches:
    include:
      - main
      - master

pool:
  vmImage: "ubuntu-latest"

jobs:
  - job: EphemeralBrowserTesting
    displayName: "Run Ephemeral Playwright Tests"
    steps:
      - checkout: self

      - bash: |
          mkdir -p harness-data/screenshots
          docker compose -f docker-compose.test.yml up -d --build
          
          echo "Waiting for app and testharness to be healthy..."
          timeout 60 bash -c 'until curl -fsSL http://localhost:9552/health; do sleep 2; done'
        displayName: "Start Test Containers"

      - bash: |
          set -euo pipefail
          
          echo "Triggering test run for 'pr-preview'..."
          RESPONSE=$(curl -fsSL -X POST http://localhost:9552/api/runs \
            -H "Content-Type: application/json" \
            -d '{"app": "pr-preview", "environment": "default", "triggered_by": "azure-pr-pipeline"}')
          
          RUN_ID=$(echo "$RESPONSE" | python3 -c "import sys, json; print(json.load(sys.stdin).get('run_id', ''))")
          echo "Run ID: $RUN_ID"

          # Poll until execution finishes
          while true; do
            STATUS=$(curl -fsSL http://localhost:9552/api/runs/$RUN_ID | python3 -c "import sys, json; print(json.load(sys.stdin).get('status', ''))")
            if [ "$STATUS" = "complete" ] || [ "$STATUS" = "error" ]; then
              break
            fi
            sleep 3
          done

          # Fetch results and check pass/fail
          RESULTS=$(curl -fsSL http://localhost:9552/api/results/$RUN_ID)
          echo "$RESULTS" > harness-data/results.json

          # Export PDF summary report
          curl -fsSL "http://localhost:9552/api/runs/$RUN_ID/export?format=pdf" -o harness-data/test-report.pdf || true

          # Check if any test failed
          python3 -c '
          import sys, json
          with open("harness-data/results.json") as f:
              results = json.load(f)
          failures = [r for r in results if r.get("status") != "pass"]
          if failures:
              print(f"FAILED: {len(failures)} tests failed:")
              for f in failures:
                  print(f"  - {f.get(\"test_name\")}: {f.get(\"error_msg\")}")
              sys.exit(1)
          print(f"SUCCESS: All {len(results)} tests passed!")
          '
        displayName: "Execute Synthetic Test Run & Check Results"

      - task: PublishPipelineArtifact@1
        displayName: "Publish Screenshots and PDF Report"
        condition: always()
        inputs:
          targetPath: "harness-data"
          artifact: "testharness-execution-artifacts"
          publishLocation: "pipeline"

      - bash: |
          docker compose -f docker-compose.test.yml down -v
        displayName: "Teardown Containers"
        condition: always()
```

---

## 4. Capturing Visual Diffs & Screenshots in Artifacts

When a browser test step executes `screenshot: null`, Testharness stores screenshots and visual diffs in `data/screenshots/`.

By mapping `./harness-data:/app/data` and running `PublishPipelineArtifact@1`, all captured assets are saved directly into Azure DevOps:
- **Baseline screenshots**: `harness-data/screenshots/<test-slug>.png`
- **Candidate screenshots**: `harness-data/screenshots/<test-slug>-candidate.png`
- **High-contrast magenta diff heatmap**: `harness-data/screenshots/<test-slug>-diff.png`
- **PDF Report**: `harness-data/test-report.pdf`

Engineers reviewing the Pull Request in Azure DevOps can download the artifact zip from the run summary page to inspect pixel diffs without having to reproduce the test locally.
