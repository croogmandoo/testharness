# API Key Management — Design Spec
Date: 2026-04-14

## Overview

Add self-service API key management so users can authenticate to the harness API from external systems (CI pipelines, scripts, integrations) without exposing their session credentials. Keys inherit the owning user's current role and are revocable at any time.

## Goals

- Any authenticated user can create and revoke their own API keys
- Keys are accepted on all existing API routes alongside session cookies
- Admins can see and revoke any user's keys
- Keys have a configurable expiry (7d / 30d / 90d / 1y / never)
- The plaintext key is shown exactly once at creation; only a prefix is stored/displayed thereafter

## Non-Goals

- Per-key permission scoping (keys inherit the full user role)
- Key rotation (users delete and recreate)
- Service accounts (keys are always owned by a real user)

---

## Data Model

### `api_keys` table

| Column | Type | Notes |
|---|---|---|
| id | TEXT | UUID v4, primary key |
| user_id | TEXT | FK → users.id |
| name | TEXT | Human label, e.g. "CI pipeline" |
| key_prefix | TEXT | First 8 chars of plaintext key, shown in UI |
| key_hash | TEXT | SHA-256 hex digest of full plaintext key |
| expires_at | TEXT | ISO-8601 UTC, NULL = never expires |
| created_at | TEXT | ISO-8601 UTC |
| last_used_at | TEXT | ISO-8601 UTC, NULL = never used, updated on each auth |
| is_active | INTEGER | 1 = active, 0 = revoked |

### Key format

```
hth_<40 url-safe random chars>
```

Total: 44 characters. The `hth_` prefix makes keys identifiable in logs and CI secret stores. Example: `hth_aB3xK9mQpR2wYvNuJdLtEsHcFgOiZbVe8`

Key generation: `secrets.token_urlsafe(30)` → prepend `hth_`. Hash for storage: `hashlib.sha256(key.encode()).hexdigest()`.

Lookup: extract prefix (first 8 chars after `hth_`), fetch candidate rows by prefix, verify hash. Prefix lookup avoids a full-table hash scan.

---

## Authentication Flow

Add API key checking to `web/auth.py` `get_current_user`. The check runs before session cookie fallback:

```
1. Request arrives
2. Check Authorization: Bearer hth_... or X-API-Key: hth_... header
3. If present → look up by prefix, verify SHA-256 hash, check is_active + expiry
4. If valid → load user, update last_used_at, return user dict
5. If header absent → fall through to existing session cookie logic
6. If header present but invalid → 401 (API) or redirect to login (HTML)
```

No changes needed to any route — auth is transparent.

---

## Routes

All under `web/routes/api_keys.py`:

| Method | Path | Role | Description |
|---|---|---|---|
| GET | /api-keys | any authenticated | List own keys + creation form |
| POST | /api-keys | any authenticated | Create key, redirect with plaintext in flash |
| POST | /api-keys/{id}/revoke | any authenticated | Revoke own key |
| POST | /api-keys/{id}/revoke (admin) | admin | Revoke any user's key |

Admin sees an additional table of all users' keys on the same `/api-keys` page.

---

## UI

### `/api-keys` page

**Create form** (top card):
- Name field (required)
- Expiry select: 7 days / 30 days / 90 days / 1 year / Never

**Your Keys** table:
- Prefix, Name, Expires, Last used, Revoke button
- On creation: a one-time banner shows the full key with a copy button. Navigating away dismisses it.

**All Keys** (admin only, second card):
- Username, Prefix, Name, Expires, Last used, Revoke button

### Nav

Add "API Keys" link for all authenticated users (all roles).

---

## Database Changes

Add to `harness/db.py`:
- `create_api_keys_table()` — called from `init_schema()`
- `insert_api_key(row: dict)`
- `get_api_key_by_prefix(prefix: str) → list[dict]`
- `revoke_api_key(key_id: str, user_id: str = None)` — user_id=None allows admin revoke of any key
- `list_api_keys_for_user(user_id: str) → list[dict]`
- `list_all_api_keys() → list[dict]` — admin only
- `touch_api_key_last_used(key_id: str, timestamp: str)`

---

## Security Considerations

- Plaintext key is never stored — only SHA-256 hash
- Prefix stored in plaintext only for efficient lookup (8 chars, not enough to brute-force)
- `last_used_at` updated on every authenticated request (write on each call — acceptable at this scale)
- Expired keys rejected even if `is_active = 1`
- Revoked keys rejected immediately (no TTL/cache)
- Keys should be treated as secrets — document that they should be stored in the harness Secrets store or CI secret manager, not in plaintext config

---

## Testing

- Unit: `test_api_key_db.py` — insert, lookup by prefix, revoke, expiry check
- Integration: `test_web_api_keys.py` — create via POST, authenticate via Bearer header, revoke, verify rejected after revoke, verify expired key rejected
- Auth: verify existing session-cookie auth still works alongside key auth
