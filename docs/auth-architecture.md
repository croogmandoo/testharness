# Authentication & RBAC Architecture

**Status:** Complete (Phase 2)  
**Branch:** `feature/phase2-auth` → ready to merge to `master`  
**Tests:** 149 passing

---

## Key Files

```
harness/
  secrets_store.py   SecretsStore — Fernet encryption, HKDF key derivation, inject_to_env()
  auth_manager.py    verify_local_password() · ldap_authenticate()

web/
  auth.py            set_auth_config() · get_current_user() · require_role()
  routes/
    auth.py          GET/POST /setup · GET/POST /auth/login · POST /auth/logout
    users.py         /users CRUD (admin only)
    admin.py         /admin/ldap config + POST /admin/ldap/test AJAX
    secrets.py       /secrets (admin only, SecretsStore-backed)
  templates/
    setup.html       First-run admin account creation
    login.html       Login form
    403.html         Access denied page
    users.html       User list table
    user_form.html   Create / edit user form
    admin_ldap.html  LDAP config display + test-connection widget
```

---

## How It Fits Together

```
startup:  create_app()
            └─ SecretsStore(db)          derives two keys via HKDF from data/secret.key
                 ├─ .session_signing_key  → set_auth_config()  (itsdangerous)
                 └─ Fernet key           → encrypt/decrypt DB secrets

request:  cookie "session" (signed user_id)
            └─ get_current_user()        validates token, loads user, checks is_active
                 └─ require_role(...)    checks user.role membership → 403 if insufficient

first-run: _FirstRunMiddleware
            └─ if count_users() == 0 → redirect everything to /setup
```

---

## Roles & Access

| Role | Trigger runs | Export | Manage apps | Secrets | Users/LDAP |
|------|:---:|:---:|:---:|:---:|:---:|
| `admin` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `runner` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `reporting` | ❌ | ✅ | ❌ | ❌ | ❌ |
| `read_only` | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Auth Flow

**Local:** `POST /auth/login` → `verify_local_password()` → bcrypt check → set cookie  
**LDAP:** no local account + `auth.ldap.enabled` → `ldap_authenticate()` → `upsert_ldap_user()` → set cookie  
**LDAP role override:** `role_override=1` in DB skips LDAP group sync on login

---

## Critical Notes

- `passlib` is **broken** with bcrypt 5.x — use `import bcrypt` directly everywhere
- `data/secret.key` must be preserved across migrations (lost key = unrecoverable secrets)
- Session revocation: set `users.is_active = 0` (no sessions table)
- Config: `config.yaml` → `auth.session_hours`, `auth.secure_cookie`, `auth.ldap.*`
