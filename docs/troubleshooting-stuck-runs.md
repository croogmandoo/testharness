# Troubleshooting: App Stuck on "Running"

When an app gets stuck in "running" status, it typically means:
- The test process crashed or was terminated unexpectedly
- Required secrets/environment variables were added after the run started
- Database state wasn't updated when the run ended

This prevents new runs from being triggered because the harness detects an active run already in progress.

---

## Solution: Reset the Stuck Run

### Prerequisites
- SQLite3 command-line tool (or any SQLite client)
- The app name and environment that's stuck

  
---

## Steps

### 1. Open a Terminal
Navigate to the workspace root:
```powershell
cd c:\Users\davigc\OneDrive - CATSAACSTA\Documents\testharness
```

### 2. Open SQLite and Connect to the Database
```powershell
sqlite3 data/harness.db
```

You should see the SQLite prompt:
```
SQLite version 3.x.x ...
sqlite>
```

### 3. Verify the Stuck Run
Before making changes, view the stuck run:

```sql
SELECT id, app, environment, status, started_at, finished_at FROM runs 
WHERE app='YourAppName' AND environment='YourEnvironment' ORDER BY started_at DESC LIMIT 5;
```

Replace `YourAppName` and `YourEnvironment` with the actual values. Look for a row with `status='running'`.

Example output:
```
id                                  app      environment  status   started_at           finished_at
────────────────────────────────────────────────────────────────────────────────────────────────────
550e8400-e29b-41d4-a716-446655440000  My App   production   running  2026-04-16 10:15:32  (null)
```

### 4. Reset the Status

Once confirmed, update the stuck run to mark it complete:

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE app='YourAppName' AND environment='YourEnvironment' AND status='running';
```

You should see:
```
sqlite> ...
sqlite>
```

### 5. Verify the Change
Run the SELECT query again to confirm:

```sql
SELECT id, app, environment, status, started_at, finished_at FROM runs 
WHERE app='YourAppName' AND environment='YourEnvironment' ORDER BY started_at DESC LIMIT 5;
```

The stuck run should now show `status='complete'` and have a `finished_at` timestamp.

### 6. Exit SQLite
```sql
.quit
```

Or press `Ctrl+D`.

---

## Verify in the Web Dashboard

1. Open the dashboard: http://localhost:8000 (or your configured URL)
2. Navigate to the app and environment
3. You should now be able to trigger a new run
4. The stuck run should appear in the history with status "complete" (or "fail" if you prefer)

---

## Prevent This in the Future

1. **Create all secrets before running tests:**  
   Go to `/admin/secrets` in the web UI and create all required `$VAR_NAME` secrets before triggering app runs.

2. **Check test YAML for required variables:**  
   Review your app YAML and identify all `$VAR_NAME` references, then ensure those secrets exist.

3. **Use environment variables for passwords:**  
   Instead of hardcoding, use `$SECRET_NAME` so the harness can substitute actual values at runtime.

---

## Example Walkthrough

Given this app definition with a missing secret:

```yaml
# apps/my-system.yaml
app: "My System"
url: "https://system.local"
tests:
  - name: "Login flow"
    type: browser
    steps:
      - navigate: /login
      - fill:
          field: "#username"
          value: "admin"
      - fill:
          field: "#password"
          value: "$ADMIN_PASSWORD"  # ← This secret doesn't exist yet!
      - click: "button[type=submit]"
```

**What happens:**
1. You run the test without creating the `ADMIN_PASSWORD` secret first
2. The test hangs or fails when trying to substitute `$ADMIN_PASSWORD`
3. The run gets stuck in status='running'

**How to fix:**
1. Create the secret at `/admin/secrets` (name: `ADMIN_PASSWORD`, value: your actual password)
2. Reset the stuck run using the SQL steps above
3. Trigger a new run — it should pass now

---

## If Multiple Runs Are Stuck

To reset all stuck runs for an app (if there are more than one):

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE app='YourAppName' AND status='running';
```

Or reset all stuck runs across all apps and environments:

```sql
UPDATE runs 
SET status='complete', finished_at=datetime('now') 
WHERE status='running';
```

**Use with caution** — only if you're sure you want to reset ALL pending/running tasks.

