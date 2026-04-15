# CA Certificate Management — Design Spec
Date: 2026-04-14

## Overview

Add admin-managed custom CA certificates so the harness can test internal/enterprise services that use private or self-signed TLS certificates without SSL errors. Admins upload named PEM certificates; the harness applies them transparently to all HTTP and browser test clients.

## Goals

- Admins can add and delete named CA certificates via the UI
- Certificates are applied to `httpx` clients (API and availability tests)
- Certificates are applied to Playwright browser tests
- No cert stored → no change in behaviour (fully backwards-compatible)
- Certificate content stored in the database (paste PEM text or upload a `.pem`/`.crt`/`.cer` file)

## Non-Goals

- Per-app or per-environment cert scoping (all active certs apply to all tests)
- Client certificate (mTLS) support
- Certificate expiry tracking or renewal reminders
- Encrypting cert content (CA certs are public)

---

## Data Model

### `ca_certs` table

| Column | Type | Notes |
|---|---|---|
| id | TEXT | UUID v4, primary key |
| name | TEXT | Human label, e.g. "Corp Root CA" |
| pem_content | TEXT | Full PEM block(s); may contain a certificate chain |
| created_at | TEXT | ISO-8601 UTC |
| added_by | TEXT | FK → users.id |

No `is_active` flag — unwanted certs are deleted. No edit — delete and re-add.

### On-disk bundle

`data/ca-bundle.pem` — all `pem_content` rows concatenated. Regenerated on every add and delete. Used by Playwright (Node.js process cannot consume in-memory certs). Excluded from version control via `.gitignore` (already covers `data/`).

---

## Database Changes

Add to `harness/db.py`:
- DDL: `ca_certs` table in `SCHEMA`
- `insert_ca_cert(row: dict)` — insert a cert row
- `list_ca_certs() → list[dict]` — all certs, ordered by `created_at DESC`
- `get_ca_cert(cert_id: str) → Optional[dict]`
- `delete_ca_cert(cert_id: str)` — delete by id

---

## SSL Context Helper

New file `harness/ssl_context.py`:

```python
import ssl
from harness.db import Database

def get_ssl_context(db: Database) -> ssl.SSLContext:
    """Return system default SSL context with any stored CA certs appended."""
    ctx = ssl.create_default_context()
    certs = db.list_ca_certs()
    if certs:
        combined = "\n".join(c["pem_content"] for c in certs)
        ctx.load_verify_locations(cadata=combined)
    return ctx
```

Returns the unmodified system context when no certs are stored — zero impact on existing behaviour.

### Bundle writer

Also in `harness/ssl_context.py`:

```python
import os

def write_ca_bundle(db: Database, path: str = "data/ca-bundle.pem") -> None:
    """Write all CA certs to a PEM bundle file. Removes the file if no certs."""
    certs = db.list_ca_certs()
    if certs:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(c["pem_content"] for c in certs))
    elif os.path.exists(path):
        os.remove(path)
```

---

## Runtime Integration

### `harness/runner.py`

`run_app` already receives `db: Database`. Build the SSL context once per run and pass it to the httpx-based test functions:
```python
from harness.ssl_context import get_ssl_context, BUNDLE_PATH
ssl_ctx = get_ssl_context(db)
```
Pass `ssl_ctx` as a new keyword argument to `run_api_test` and `run_availability_test` only. `run_browser_test` does not receive it — Playwright uses the on-disk bundle exclusively.

### `harness/api.py`

`run_api_test` and `run_availability_test` gain `ssl_ctx: ssl.SSLContext = None` parameter:
```python
async with httpx.AsyncClient(timeout=30, verify=ssl_ctx or True) as client:
```

`verify=True` is the httpx default (system CAs), so passing `None` is safe and fully backwards-compatible.

### `harness/browser.py`

`run_browser_test` signature is unchanged. Before launching Playwright, check for the bundle:
```python
from harness.ssl_context import BUNDLE_PATH
if os.path.exists(BUNDLE_PATH):
    os.environ["SSL_CERT_FILE"] = BUNDLE_PATH
```

`BUNDLE_PATH = "data/ca-bundle.pem"` is the single source of truth for the bundle path, exported from `ssl_context.py`.

`SSL_CERT_FILE` is the standard mechanism for injecting a custom CA bundle into the Node.js process that backs the Playwright browser — this is the only supported approach for custom CAs in Python Playwright.

---

## Routes

New file `web/routes/admin_ca_certs.py` (admin-only):

| Method | Path | Description |
|---|---|---|
| GET | /admin/ca-certs | List all certs + add form |
| POST | /admin/ca-certs | Add cert (name + PEM text or file upload) |
| POST | /admin/ca-certs/{id}/delete | Delete cert, regenerate bundle |

Registered in `web/main.py` alongside other routers.

---

## UI

### `/admin/ca-certs` page

**Add Certificate** card:
- Name field (required)
- PEM content textarea (paste) — OR —
- File upload input (`accept=".pem,.crt,.cer"`) — server reads file content
- Both paths submit to `POST /admin/ca-certs`; file upload takes priority if both provided
- Basic server-side validation: content must contain at least one `-----BEGIN CERTIFICATE-----` block

**Stored Certificates** table:
- Name, Added by, Added date, Delete button
- Empty state: "No CA certificates stored."

**Nav:** Add "CA Certs" link in `base.html` next to "LDAP" (admin-only section).

---

## Security Considerations

- Admin-only — no other role can add or delete certs
- PEM content validated to contain at least one certificate block before storing (rejects obviously wrong input)
- CA certs are not secrets — no encryption needed
- Bundle file lives under `data/` which is not served statically

---

## Testing

- Unit: `test_ca_cert_db.py` — insert, list, delete, bundle writer creates/removes file
- Integration: `test_web_admin_ca_certs.py` — add via POST (paste + upload), list page renders, delete removes cert and regenerates bundle, non-admin gets 403
- Runtime: verify `httpx.AsyncClient` receives a non-default SSL context when certs exist; verify `SSL_CERT_FILE` is set when bundle exists
