# Automating Microsoft Power Apps & Importing Microsoft Edge Sessions

This guide explains how to monitor **Microsoft Power Apps (Canvas & Model-Driven Apps)** and import authenticated **Microsoft Edge / Entra ID (Azure AD)** sessions into **Web Testing Harness (`testharness`)**.

---

## 1. Architectural Challenges with Power Apps

Automating Power Apps in headless browsers presents two unique enterprise challenges:

1. **Enterprise Identity & MFA (Entra ID / Microsoft 365)**:
   Power Apps requires authentication via `login.microsoftonline.com`. In corporate environments, accounts are protected by Multi-Factor Authentication (MFA), Conditional Access policies, or Intune device compliance. Headless browser runs cannot easily pass interactive MFA prompts on every execution.
2. **The Canvas App IFrame Sandbox (`fullscreen-app-host`)**:
   Power Apps Player runs inside a nested iframe named `fullscreen-app-host` (`iframe[name="fullscreen-app-host"]`). Standard top-level DOM queries fail because controls exist inside this isolated frame.
3. **Dynamic Canvas DOM Structure**:
   Standard element IDs in Power Apps are auto-generated and fragile across app publishes. However, Power Apps tags all controls with their authoring name via the `data-control-name` attribute (e.g. `[data-control-name="SubmitButton"]`).

`testharness` solves all three challenges with **Edge Session Import**, **Automatic PowerApps Frame Fallback**, and **Native `data-control-name` Control Recognition**.

---

## 2. Capturing & Importing Microsoft Edge Sessions

By capturing an authenticated session from Microsoft Edge, `testharness` can load Power Apps with single sign-on (SSO) active, bypassing MFA and repeated login forms.

### Method A: Via the Web UI (Recommended)

1. Open **Microsoft Edge** on your workstation and log into your Power Apps portal (e.g. `https://apps.powerapps.com` or `https://make.powerapps.com`).
2. Export the active cookies using a browser extension (such as **Cookie-Editor** or **EditThisCookie**) by clicking **Export -> JSON**.
3. In the `testharness` dashboard, open your Application Form (`/apps/{name}/edit`).
4. Under the Browser test configuration, click **"🔑 Import Edge / M365 Session"**.
5. Paste the exported JSON or cookies into the dialog and click **Save Session State**.
6. The system automatically normalizes the cookies into a Playwright `storage_state` and saves it to:
   ```text
   data/storage_states/{app}_{environment}.json
   ```

### Method B: Via the REST API

You can programmatically push session states using the REST API:

```bash
curl -X POST "http://localhost:9552/api/storage-states/powerapp-crm/production" \
  -H "Authorization: Bearer <ADMIN_OR_RUNNER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "storage_state": [
      {
        "name": "ESTSAUTH",
        "value": "<token>",
        "domain": ".login.microsoftonline.com",
        "path": "/",
        "secure": true,
        "httpOnly": true
      },
      {
        "name": "SignInStateCookie",
        "value": "<token>",
        "domain": ".apps.powerapps.com",
        "path": "/",
        "secure": true
      }
    ]
  }'
```

### Method C: Via Playwright CLI

You can launch Edge interactively to log in and save state directly:

```bash
.venv/bin/playwright codegen --channel=msedge --save-storage=data/storage_states/my-powerapp_production.json https://apps.powerapps.com
```

---

## 3. Authoring a Power Apps Test

When creating an app definition YAML in `apps/`, configure the browser test:

```yaml
app: powerapp-inventory
name: "Inventory Management PowerApp"
environments:
  production: "https://apps.powerapps.com/play/e/default-tenant-id/a/app-guid?tenantId=tenant-guid"

tests:
  - name: "Inventory Lookup Journey"
    type: browser
    browser_channel: "msedge"  # optional: uses Edge engine if installed
    steps:
      # 1. Navigate to the app (uses imported storage_state automatically)
      - navigate: "/"

      # 2. Wait for PowerApps Player & Canvas frame to compile and dismiss splash screen
      - wait_for_powerapps:
          timeout_ms: 30000

      # 3. Fill search input (automatically locates inside fullscreen-app-host frame)
      - fill:
          field: '[data-control-name="SearchInputBox"] input'
          value: "SKU-9921"

      # 4. Click Search Button
      - click: '[data-control-name="SearchButton"] [role="button"]'

      # 5. Assert result appears inside the canvas
      - assert_text: "In Stock - Warehouse A"

      # 6. Optional: Capture screenshot of the loaded canvas
      - screenshot: null
```

### Key Power Apps Test Steps

| Step Type | Description |
| :--- | :--- |
| `wait_for_powerapps` | Waits for `iframe[name="fullscreen-app-host"]` to attach, compiles formulas, and waits for `#app-load-screen` to dismiss. |
| `[data-control-name="..."]` | Directly targets canvas controls by their authoring name in Power Apps Studio. |
| `frame: "fullscreen-app-host"` | Explicitly scopes an action to the Power Apps iframe (optional; fallback is automatic). |

---

## 4. Using Guided Journey Studio with Power Apps

1. In the Application Form, ensure an Edge session is imported.
2. Click **"⏺ Launch Journey Studio (Interactive)"**.
3. The studio automatically loads your authenticated session state and displays the live Power Apps canvas in the screencast canvas.
4. Clicking directly on any button, gallery row, or field in the screencast automatically recognizes `[data-control-name="..."]` attributes and synthesizes robust Power Apps steps.
5. Click **"Insert Captured Steps into Test"** to populate the test editor with 1 click.

---

## 5. Session State Inspection & Expiry

To check the validity and token health of an imported session state, call:

```bash
GET /api/storage-states/{app}/{environment}
```

**Response Example:**
```json
{
  "exists": true,
  "cookie_count": 18,
  "has_entra_id_auth": true,
  "entra_id_tokens": [
    "ESTSAUTH",
    "ESTSAUTHPERSISTENT",
    "MSPOK",
    "SignInStateCookie",
    "buid",
    "fpc"
  ],
  "domains": [
    ".login.microsoftonline.com",
    ".powerapps.com",
    "apps.powerapps.com"
  ],
  "status": "valid",
  "days_remaining": 28.4,
  "expires_at": "2026-11-25 04:00 UTC",
  "updated_at": "2026-10-27T19:40:00+00:00"
}
```

> [!TIP]
> For long-running synthetic monitoring of Power Apps, configure a dedicated service account in Microsoft Entra ID with a Conditional Access policy exempt from frequent MFA re-prompts or pinned to a static trusted worker egress IP.
