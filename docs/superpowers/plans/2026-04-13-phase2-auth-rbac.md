# Phase 2: Authentication & RBAC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add session-based login (local accounts + LDAP/AD) with four roles (admin, runner, reporting, read_only) gating all existing and new routes.

**Architecture:** `harness/auth_manager.py` handles credential verification (bcrypt for local, ldap3 for LDAP). `web/auth.py` owns session cookie signing (itsdangerous, key derived from SecretsStore) and exposes `get_current_user()` and `require_role(*roles)` FastAPI dependencies. A first-run middleware in `web/main.py` redirects every non-auth route to `/setup` when the users table is empty. All existing routes gain auth deps; all existing tests gain a `dependency_overrides` bypass so they keep passing.

**Tech Stack:** `passlib[bcrypt]`, `ldap3`, `itsdangerous.URLSafeTimedSerializer`, FastAPI `Depends`, SQLite via existing `Database` class, Jinja2 templates.

**Assumes Phase 1 complete:** `harness/secrets_store.py` exists; `web/main.py` exposes `get_secrets_store()`; `web/routes/secrets.py` exists; `cryptography`, `passlib`, `itsdangerous`, `ldap3` are installed.

---

## File Map

**New files:**
- `harness/auth_manager.py` — `verify_local_password()`, `ldap_authenticate()`
- `web/auth.py` — session helpers + `get_current_user()` + `require_role()` factory
- `web/routes/auth.py` — `/setup`, `GET/POST /auth/login`, `POST /auth/logout`
- `web/routes/users.py` — `/users` CRUD UI
- `web/routes/admin.py` — `/admin/ldap` config + test-connection AJAX
- `web/templates/login.html`
- `web/templates/setup.html`
- `web/templates/403.html`
- `web/templates/users.html`
- `web/templates/user_form.html`
- `web/templates/admin_ldap.html`
- `tests/test_auth.py`

**Modified files:**
- `harness/db.py` — add `users` table to SCHEMA + 8 CRUD methods
- `web/main.py` — register 3 new routers, first-run middleware, 403 exception handler, set signing key
- `web/routes/api.py` — add `require_role` to mutation endpoints; pass `secrets_store` to trigger
- `web/routes/apps.py` — add `require_role` to write/delete endpoints
- `web/routes/dashboard.py` — add `get_current_user` dep, pass `current_user` to template
- `web/routes/export.py` — add `require_role` dep
- `web/routes/secrets.py` — add `require_role("admin")` to all endpoints
- `web/templates/base.html` — user menu, role-conditional nav links
- `tests/test_web_api.py` — update fixtures with test user + auth override
- `tests/test_web_apps.py` — update fixtures with test user + auth override
- `config.yaml` — add `auth:` section

---

## Task 1: Add `users` Table to `harness/db.py`

**Files:**
- Modify: `harness/db.py`
- Create: `tests/test_auth_db.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_auth_db.py`:

  ```python
  import pytest
  from harness.db import Database


  @pytest.fixture
  def db(tmp_path):
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      return d


  def _user(overrides=None):
      base = {
          "id": "u1",
          "username": "alice",
          "display_name": "Alice",
          "email": "alice@example.com",
          "password_hash": "$2b$12$fakehash",
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": "2026-01-01T00:00:00",
          "last_login_at": None,
      }
      if overrides:
          base.update(overrides)
      return base


  def test_users_table_exists(db):
      with db._connect() as conn:
          tables = {r["name"] for r in conn.execute(
              "SELECT name FROM sqlite_master WHERE type='table'"
          ).fetchall()}
      assert "users" in tables


  def test_insert_and_get_user_by_username(db):
      db.insert_user(_user())
      row = db.get_user_by_username("alice")
      assert row is not None
      assert row["username"] == "alice"
      assert row["role"] == "admin"


  def test_get_user_by_id(db):
      db.insert_user(_user())
      row = db.get_user_by_id("u1")
      assert row is not None
      assert row["id"] == "u1"


  def test_get_user_missing_returns_none(db):
      assert db.get_user_by_username("nobody") is None
      assert db.get_user_by_id("nope") is None


  def test_count_users(db):
      assert db.count_users() == 0
      db.insert_user(_user())
      assert db.count_users() == 1


  def test_list_users(db):
      db.insert_user(_user({"id": "u1", "username": "alice"}))
      db.insert_user(_user({"id": "u2", "username": "bob"}))
      users = db.list_users()
      assert len(users) == 2
      usernames = {u["username"] for u in users}
      assert usernames == {"alice", "bob"}


  def test_list_users_excludes_password_hash(db):
      db.insert_user(_user())
      users = db.list_users()
      assert "password_hash" not in users[0]


  def test_update_user(db):
      db.insert_user(_user())
      db.update_user("u1", role="runner", is_active=0)
      row = db.get_user_by_id("u1")
      assert row["role"] == "runner"
      assert row["is_active"] == 0


  def test_update_user_last_login(db):
      db.insert_user(_user())
      db.update_user_last_login("u1", "2026-04-13T10:00:00")
      row = db.get_user_by_id("u1")
      assert row["last_login_at"] == "2026-04-13T10:00:00"


  def test_upsert_ldap_user_creates(db):
      user = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
      assert user["username"] == "bob"
      assert user["auth_provider"] == "ldap"
      assert user["role"] == "runner"


  def test_upsert_ldap_user_updates_role(db):
      db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
      updated = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "reporting")
      assert updated["role"] == "reporting"


  def test_upsert_ldap_user_respects_role_override(db):
      db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
      # Set role_override so LDAP sync won't change the role
      db.update_user(db.get_user_by_username("bob")["id"], role="admin", role_override=1)
      result = db.upsert_ldap_user("bob", "Bob Smith", "bob@corp.com", "runner")
      # Role should still be admin because role_override=1
      assert result["role"] == "admin"
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  cd C:/webtestingharness
  pytest tests/test_auth_db.py -v 2>&1 | head -30
  ```

  Expected: multiple FAILs — `Database` has no `insert_user`, `count_users`, etc.

- [ ] **Step 3: Add `users` table to SCHEMA in `harness/db.py`**

  Open `harness/db.py`. Append to the `SCHEMA` string (before the closing `"""`):

  ```sql
  CREATE TABLE IF NOT EXISTS users (
      id            TEXT PRIMARY KEY,
      username      TEXT UNIQUE NOT NULL,
      display_name  TEXT,
      email         TEXT,
      password_hash TEXT,
      role          TEXT NOT NULL,
      auth_provider TEXT NOT NULL DEFAULT 'local',
      role_override INTEGER DEFAULT 0,
      is_active     INTEGER DEFAULT 1,
      created_at    TEXT NOT NULL,
      last_login_at TEXT
  );
  ```

- [ ] **Step 4: Add 8 CRUD methods to `Database` class in `harness/db.py`**

  Append these methods to the `Database` class (after `get_app_summary`):

  ```python
  # ── Users ──────────────────────────────────────────────────────────────

  def insert_user(self, user: dict) -> None:
      with self._connect() as conn:
          conn.execute(
              "INSERT INTO users (id, username, display_name, email, password_hash, "
              "role, auth_provider, role_override, is_active, created_at, last_login_at) "
              "VALUES (:id,:username,:display_name,:email,:password_hash,"
              ":role,:auth_provider,:role_override,:is_active,:created_at,:last_login_at)",
              user,
          )

  def get_user_by_username(self, username: str) -> Optional[dict]:
      with self._connect() as conn:
          row = conn.execute(
              "SELECT * FROM users WHERE username=?", (username,)
          ).fetchone()
          return dict(row) if row else None

  def get_user_by_id(self, user_id: str) -> Optional[dict]:
      with self._connect() as conn:
          row = conn.execute(
              "SELECT * FROM users WHERE id=?", (user_id,)
          ).fetchone()
          return dict(row) if row else None

  def count_users(self) -> int:
      with self._connect() as conn:
          return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

  def list_users(self) -> list:
      """Returns all users. password_hash is excluded."""
      with self._connect() as conn:
          rows = conn.execute(
              "SELECT id,username,display_name,email,role,auth_provider,"
              "role_override,is_active,created_at,last_login_at FROM users "
              "ORDER BY username"
          ).fetchall()
          return [dict(r) for r in rows]

  def update_user(self, user_id: str, **kwargs) -> None:
      """Update specified fields. Accepted keys: display_name, email, role,
      role_override, is_active, password_hash."""
      allowed = {"display_name", "email", "role", "role_override",
                 "is_active", "password_hash"}
      updates = {k: v for k, v in kwargs.items() if k in allowed}
      if not updates:
          return
      sets = ", ".join(f"{k}=?" for k in updates)
      with self._connect() as conn:
          conn.execute(
              f"UPDATE users SET {sets} WHERE id=?",
              list(updates.values()) + [user_id],
          )

  def update_user_last_login(self, user_id: str, timestamp: str) -> None:
      with self._connect() as conn:
          conn.execute(
              "UPDATE users SET last_login_at=? WHERE id=?",
              (timestamp, user_id),
          )

  def upsert_ldap_user(self, username: str, display_name: str,
                       email: Optional[str], role: str) -> dict:
      """Create or update an LDAP user. Skips role update if role_override=1.
      Returns the up-to-date user dict (no password_hash)."""
      import uuid
      from datetime import datetime, timezone
      existing = self.get_user_by_username(username)
      if existing is None:
          new_id = str(uuid.uuid4())
          self.insert_user({
              "id": new_id,
              "username": username,
              "display_name": display_name,
              "email": email,
              "password_hash": None,
              "role": role,
              "auth_provider": "ldap",
              "role_override": 0,
              "is_active": 1,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "last_login_at": None,
          })
      else:
          updates: dict = {"display_name": display_name, "email": email}
          if not existing.get("role_override"):
              updates["role"] = role
          self.update_user(existing["id"], **updates)
      return self.get_user_by_username(username)
  ```

- [ ] **Step 5: Run tests to confirm they pass**

  ```bash
  pytest tests/test_auth_db.py -v
  ```

  Expected: all 12 tests PASS.

- [ ] **Step 6: Run full suite to confirm no regressions**

  ```bash
  pytest --ignore=tests/test_browser_screenshot.py -x -q
  ```

  Expected: all existing tests PASS (users table was added with `IF NOT EXISTS`).

- [ ] **Step 7: Commit**

  ```bash
  git add harness/db.py tests/test_auth_db.py
  git commit -m "feat: add users table schema and CRUD methods to Database"
  ```

---

## Task 2: Create `harness/auth_manager.py`

**Files:**
- Create: `harness/auth_manager.py`
- Create: `tests/test_auth_manager.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_auth_manager.py`:

  ```python
  import pytest
  from unittest.mock import patch, MagicMock
  from harness.db import Database


  @pytest.fixture
  def db(tmp_path):
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      return d


  def _make_local_user(db, username="alice", password="secret123"):
      from passlib.hash import bcrypt
      from datetime import datetime, timezone
      import uuid
      db.insert_user({
          "id": str(uuid.uuid4()),
          "username": username,
          "display_name": "Alice",
          "email": None,
          "password_hash": bcrypt.hash(password),
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })


  def test_verify_local_password_success(db):
      from harness.auth_manager import verify_local_password
      _make_local_user(db)
      user = verify_local_password("alice", "secret123", db)
      assert user is not None
      assert user["username"] == "alice"


  def test_verify_local_password_wrong_password(db):
      from harness.auth_manager import verify_local_password
      _make_local_user(db)
      assert verify_local_password("alice", "wrongpass", db) is None


  def test_verify_local_password_no_user(db):
      from harness.auth_manager import verify_local_password
      assert verify_local_password("nobody", "pass", db) is None


  def test_verify_local_password_inactive_user(db):
      from harness.auth_manager import verify_local_password
      _make_local_user(db)
      user = db.get_user_by_username("alice")
      db.update_user(user["id"], is_active=0)
      assert verify_local_password("alice", "secret123", db) is None


  def test_verify_local_password_ldap_user_returns_none(db):
      """LDAP users have no password_hash; local verify must return None for them."""
      from harness.auth_manager import verify_local_password
      db.upsert_ldap_user("ldapuser", "LDAP User", None, "runner")
      assert verify_local_password("ldapuser", "anypass", db) is None


  def test_ldap_authenticate_success():
      from harness.auth_manager import ldap_authenticate
      ldap_cfg = {
          "enabled": True,
          "server": "ldap://dc.corp.com",
          "port": 389,
          "use_tls": False,
          "base_dn": "DC=corp,DC=com",
          "user_search_filter": "(sAMAccountName={username})",
          "group_search_base": "OU=Groups,DC=corp,DC=com",
          "group_attribute": "memberOf",
          "role_map": {
              "CN=Admins,OU=Groups,DC=corp,DC=com": "admin",
          },
          "default_role": "read_only",
      }

      mock_conn = MagicMock()
      mock_conn.bind.return_value = True
      mock_conn.search.return_value = True
      mock_conn.entries = [MagicMock(
          **{
              "displayName.value": "Bob Smith",
              "mail.value": "bob@corp.com",
              "memberOf.values": ["CN=Admins,OU=Groups,DC=corp,DC=com"],
          }
      )]

      with patch("harness.auth_manager.Connection", return_value=mock_conn), \
           patch("harness.auth_manager.Server"):
          result = ldap_authenticate("bob", "pass", ldap_cfg)

      assert result is not None
      assert result["username"] == "bob"
      assert result["role"] == "admin"
      assert result["display_name"] == "Bob Smith"


  def test_ldap_authenticate_bind_failure():
      from harness.auth_manager import ldap_authenticate
      ldap_cfg = {
          "enabled": True,
          "server": "ldap://dc.corp.com",
          "port": 389,
          "use_tls": False,
          "base_dn": "DC=corp,DC=com",
          "user_search_filter": "(sAMAccountName={username})",
          "group_search_base": "OU=Groups,DC=corp,DC=com",
          "group_attribute": "memberOf",
          "role_map": {},
          "default_role": "read_only",
      }

      mock_conn = MagicMock()
      mock_conn.bind.return_value = False

      with patch("harness.auth_manager.Connection", return_value=mock_conn), \
           patch("harness.auth_manager.Server"):
          result = ldap_authenticate("bob", "badpass", ldap_cfg)

      assert result is None


  def test_ldap_authenticate_default_role():
      from harness.auth_manager import ldap_authenticate
      ldap_cfg = {
          "enabled": True,
          "server": "ldap://dc.corp.com",
          "port": 389,
          "use_tls": False,
          "base_dn": "DC=corp,DC=com",
          "user_search_filter": "(sAMAccountName={username})",
          "group_search_base": "OU=Groups,DC=corp,DC=com",
          "group_attribute": "memberOf",
          "role_map": {"CN=Admins,OU=Groups,DC=corp,DC=com": "admin"},
          "default_role": "read_only",
      }

      mock_conn = MagicMock()
      mock_conn.bind.return_value = True
      mock_conn.search.return_value = True
      mock_conn.entries = [MagicMock(
          **{
              "displayName.value": "Carol",
              "mail.value": "carol@corp.com",
              "memberOf.values": [],  # no matching groups
          }
      )]

      with patch("harness.auth_manager.Connection", return_value=mock_conn), \
           patch("harness.auth_manager.Server"):
          result = ldap_authenticate("carol", "pass", ldap_cfg)

      assert result["role"] == "read_only"
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_auth_manager.py -v 2>&1 | head -20
  ```

  Expected: ImportError — `harness.auth_manager` does not exist.

- [ ] **Step 3: Create `harness/auth_manager.py`**

  Create the file:

  ```python
  """
  Authentication helpers.

  verify_local_password  — bcrypt check for local accounts
  ldap_authenticate      — ldap3 bind + group→role mapping for LDAP/AD accounts
  """
  from typing import Optional

  # Imported at call sites to keep module-level imports fast and patchable in tests.
  from ldap3 import Server, Connection, ALL, SUBTREE


  def verify_local_password(username: str, password: str, db) -> Optional[dict]:
      """Return the user dict if credentials are valid, else None.

      Returns None (not an error) for:
      - unknown username
      - wrong password
      - inactive user
      - LDAP users (no password_hash)
      """
      from passlib.hash import bcrypt as _bcrypt

      user = db.get_user_by_username(username)
      if user is None:
          return None
      if user.get("auth_provider") != "local":
          return None
      if not user.get("is_active"):
          return None
      if not user.get("password_hash"):
          return None
      if not _bcrypt.verify(password, user["password_hash"]):
          return None
      return user


  def ldap_authenticate(username: str, password: str, ldap_cfg: dict) -> Optional[dict]:
      """Attempt LDAP bind for *username*/*password* using *ldap_cfg*.

      On success returns a dict with keys: username, display_name, email, role.
      On failure (wrong credentials, server error) returns None.

      ldap_cfg keys (all required):
        server, port, use_tls, base_dn, user_search_filter,
        group_search_base, group_attribute, role_map, default_role
      """
      # Derive the bind DN.  For Active Directory the simplest approach is
      # user@domain, where domain is assembled from the base_dn DC components.
      dc_parts = [
          p.split("=")[1]
          for p in ldap_cfg["base_dn"].split(",")
          if p.strip().upper().startswith("DC=")
      ]
      domain = ".".join(dc_parts)
      bind_user = f"{username}@{domain}"

      server = Server(
          ldap_cfg["server"],
          port=ldap_cfg["port"],
          use_ssl=ldap_cfg.get("use_tls", False),
          get_info=ALL,
      )

      conn = Connection(server, user=bind_user, password=password, auto_bind=False)
      try:
          if not conn.bind():
              return None

          # Search for the user entry to get display name, email, and groups.
          search_filter = ldap_cfg["user_search_filter"].format(username=username)
          group_attr = ldap_cfg.get("group_attribute", "memberOf")
          conn.search(
              search_base=ldap_cfg["base_dn"],
              search_filter=search_filter,
              search_scope=SUBTREE,
              attributes=["displayName", "mail", group_attr],
          )

          if not conn.entries:
              return None

          entry = conn.entries[0]

          display_name = _safe_attr(entry, "displayName") or username
          email = _safe_attr(entry, "mail")
          groups = _safe_list_attr(entry, group_attr)

          role = ldap_cfg.get("default_role", "read_only")
          role_map: dict = ldap_cfg.get("role_map", {})
          for group_dn in groups:
              if group_dn in role_map:
                  role = role_map[group_dn]
                  break  # first match wins

          return {
              "username": username,
              "display_name": display_name,
              "email": email,
              "role": role,
          }
      except Exception:
          return None
      finally:
          try:
              conn.unbind()
          except Exception:
              pass


  def _safe_attr(entry, attr: str) -> Optional[str]:
      try:
          val = getattr(entry, attr).value
          return str(val) if val is not None else None
      except Exception:
          return None


  def _safe_list_attr(entry, attr: str) -> list:
      try:
          vals = getattr(entry, attr).values
          return list(vals) if vals else []
      except Exception:
          return []
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  pytest tests/test_auth_manager.py -v
  ```

  Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add harness/auth_manager.py tests/test_auth_manager.py
  git commit -m "feat: add auth_manager with local bcrypt verify and LDAP authenticate"
  ```

---

## Task 3: Create `web/auth.py`

**Files:**
- Create: `web/auth.py`
- Create: `tests/test_web_auth.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_web_auth.py`:

  ```python
  import pytest
  from unittest.mock import patch


  def _signing_key() -> bytes:
      return b"\x00" * 32


  def test_make_and_load_session_token_roundtrip():
      from web.auth import make_session_token, load_session_token
      token = make_session_token("user-123", _signing_key(), session_hours=8)
      user_id = load_session_token(token, _signing_key(), session_hours=8)
      assert user_id == "user-123"


  def test_load_session_token_wrong_key():
      from web.auth import make_session_token, load_session_token
      token = make_session_token("user-123", _signing_key(), session_hours=8)
      other_key = b"\xff" * 32
      assert load_session_token(token, other_key, session_hours=8) is None


  def test_load_session_token_tampered():
      from web.auth import load_session_token
      assert load_session_token("tampered.garbage.token", _signing_key(), session_hours=8) is None


  def test_load_session_token_empty():
      from web.auth import load_session_token
      assert load_session_token("", _signing_key(), session_hours=8) is None
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_web_auth.py -v 2>&1 | head -10
  ```

  Expected: ImportError — `web.auth` does not exist.

- [ ] **Step 3: Create `web/auth.py`**

  ```python
  """
  Session cookie helpers and FastAPI auth dependencies.

  Module-level config (_signing_key, _session_hours, _secure_cookie) is set
  once at startup by web.main.create_app() via set_auth_config().

  get_current_user  — FastAPI dependency; raises 307→/auth/login for HTML,
                      401 for /api routes
  require_role      — factory returning a dependency that checks role membership
  """
  from typing import Optional
  from fastapi import Depends, HTTPException, Request
  from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

  # Set by create_app() after SecretsStore is initialised.
  _signing_key: bytes = b""
  _session_hours: int = 8
  _secure_cookie: bool = False


  def set_auth_config(signing_key: bytes, session_hours: int, secure_cookie: bool) -> None:
      global _signing_key, _session_hours, _secure_cookie
      _signing_key = signing_key
      _session_hours = session_hours
      _secure_cookie = secure_cookie


  # ── Cookie helpers ────────────────────────────────────────────────────────────

  def make_session_token(user_id: str, signing_key: bytes, session_hours: int) -> str:
      s = URLSafeTimedSerializer(signing_key)
      return s.dumps(user_id)


  def load_session_token(token: str, signing_key: bytes, session_hours: int) -> Optional[str]:
      if not token:
          return None
      s = URLSafeTimedSerializer(signing_key)
      try:
          return s.loads(token, max_age=session_hours * 3600)
      except (BadSignature, SignatureExpired, Exception):
          return None


  # Convenience wrappers that use the module-level config (used by routes).

  def _make_token(user_id: str) -> str:
      return make_session_token(user_id, _signing_key, _session_hours)


  def _load_token(token: str) -> Optional[str]:
      return load_session_token(token, _signing_key, _session_hours)


  # ── FastAPI dependencies ──────────────────────────────────────────────────────

  async def get_current_user(request: Request) -> dict:
      """Validate the session cookie and return the active user dict.

      Raises HTTPException(307 → /auth/login) for HTML routes,
      HTTPException(401) for /api routes.
      """
      from web.main import get_db
      db = get_db()

      is_api = request.url.path.startswith("/api")

      def _not_authed():
          if is_api:
              raise HTTPException(status_code=401, detail="Not authenticated")
          raise HTTPException(
              status_code=307,
              headers={"Location": "/auth/login"},
          )

      token = request.cookies.get("session", "")
      user_id = _load_token(token)
      if not user_id:
          _not_authed()

      user = db.get_user_by_id(user_id)
      if not user or not user.get("is_active"):
          _not_authed()

      return user


  def require_role(*roles: str):
      """Factory: returns a FastAPI dependency that calls get_current_user and
      then checks the user's role is in *roles*.

      Usage:
          @router.post("/secrets")
          async def create_secret(user=Depends(require_role("admin"))):
              ...
      """
      async def _dep(request: Request, user: dict = Depends(get_current_user)):
          if user["role"] not in roles:
              if request.url.path.startswith("/api"):
                  raise HTTPException(status_code=403, detail="Insufficient permissions")
              raise HTTPException(status_code=403)
          return user
      return _dep
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  pytest tests/test_web_auth.py -v
  ```

  Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add web/auth.py tests/test_web_auth.py
  git commit -m "feat: add session cookie helpers and get_current_user/require_role FastAPI deps"
  ```

---

## Task 4: Create `web/routes/auth.py`

**Files:**
- Create: `web/routes/auth.py`
- Create: `tests/test_web_auth_routes.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_web_auth_routes.py`:

  ```python
  import pytest
  from fastapi.testclient import TestClient
  from harness.db import Database
  from web.main import create_app


  CONFIG = {
      "default_environment": "production",
      "environments": {"production": {"label": "Production"}},
      "auth": {"session_hours": 8, "secure_cookie": False},
  }


  @pytest.fixture
  def db(tmp_path):
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      return d


  @pytest.fixture
  def client(db, tmp_path):
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
      return TestClient(app, follow_redirects=False)


  # ── /setup ────────────────────────────────────────────────────────────────────

  def test_setup_redirects_to_setup_when_no_users(client):
      resp = client.get("/")
      assert resp.status_code == 302
      assert resp.headers["location"] == "/setup"


  def test_setup_get_renders_form(client):
      resp = client.get("/setup")
      assert resp.status_code == 200
      assert b"Create Admin" in resp.content or b"setup" in resp.content.lower()


  def test_setup_post_creates_admin_and_redirects(client, db):
      resp = client.post("/setup", data={
          "username": "admin",
          "password": "Secret123!",
          "confirm": "Secret123!",
          "display_name": "Admin User",
      })
      assert resp.status_code in (302, 303)
      assert db.count_users() == 1
      user = db.get_user_by_username("admin")
      assert user["role"] == "admin"


  def test_setup_post_password_mismatch_returns_form(client):
      resp = client.post("/setup", data={
          "username": "admin",
          "password": "Secret123!",
          "confirm": "Different!",
          "display_name": "",
      })
      assert resp.status_code == 200
      assert b"match" in resp.content.lower() or b"error" in resp.content.lower()


  def test_setup_returns_404_when_users_exist(db, tmp_path):
      db.upsert_ldap_user("existing", "Existing", None, "admin")
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))
      c = TestClient(app, follow_redirects=False)
      resp = c.get("/setup")
      assert resp.status_code == 404


  # ── /auth/login ───────────────────────────────────────────────────────────────

  def test_login_get_renders_form(client):
      resp = client.get("/auth/login")
      assert resp.status_code == 200
      assert b"login" in resp.content.lower() or b"username" in resp.content.lower()


  def test_login_post_success_sets_cookie_and_redirects(client, db):
      from passlib.hash import bcrypt
      import uuid
      from datetime import datetime, timezone
      db.insert_user({
          "id": str(uuid.uuid4()),
          "username": "alice",
          "display_name": "Alice",
          "email": None,
          "password_hash": bcrypt.hash("pass123"),
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })
      resp = client.post("/auth/login", data={"username": "alice", "password": "pass123"})
      assert resp.status_code in (302, 303)
      assert "session" in resp.cookies


  def test_login_post_wrong_password_shows_error(client, db):
      from passlib.hash import bcrypt
      import uuid
      from datetime import datetime, timezone
      db.insert_user({
          "id": str(uuid.uuid4()),
          "username": "alice",
          "display_name": "Alice",
          "email": None,
          "password_hash": bcrypt.hash("correct"),
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })
      resp = client.post("/auth/login", data={"username": "alice", "password": "wrong"})
      assert resp.status_code == 200
      assert b"Invalid" in resp.content


  # ── /auth/logout ──────────────────────────────────────────────────────────────

  def test_logout_clears_cookie_and_redirects(client, db):
      # Create a user so first-run doesn't interfere
      db.upsert_ldap_user("bob", "Bob", None, "admin")
      resp = client.post("/auth/logout")
      assert resp.status_code in (302, 303)
      # Cookie should be cleared (empty value or expired)
      session_cookie = resp.cookies.get("session", "")
      assert session_cookie == ""
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_web_auth_routes.py -v 2>&1 | head -20
  ```

  Expected: errors — `/setup`, `/auth/login` routes not registered yet.

- [ ] **Step 3: Create `web/routes/auth.py`**

  ```python
  import uuid
  import os
  from datetime import datetime, timezone

  from fastapi import APIRouter, Form, Request
  from fastapi.responses import HTMLResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates

  router = APIRouter()
  templates = Jinja2Templates(
      directory=os.path.join(os.path.dirname(__file__), "..", "templates")
  )


  def _nav_ctx(request: Request) -> dict:
      from web.main import get_config
      config = get_config()
      return {
          "request": request,
          "environments": config.get("environments", {}),
          "environment": None,
          "current_user": None,
      }


  # ── First-run setup ───────────────────────────────────────────────────────────

  @router.get("/setup", response_class=HTMLResponse)
  async def setup_get(request: Request):
      from web.main import get_db
      if get_db().count_users() > 0:
          from fastapi import HTTPException
          raise HTTPException(status_code=404)
      return templates.TemplateResponse(request, "setup.html", _nav_ctx(request))


  @router.post("/setup")
  async def setup_post(
      request: Request,
      username: str = Form(...),
      password: str = Form(...),
      confirm: str = Form(...),
      display_name: str = Form(""),
  ):
      from web.main import get_db, get_config
      db = get_db()
      if db.count_users() > 0:
          from fastapi import HTTPException
          raise HTTPException(status_code=404)

      ctx = {**_nav_ctx(request), "error": None}

      if password != confirm:
          ctx["error"] = "Passwords do not match."
          return templates.TemplateResponse(request, "setup.html", ctx, status_code=422)

      if len(password) < 8:
          ctx["error"] = "Password must be at least 8 characters."
          return templates.TemplateResponse(request, "setup.html", ctx, status_code=422)

      from passlib.hash import bcrypt
      from web.auth import _make_token
      config = get_config()

      user_id = str(uuid.uuid4())
      db.insert_user({
          "id": user_id,
          "username": username.strip(),
          "display_name": display_name.strip() or username.strip(),
          "email": None,
          "password_hash": bcrypt.hash(password),
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })
      db.update_user_last_login(user_id, datetime.now(timezone.utc).isoformat())

      token = _make_token(user_id)
      session_hours = config.get("auth", {}).get("session_hours", 8)
      secure = config.get("auth", {}).get("secure_cookie", False)

      response = RedirectResponse("/", status_code=303)
      response.set_cookie(
          "session", token,
          httponly=True, samesite="lax", secure=secure,
          max_age=session_hours * 3600,
      )
      return response


  # ── Login / logout ────────────────────────────────────────────────────────────

  @router.get("/auth/login", response_class=HTMLResponse)
  async def login_get(request: Request):
      return templates.TemplateResponse(request, "login.html", _nav_ctx(request))


  @router.post("/auth/login")
  async def login_post(
      request: Request,
      username: str = Form(...),
      password: str = Form(...),
  ):
      from web.main import get_db, get_config
      from harness.auth_manager import verify_local_password, ldap_authenticate
      from web.auth import _make_token

      db = get_db()
      config = get_config()

      user = verify_local_password(username, password, db)

      if user is None:
          ldap_cfg = config.get("auth", {}).get("ldap", {})
          if ldap_cfg.get("enabled"):
              ldap_info = ldap_authenticate(username, password, ldap_cfg)
              if ldap_info:
                  user = db.upsert_ldap_user(
                      ldap_info["username"],
                      ldap_info["display_name"],
                      ldap_info["email"],
                      ldap_info["role"],
                  )

      if user is None:
          ctx = {**_nav_ctx(request), "error": "Invalid username or password."}
          return templates.TemplateResponse(request, "login.html", ctx, status_code=401)

      db.update_user_last_login(user["id"], datetime.now(timezone.utc).isoformat())

      token = _make_token(user["id"])
      session_hours = config.get("auth", {}).get("session_hours", 8)
      secure = config.get("auth", {}).get("secure_cookie", False)

      response = RedirectResponse("/", status_code=303)
      response.set_cookie(
          "session", token,
          httponly=True, samesite="lax", secure=secure,
          max_age=session_hours * 3600,
      )
      return response


  @router.post("/auth/logout")
  async def logout(request: Request):
      response = RedirectResponse("/auth/login", status_code=303)
      response.delete_cookie("session")
      return response
  ```

- [ ] **Step 4: Wire the router temporarily in `web/main.py` to unblock tests**

  Open `web/main.py`. Inside `create_app()`, after the existing `app.include_router` calls, add:

  ```python
  from web.routes.auth import router as auth_router
  app.include_router(auth_router)
  ```

  Also add the first-run middleware and set the signing key. Replace the `create_app` function body with:

  ```python
  def create_app(db: Database = None, config: dict = None, apps_dir: str = "apps") -> FastAPI:
      global _db, _config, _apps, _apps_dir, _secrets_store
      _config = config or {}
      _apps_dir = apps_dir
      _apps = load_apps(apps_dir) if os.path.isdir(apps_dir) else []

      if db is None:
          os.makedirs("data", exist_ok=True)
          _db = Database("data/harness.db")
          _db.init_schema()
      else:
          _db = db

      # Initialise SecretsStore and derive session signing key.
      from harness.secrets_store import SecretsStore
      from web.auth import set_auth_config
      _secrets_store = SecretsStore(_db)
      auth_cfg = _config.get("auth", {})
      set_auth_config(
          signing_key=_secrets_store.session_signing_key,
          session_hours=auth_cfg.get("session_hours", 8),
          secure_cookie=auth_cfg.get("secure_cookie", False),
      )

      app = FastAPI(title="Web Testing Harness")

      # First-run middleware: redirect every HTML route to /setup when no users exist.
      from starlette.middleware.base import BaseHTTPMiddleware
      from fastapi.responses import RedirectResponse as _Redirect

      class _FirstRunMiddleware(BaseHTTPMiddleware):
          async def dispatch(self, request, call_next):
              skip = {"/setup", "/auth/login", "/auth/logout"}
              skip_prefix = ("/static", "/screenshots", "/api")
              if (request.url.path not in skip and
                      not request.url.path.startswith(skip_prefix)):
                  if _db is not None and _db.count_users() == 0:
                      return _Redirect("/setup", status_code=302)
              return await call_next(request)

      app.add_middleware(_FirstRunMiddleware)

      # 403 exception handler — renders 403.html for HTML requests.
      from fastapi.exceptions import HTTPException as _HTTPExc
      from fastapi.responses import JSONResponse
      from fastapi.templating import Jinja2Templates as _Tmpl
      _tmpl = _Tmpl(directory=os.path.join(os.path.dirname(__file__), "templates"))

      @app.exception_handler(_HTTPExc)
      async def _http_exc_handler(request, exc: _HTTPExc):
          if exc.status_code == 403 and not request.url.path.startswith("/api"):
              accept = request.headers.get("accept", "")
              if "text/html" in accept or "/" in accept:
                  return _tmpl.TemplateResponse(request, "403.html", {
                      "request": request,
                      "environments": _config.get("environments", {}),
                      "environment": None,
                      "current_user": None,
                  }, status_code=403)
          return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

      from web.routes.api import router as api_router
      app.include_router(api_router)

      from web.routes.dashboard import router as dashboard_router
      app.include_router(dashboard_router)

      from web.routes.apps import router as apps_router
      app.include_router(apps_router)

      from web.routes.export import router as export_router
      app.include_router(export_router)

      from web.routes.auth import router as auth_router
      app.include_router(auth_router)

      from web.routes.secrets import router as secrets_router
      app.include_router(secrets_router)

      screenshots_dir = "data/screenshots"
      os.makedirs(screenshots_dir, exist_ok=True)
      app.mount("/screenshots", StaticFiles(directory=screenshots_dir), name="screenshots")

      static_dir = os.path.join(os.path.dirname(__file__), "static")
      if os.path.isdir(static_dir):
          app.mount("/static", StaticFiles(directory=static_dir), name="static")

      return app
  ```

  Also add `_secrets_store = None` near the other module-level globals at the top of the file, and add `get_secrets_store`:

  ```python
  _secrets_store = None

  def get_secrets_store():
      return _secrets_store
  ```

- [ ] **Step 5: Create placeholder templates so the app starts**

  Create `web/templates/setup.html`:

  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head><meta charset="UTF-8"><title>Setup — Testing Harness</title>
  <link rel="stylesheet" href="/static/style.css"></head>
  <body>
  <div class="main" style="max-width:400px;margin:4rem auto;">
    <h1 style="margin-bottom:1.5rem;">Create Admin Account</h1>
    {% if error %}<p style="color:#f87171;margin-bottom:1rem;">{{ error }}</p>{% endif %}
    <form method="post" action="/setup">
      <div style="margin-bottom:1rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Username</label>
        <input name="username" required autofocus
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div style="margin-bottom:1rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Display name (optional)</label>
        <input name="display_name"
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div style="margin-bottom:1rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Password</label>
        <input name="password" type="password" required
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div style="margin-bottom:1.5rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Confirm password</label>
        <input name="confirm" type="password" required
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;padding:.6rem;">
        Create admin &amp; sign in
      </button>
    </form>
  </div>
  </body>
  </html>
  ```

  Create `web/templates/login.html`:

  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head><meta charset="UTF-8"><title>Sign in — Testing Harness</title>
  <link rel="stylesheet" href="/static/style.css"></head>
  <body>
  <div class="main" style="max-width:380px;margin:4rem auto;">
    <h1 style="margin-bottom:1.5rem;">Sign in</h1>
    {% if error %}<p style="color:#f87171;margin-bottom:1rem;">{{ error }}</p>{% endif %}
    <form method="post" action="/auth/login">
      <div style="margin-bottom:1rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Username</label>
        <input name="username" required autofocus
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div style="margin-bottom:1.5rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Password</label>
        <input name="password" type="password" required
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%;padding:.6rem;">
        Sign in
      </button>
    </form>
  </div>
  </body>
  </html>
  ```

  Create `web/templates/403.html`:

  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head><meta charset="UTF-8"><title>Access Denied — Testing Harness</title>
  <link rel="stylesheet" href="/static/style.css"></head>
  <body>
  <nav class="nav">
    <div style="display:flex;align-items:center;gap:1.5rem;">
      <a href="/" class="nav-brand">Testing Harness</a>
    </div>
  </nav>
  <div class="main" style="text-align:center;padding:4rem 1rem;">
    <h1 style="font-size:3rem;margin-bottom:.5rem;">403</h1>
    <p style="color:#a0aec0;margin-bottom:1.5rem;">
      You don't have permission to access this page.
    </p>
    <a href="/" class="btn btn-primary">Go to dashboard</a>
  </div>
  </body>
  </html>
  ```

- [ ] **Step 6: Run auth route tests**

  ```bash
  pytest tests/test_web_auth_routes.py -v
  ```

  Expected: all 9 tests PASS.

- [ ] **Step 7: Commit**

  ```bash
  git add web/routes/auth.py web/main.py web/templates/setup.html \
          web/templates/login.html web/templates/403.html \
          tests/test_web_auth_routes.py
  git commit -m "feat: add /setup, /auth/login, /auth/logout routes with first-run middleware"
  ```

---

## Task 5: Update Existing Test Fixtures for Auth

All existing tests will now fail because the first-run middleware redirects requests when the DB has 0 users. Fix: insert a test user in each `db` fixture and override `get_current_user`.

**Files:**
- Modify: `tests/test_web_api.py`
- Modify: `tests/test_web_apps.py`

- [ ] **Step 1: Run existing tests to see current failures**

  ```bash
  pytest tests/test_web_api.py tests/test_web_apps.py -v 2>&1 | tail -20
  ```

  Expected: many failures — responses are 302 redirects to `/setup`.

- [ ] **Step 2: Update `tests/test_web_api.py`**

  Replace the existing `db` and `client` fixtures:

  ```python
  import pytest
  from fastapi import Request
  from fastapi.testclient import TestClient
  from unittest.mock import patch, AsyncMock, MagicMock
  from web.main import create_app
  from harness.db import Database


  def _insert_test_admin(db):
      import uuid
      from datetime import datetime, timezone
      db.insert_user({
          "id": "test-admin-1",
          "username": "testadmin",
          "display_name": "Test Admin",
          "email": None,
          "password_hash": None,
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })


  @pytest.fixture
  def db(tmp_path):
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      _insert_test_admin(d)
      return d


  @pytest.fixture
  def client(db, tmp_path):
      from web.auth import get_current_user
      apps_dir = tmp_path / "apps"
      apps_dir.mkdir()
      config = {
          "default_environment": "production",
          "environments": {"production": {"label": "Production"}},
      }
      app = create_app(db=db, config=config, apps_dir=str(apps_dir))

      async def _mock_admin(request: Request):
          return {"id": "test-admin-1", "username": "testadmin",
                  "display_name": "Test Admin", "role": "admin", "is_active": 1}

      app.dependency_overrides[get_current_user] = _mock_admin
      return TestClient(app)
  ```

  Leave all existing test functions unchanged.

- [ ] **Step 3: Update `tests/test_web_apps.py`**

  Replace the existing `db`, `client`, and `client_with_app` fixtures:

  ```python
  import pytest
  import yaml
  from fastapi import Request
  from fastapi.testclient import TestClient
  from web.main import create_app
  from harness.db import Database


  def _insert_test_admin(db):
      import uuid
      from datetime import datetime, timezone
      db.insert_user({
          "id": "test-admin-1",
          "username": "testadmin",
          "display_name": "Test Admin",
          "email": None,
          "password_hash": None,
          "role": "admin",
          "auth_provider": "local",
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })


  def _make_app(db, apps_dir):
      from web.auth import get_current_user
      config = {
          "default_environment": "production",
          "environments": {"production": {"label": "Production"}},
      }
      app = create_app(db=db, config=config, apps_dir=str(apps_dir))

      async def _mock_admin(request: Request):
          return {"id": "test-admin-1", "username": "testadmin",
                  "display_name": "Test Admin", "role": "admin", "is_active": 1}

      app.dependency_overrides[get_current_user] = _mock_admin
      return app


  @pytest.fixture
  def db(tmp_path):
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      _insert_test_admin(d)
      return d


  @pytest.fixture
  def apps_dir(tmp_path):
      d = tmp_path / "apps"
      d.mkdir()
      return d


  @pytest.fixture
  def client(db, apps_dir):
      return TestClient(_make_app(db, apps_dir))


  @pytest.fixture
  def client_with_app(db, apps_dir):
      """Client with one pre-existing app."""
      app_def = {
          "app": "my-api",
          "url": "https://example.com",
          "tests": [{"name": "health", "type": "availability", "expect_status": 200}],
      }
      (apps_dir / "my-api.yaml").write_text(yaml.dump(app_def))
      return TestClient(_make_app(db, apps_dir)), apps_dir
  ```

  Leave all existing test functions unchanged.

- [ ] **Step 4: Run the updated test files**

  ```bash
  pytest tests/test_web_api.py tests/test_web_apps.py -v
  ```

  Expected: all previously-passing tests PASS again.

- [ ] **Step 5: Commit**

  ```bash
  git add tests/test_web_api.py tests/test_web_apps.py
  git commit -m "test: update web test fixtures to insert test user and override get_current_user"
  ```

---

## Task 6: Apply Auth Dependencies to Existing Routes

**Files:**
- Modify: `web/routes/dashboard.py`
- Modify: `web/routes/api.py`
- Modify: `web/routes/apps.py`
- Modify: `web/routes/export.py`
- Modify: `web/routes/secrets.py`

- [ ] **Step 1: Update `web/routes/dashboard.py`**

  Add `get_current_user` dep to both route handlers and pass `current_user` to templates.

  Replace the entire file:

  ```python
  import json

  from fastapi import APIRouter, Depends, Request
  from fastapi.responses import HTMLResponse
  from fastapi.templating import Jinja2Templates
  import os

  from web.auth import get_current_user

  router = APIRouter()
  templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "..", "templates"))


  @router.get("/", response_class=HTMLResponse)
  async def dashboard(request: Request, environment: str = None,
                      user: dict = Depends(get_current_user)):
      from web.main import get_db, get_config, get_apps
      db = get_db()
      config = get_config()
      env = environment or config.get("default_environment", "production")
      envs = config.get("environments", {})
      summary = db.get_app_summary(env)

      known = {row["app"] for row in summary}
      for app_def in get_apps():
          if app_def["app"] not in known:
              summary.append({
                  "app": app_def["app"],
                  "total": 0, "passing": 0, "failing": 0, "unknown": 0,
                  "last_run": None, "last_run_id": None, "active_run_id": None,
              })

      return templates.TemplateResponse("dashboard.html", {
          "request": request,
          "summary": summary,
          "environment": env,
          "environments": envs,
          "current_user": user,
      })


  @router.get("/app/{app}/{environment}", response_class=HTMLResponse)
  async def app_detail(request: Request, app: str, environment: str,
                       run_id: str = None, user: dict = Depends(get_current_user)):
      from web.main import get_db, get_config, get_apps
      db = get_db()
      config = get_config()

      runs = db.get_recent_runs(app, environment)

      selected_run = None
      test_results = []
      history = {}

      if runs:
          selected_run = next((r for r in runs if r["id"] == run_id), runs[0])
          test_results = db.get_results_for_run(selected_run["id"])
          for tr in test_results:
              tr["step_log"] = json.loads(tr["step_log"] or "[]")
          test_names = [tr["test_name"] for tr in test_results]
          history = db.get_run_history_batch(app, environment, test_names)

      is_live = bool(selected_run and selected_run["status"] in ("pending", "running"))
      pending_test_names = []
      if is_live:
          completed_names = {tr["test_name"] for tr in test_results}
          app_def = next((a for a in get_apps() if a["app"] == app), None)
          if app_def:
              pending_test_names = [
                  t["name"] for t in app_def.get("tests", [])
                  if t["name"] not in completed_names
              ]

      return templates.TemplateResponse(request, "detail.html", {
          "request": request,
          "app": app,
          "environment": environment,
          "environments": config.get("environments", {}),
          "runs": runs,
          "selected_run": selected_run,
          "test_results": test_results,
          "history": history,
          "is_live": is_live,
          "pending_test_names": pending_test_names,
          "current_user": user,
      })
  ```

- [ ] **Step 2: Update `web/routes/api.py`**

  Add `require_role` to mutation endpoints. Replace import block and decorated functions:

  At the top, add the import:
  ```python
  from web.auth import require_role
  ```

  Update `trigger_run`:
  ```python
  @router.post("/runs", status_code=202)
  async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks,
                        user: dict = Depends(require_role("admin", "runner"))):
  ```

  Update `create_app_def`:
  ```python
  @router.post("/apps", status_code=201)
  async def create_app_def(req: AppDefRequest,
                           user: dict = Depends(require_role("admin", "runner"))):
  ```

  Update `update_app_def`:
  ```python
  @router.put("/apps/{app_name}", status_code=200)
  async def update_app_def(app_name: str, req: AppDefRequest,
                           user: dict = Depends(require_role("admin", "runner"))):
  ```

  Update `archive_app_def`:
  ```python
  @router.delete("/apps/{app_name}", status_code=200)
  async def archive_app_def(app_name: str,
                            user: dict = Depends(require_role("admin", "runner"))):
  ```

  Update `restore_app_def`:
  ```python
  @router.post("/apps/{app_name}/restore", status_code=200)
  async def restore_app_def(app_name: str,
                            user: dict = Depends(require_role("admin", "runner"))):
  ```

  Update `delete_app_permanently`:
  ```python
  @router.delete("/apps/{app_name}/permanent", status_code=204)
  async def delete_app_permanently(app_name: str,
                                   user: dict = Depends(require_role("admin", "runner"))) -> None:
  ```

  Also add `Depends` to the imports:
  ```python
  from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
  ```

- [ ] **Step 3: Update `web/routes/apps.py`**

  Add `require_role` to write endpoints. At the top add:
  ```python
  from fastapi import Depends
  from web.auth import get_current_user, require_role
  ```

  Update `_nav_ctx` to accept current_user:
  ```python
  def _nav_ctx(request: Request, user: dict = None) -> dict:
      from web.main import get_config
      config = get_config()
      return {
          "request": request,
          "environments": config.get("environments", {}),
          "environment": None,
          "current_user": user,
      }
  ```

  Update route signatures:
  ```python
  @router.get("/apps", response_class=HTMLResponse)
  async def apps_list(request: Request, user: dict = Depends(get_current_user)):
      ...
      return templates.TemplateResponse(request, "apps.html", {
          **_nav_ctx(request, user),
          "active_apps": active,
          "archived_apps": archived,
      })

  @router.get("/apps/new", response_class=HTMLResponse)
  async def apps_new(request: Request, user: dict = Depends(require_role("admin", "runner"))):
      return templates.TemplateResponse(request, "app_form.html", {
          **_nav_ctx(request, user),
          "mode": "create",
          "app_name": "",
          "app_def": {},
          "raw_yaml": "",
      })

  @router.get("/apps/{app_name}/edit", response_class=HTMLResponse)
  async def apps_edit(request: Request, app_name: str,
                      user: dict = Depends(require_role("admin", "runner"))):
      ...
      return templates.TemplateResponse(request, "app_form.html", {
          **_nav_ctx(request, user),
          "mode": "edit",
          "app_name": app_name,
          "app_def": app_def,
          "raw_yaml": raw_yaml,
      })
  ```

  Also delete the old read-only `/secrets` route (lines 68-78 in the original file):
  ```python
  # DELETE THIS ENTIRE BLOCK:
  @router.get("/secrets", response_class=HTMLResponse)
  async def secrets_page(request: Request):
      ...
  ```

- [ ] **Step 4: Update `web/routes/export.py`**

  Add import and dependency:
  ```python
  from fastapi import APIRouter, Depends, Response
  from web.auth import require_role
  ```

  Update route signature:
  ```python
  @router.get("/runs/{run_id}/export")
  async def export_run(run_id: str, format: str = "pdf",
                       user: dict = Depends(require_role("admin", "runner", "reporting"))):
  ```

- [ ] **Step 5: Update `web/routes/secrets.py`** (created in Phase 1)

  Add `require_role("admin")` to every endpoint. Open the file and:

  Add to imports:
  ```python
  from fastapi import Depends
  from web.auth import require_role
  ```

  Add `user: dict = Depends(require_role("admin"))` to each route's signature. For example:
  ```python
  @router.get("/secrets", response_class=HTMLResponse)
  async def secrets_list(request: Request, user: dict = Depends(require_role("admin"))):
      ...
      return templates.TemplateResponse(request, "secrets.html", {
          **_nav_ctx(request),
          "secrets": secrets,
          "current_user": user,
      })
  ```

  Apply the same pattern (`user: dict = Depends(require_role("admin"))` + pass `"current_user": user` in context) to every other route in `secrets.py`.

- [ ] **Step 6: Run all web tests**

  ```bash
  pytest tests/test_web_api.py tests/test_web_apps.py tests/test_web_auth_routes.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 7: Commit**

  ```bash
  git add web/routes/dashboard.py web/routes/api.py web/routes/apps.py \
          web/routes/export.py web/routes/secrets.py
  git commit -m "feat: apply get_current_user and require_role deps to all existing routes"
  ```

---

## Task 7: Create `web/routes/users.py`

**Files:**
- Create: `web/routes/users.py`
- Create: `tests/test_web_users.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_web_users.py`:

  ```python
  import pytest
  from fastapi import Request
  from fastapi.testclient import TestClient
  from harness.db import Database
  from web.main import create_app


  CONFIG = {
      "default_environment": "production",
      "environments": {"production": {"label": "Production"}},
  }

  ADMIN_USER = {
      "id": "admin-1", "username": "admin", "display_name": "Admin",
      "role": "admin", "is_active": 1,
  }


  @pytest.fixture
  def db(tmp_path):
      from datetime import datetime, timezone
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      d.insert_user({
          "id": "admin-1", "username": "admin", "display_name": "Admin",
          "email": None, "password_hash": None, "role": "admin",
          "auth_provider": "local", "role_override": 0, "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
      })
      return d


  @pytest.fixture
  def client(db, tmp_path):
      from web.auth import get_current_user
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))

      async def _admin(request: Request):
          return ADMIN_USER

      app.dependency_overrides[get_current_user] = _admin
      return TestClient(app)


  def test_users_list_renders(client, db):
      resp = client.get("/users")
      assert resp.status_code == 200
      assert b"admin" in resp.content


  def test_users_new_form_renders(client):
      resp = client.get("/users/new")
      assert resp.status_code == 200
      assert b"username" in resp.content.lower()


  def test_users_create_local(client, db):
      resp = client.post("/users/new", data={
          "username": "newuser",
          "display_name": "New User",
          "email": "",
          "password": "Secret123!",
          "confirm": "Secret123!",
          "role": "runner",
          "auth_provider": "local",
      })
      assert resp.status_code in (302, 303)
      assert db.get_user_by_username("newuser") is not None


  def test_users_create_password_mismatch(client):
      resp = client.post("/users/new", data={
          "username": "newuser",
          "display_name": "",
          "email": "",
          "password": "Secret123!",
          "confirm": "Different!",
          "role": "runner",
          "auth_provider": "local",
      })
      assert resp.status_code == 422
      assert b"match" in resp.content.lower() or b"error" in resp.content.lower()


  def test_users_edit_form_renders(client, db):
      resp = client.get("/users/admin-1/edit")
      assert resp.status_code == 200


  def test_users_edit_updates_role(client, db):
      resp = client.post("/users/admin-1/edit", data={
          "display_name": "Admin",
          "email": "",
          "role": "runner",
          "role_override": "0",
          "is_active": "1",
      })
      assert resp.status_code in (302, 303)
      user = db.get_user_by_id("admin-1")
      assert user["role"] == "runner"


  def test_users_requires_admin_role(db, tmp_path):
      from web.auth import get_current_user
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))

      async def _runner(request: Request):
          return {"id": "r1", "username": "runner", "display_name": "Runner",
                  "role": "runner", "is_active": 1}

      app.dependency_overrides[get_current_user] = _runner
      c = TestClient(app, follow_redirects=False)
      resp = c.get("/users")
      assert resp.status_code == 403
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_web_users.py -v 2>&1 | head -20
  ```

  Expected: 404s — `/users` not registered.

- [ ] **Step 3: Create `web/routes/users.py`**

  ```python
  import os
  import uuid
  from datetime import datetime, timezone

  from fastapi import APIRouter, Depends, Form, HTTPException, Request
  from fastapi.responses import HTMLResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates

  from web.auth import require_role

  router = APIRouter()
  templates = Jinja2Templates(
      directory=os.path.join(os.path.dirname(__file__), "..", "templates")
  )

  _ROLES = ["admin", "runner", "reporting", "read_only"]


  def _nav_ctx(request: Request, user: dict) -> dict:
      from web.main import get_config
      config = get_config()
      return {
          "request": request,
          "environments": config.get("environments", {}),
          "environment": None,
          "current_user": user,
      }


  @router.get("/users", response_class=HTMLResponse)
  async def users_list(request: Request, user: dict = Depends(require_role("admin"))):
      from web.main import get_db
      users = get_db().list_users()
      return templates.TemplateResponse(request, "users.html", {
          **_nav_ctx(request, user),
          "users": users,
          "roles": _ROLES,
      })


  @router.get("/users/new", response_class=HTMLResponse)
  async def users_new(request: Request, user: dict = Depends(require_role("admin"))):
      return templates.TemplateResponse(request, "user_form.html", {
          **_nav_ctx(request, user),
          "mode": "create",
          "target": None,
          "roles": _ROLES,
          "error": None,
      })


  @router.post("/users/new")
  async def users_create(
      request: Request,
      username: str = Form(...),
      display_name: str = Form(""),
      email: str = Form(""),
      password: str = Form(""),
      confirm: str = Form(""),
      role: str = Form("read_only"),
      auth_provider: str = Form("local"),
      user: dict = Depends(require_role("admin")),
  ):
      from web.main import get_db
      db = get_db()

      ctx = {
          **_nav_ctx(request, user),
          "mode": "create", "target": None, "roles": _ROLES, "error": None,
      }

      if auth_provider == "local":
          if password != confirm:
              ctx["error"] = "Passwords do not match."
              return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)
          if len(password) < 8:
              ctx["error"] = "Password must be at least 8 characters."
              return templates.TemplateResponse(request, "user_form.html", ctx, status_code=422)

      if db.get_user_by_username(username.strip()):
          ctx["error"] = f"Username '{username}' is already taken."
          return templates.TemplateResponse(request, "user_form.html", ctx, status_code=409)

      from passlib.hash import bcrypt
      password_hash = bcrypt.hash(password) if auth_provider == "local" and password else None

      db.insert_user({
          "id": str(uuid.uuid4()),
          "username": username.strip(),
          "display_name": display_name.strip() or username.strip(),
          "email": email.strip() or None,
          "password_hash": password_hash,
          "role": role,
          "auth_provider": auth_provider,
          "role_override": 0,
          "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(),
          "last_login_at": None,
      })
      return RedirectResponse("/users", status_code=303)


  @router.get("/users/{user_id}/edit", response_class=HTMLResponse)
  async def users_edit(request: Request, user_id: str,
                       user: dict = Depends(require_role("admin"))):
      from web.main import get_db
      target = get_db().get_user_by_id(user_id)
      if not target:
          raise HTTPException(status_code=404)
      return templates.TemplateResponse(request, "user_form.html", {
          **_nav_ctx(request, user),
          "mode": "edit",
          "target": target,
          "roles": _ROLES,
          "error": None,
      })


  @router.post("/users/{user_id}/edit")
  async def users_update(
      request: Request,
      user_id: str,
      display_name: str = Form(""),
      email: str = Form(""),
      role: str = Form("read_only"),
      role_override: str = Form("0"),
      is_active: str = Form("1"),
      new_password: str = Form(""),
      confirm_password: str = Form(""),
      user: dict = Depends(require_role("admin")),
  ):
      from web.main import get_db
      db = get_db()
      target = db.get_user_by_id(user_id)
      if not target:
          raise HTTPException(status_code=404)

      updates: dict = {
          "display_name": display_name.strip() or target["display_name"],
          "email": email.strip() or None,
          "role": role,
          "role_override": int(role_override),
          "is_active": int(is_active),
      }

      if new_password:
          if new_password != confirm_password:
              return templates.TemplateResponse(request, "user_form.html", {
                  **_nav_ctx(request, user),
                  "mode": "edit", "target": target, "roles": _ROLES,
                  "error": "Passwords do not match.",
              }, status_code=422)
          from passlib.hash import bcrypt
          updates["password_hash"] = bcrypt.hash(new_password)

      db.update_user(user_id, **updates)
      return RedirectResponse("/users", status_code=303)
  ```

- [ ] **Step 4: Register the router in `web/main.py`**

  Add after the existing `app.include_router(secrets_router)` line:

  ```python
  from web.routes.users import router as users_router
  app.include_router(users_router)
  ```

- [ ] **Step 5: Run users tests**

  ```bash
  pytest tests/test_web_users.py -v
  ```

  Expected: 8 tests PASS (templates will return 500 until created in Task 9 — create stub templates first if needed).

  If tests fail with `TemplateNotFound`, create `web/templates/users.html` and `web/templates/user_form.html` as stubs (full versions in Task 9):

  ```bash
  echo "users" > web/templates/users.html
  echo "user_form" > web/templates/user_form.html
  ```

  Then re-run:
  ```bash
  pytest tests/test_web_users.py -v
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add web/routes/users.py web/main.py tests/test_web_users.py
  git commit -m "feat: add /users CRUD routes (admin-only)"
  ```

---

## Task 8: Create `web/routes/admin.py`

**Files:**
- Create: `web/routes/admin.py`
- Create: `tests/test_web_admin.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_web_admin.py`:

  ```python
  import pytest
  from unittest.mock import patch
  from fastapi import Request
  from fastapi.testclient import TestClient
  from harness.db import Database
  from web.main import create_app


  CONFIG = {
      "default_environment": "production",
      "environments": {"production": {"label": "Production"}},
      "auth": {"ldap": {"enabled": False}},
  }

  ADMIN_USER = {
      "id": "admin-1", "username": "admin", "display_name": "Admin",
      "role": "admin", "is_active": 1,
  }


  @pytest.fixture
  def db(tmp_path):
      from datetime import datetime, timezone
      d = Database(str(tmp_path / "test.db"))
      d.init_schema()
      d.insert_user({
          "id": "admin-1", "username": "admin", "display_name": "Admin",
          "email": None, "password_hash": None, "role": "admin",
          "auth_provider": "local", "role_override": 0, "is_active": 1,
          "created_at": datetime.now(timezone.utc).isoformat(), "last_login_at": None,
      })
      return d


  @pytest.fixture
  def client(db, tmp_path):
      from web.auth import get_current_user
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))

      async def _admin(request: Request):
          return ADMIN_USER

      app.dependency_overrides[get_current_user] = _admin
      return TestClient(app)


  def test_ldap_config_page_renders(client):
      resp = client.get("/admin/ldap")
      assert resp.status_code == 200


  def test_ldap_config_save_updates_config(client, tmp_path):
      resp = client.post("/admin/ldap", data={
          "enabled": "1",
          "server": "ldap://dc.corp.com",
          "port": "389",
          "use_tls": "0",
          "base_dn": "DC=corp,DC=com",
          "user_search_filter": "(sAMAccountName={username})",
          "group_search_base": "OU=Groups,DC=corp,DC=com",
          "group_attribute": "memberOf",
          "default_role": "read_only",
          "role_map_json": '{"CN=Admins,DC=corp,DC=com": "admin"}',
      })
      assert resp.status_code in (302, 303)


  def test_ldap_test_connection_failure(client):
      with patch("web.routes.admin.ldap_authenticate", return_value=None):
          resp = client.post("/admin/ldap/test", json={
              "server": "ldap://dc.corp.com",
              "port": 389,
              "use_tls": False,
              "base_dn": "DC=corp,DC=com",
              "test_username": "testuser",
              "test_password": "testpass",
          })
      assert resp.status_code == 200
      data = resp.json()
      assert data["ok"] is False


  def test_admin_requires_admin_role(db, tmp_path):
      from web.auth import get_current_user
      app = create_app(db=db, config=CONFIG, apps_dir=str(tmp_path / "apps"))

      async def _runner(request: Request):
          return {"id": "r1", "username": "runner", "role": "runner", "is_active": 1}

      app.dependency_overrides[get_current_user] = _runner
      c = TestClient(app, follow_redirects=False)
      resp = c.get("/admin/ldap")
      assert resp.status_code == 403
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_web_admin.py -v 2>&1 | head -15
  ```

  Expected: failures — `/admin/ldap` not registered.

- [ ] **Step 3: Create `web/routes/admin.py`**

  ```python
  import json
  import os

  from fastapi import APIRouter, Depends, Form, Request
  from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates

  from web.auth import require_role

  router = APIRouter(prefix="/admin")
  templates = Jinja2Templates(
      directory=os.path.join(os.path.dirname(__file__), "..", "templates")
  )

  _ROLES = ["admin", "runner", "reporting", "read_only"]


  def _nav_ctx(request: Request, user: dict) -> dict:
      from web.main import get_config
      config = get_config()
      return {
          "request": request,
          "environments": config.get("environments", {}),
          "environment": None,
          "current_user": user,
      }


  @router.get("/ldap", response_class=HTMLResponse)
  async def ldap_config_get(request: Request,
                            user: dict = Depends(require_role("admin"))):
      from web.main import get_config
      config = get_config()
      ldap_cfg = config.get("auth", {}).get("ldap", {})
      return templates.TemplateResponse(request, "admin_ldap.html", {
          **_nav_ctx(request, user),
          "ldap": ldap_cfg,
          "roles": _ROLES,
          "saved": request.query_params.get("saved") == "1",
      })


  @router.post("/ldap")
  async def ldap_config_post(
      request: Request,
      enabled: str = Form("0"),
      server: str = Form(""),
      port: str = Form("389"),
      use_tls: str = Form("0"),
      base_dn: str = Form(""),
      user_search_filter: str = Form("(sAMAccountName={username})"),
      group_search_base: str = Form(""),
      group_attribute: str = Form("memberOf"),
      default_role: str = Form("read_only"),
      role_map_json: str = Form("{}"),
      user: dict = Depends(require_role("admin")),
  ):
      from web.main import get_config
      import yaml

      config = get_config()

      try:
          role_map = json.loads(role_map_json) if role_map_json.strip() else {}
      except json.JSONDecodeError:
          role_map = {}

      ldap_section = {
          "enabled": enabled == "1",
          "server": server.strip(),
          "port": int(port) if port.strip().isdigit() else 389,
          "use_tls": use_tls == "1",
          "base_dn": base_dn.strip(),
          "user_search_filter": user_search_filter.strip(),
          "group_search_base": group_search_base.strip(),
          "group_attribute": group_attribute.strip(),
          "role_map": role_map,
          "default_role": default_role,
      }

      if "auth" not in config:
          config["auth"] = {}
      config["auth"]["ldap"] = ldap_section

      # Persist to config.yaml if it exists on disk.
      config_path = "config.yaml"
      if os.path.exists(config_path):
          with open(config_path, "w") as f:
              yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

      return RedirectResponse("/admin/ldap?saved=1", status_code=303)


  @router.post("/ldap/test")
  async def ldap_test_connection(
      request: Request,
      user: dict = Depends(require_role("admin")),
  ):
      """AJAX endpoint: attempts an LDAP auth with supplied test credentials.

      Accepts JSON body:
        server, port, use_tls, base_dn, test_username, test_password
      Returns JSON: {"ok": bool, "message": str}
      """
      body = await request.json()

      ldap_cfg = {
          "enabled": True,
          "server": body.get("server", ""),
          "port": body.get("port", 389),
          "use_tls": body.get("use_tls", False),
          "base_dn": body.get("base_dn", ""),
          "user_search_filter": "(sAMAccountName={username})",
          "group_search_base": "",
          "group_attribute": "memberOf",
          "role_map": {},
          "default_role": "read_only",
      }

      try:
          result = ldap_authenticate(
              body.get("test_username", ""),
              body.get("test_password", ""),
              ldap_cfg,
          )
          if result is not None:
              return JSONResponse({"ok": True, "message": f"Connected as {result['username']}"})
          return JSONResponse({"ok": False, "message": "Bind failed — check credentials."})
      except Exception as exc:
          return JSONResponse({"ok": False, "message": str(exc)})


  def ldap_authenticate(username, password, ldap_cfg):
      from harness.auth_manager import ldap_authenticate as _ldap_auth
      return _ldap_auth(username, password, ldap_cfg)
  ```

- [ ] **Step 4: Register the router in `web/main.py`**

  Add after `app.include_router(users_router)`:

  ```python
  from web.routes.admin import router as admin_router
  app.include_router(admin_router)
  ```

- [ ] **Step 5: Create stub template and run tests**

  ```bash
  echo "admin_ldap" > web/templates/admin_ldap.html
  pytest tests/test_web_admin.py -v
  ```

  Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

  ```bash
  git add web/routes/admin.py web/main.py tests/test_web_admin.py
  git commit -m "feat: add /admin/ldap LDAP config and test-connection routes"
  ```

---

## Task 9: Create Management Templates

Replace the stub templates with real HTML. These extend `base.html` which will be updated in Task 10.

**Files:**
- Write: `web/templates/users.html`
- Write: `web/templates/user_form.html`
- Write: `web/templates/admin_ldap.html`

- [ ] **Step 1: Write `web/templates/users.html`**

  ```html
  {% extends "base.html" %}
  {% block content %}
  <div class="toolbar">
    <h1>Users</h1>
    <a href="/users/new" class="btn btn-primary btn-sm">+ New user</a>
  </div>
  <table class="table">
    <thead>
      <tr>
        <th>Username</th>
        <th>Display name</th>
        <th>Role</th>
        <th>Provider</th>
        <th>Status</th>
        <th>Last login</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for u in users %}
      <tr>
        <td>{{ u.username }}</td>
        <td>{{ u.display_name or "—" }}</td>
        <td>
          <span class="badge" style="background:rgba(59,130,246,.15);color:#60a5fa;">
            {{ u.role }}
          </span>
        </td>
        <td>{{ u.auth_provider }}</td>
        <td>
          {% if u.is_active %}
            <span class="badge badge-pass">active</span>
          {% else %}
            <span class="badge badge-fail">inactive</span>
          {% endif %}
        </td>
        <td style="color:#718096;font-size:.8rem;">
          {{ u.last_login_at[:16] if u.last_login_at else "never" }}
        </td>
        <td>
          <a href="/users/{{ u.id }}/edit" class="btn btn-sm">Edit</a>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" class="empty">No users yet.</td></tr>
      {% endfor %}
    </tbody>
  </table>
  {% endblock %}
  ```

- [ ] **Step 2: Write `web/templates/user_form.html`**

  ```html
  {% extends "base.html" %}
  {% block content %}
  <div class="breadcrumb">
    <a href="/users">Users</a> /
    {% if mode == "create" %}New user{% else %}Edit {{ target.username }}{% endif %}
  </div>
  <h1 style="margin-bottom:1.5rem;">
    {% if mode == "create" %}Create user{% else %}Edit {{ target.username }}{% endif %}
  </h1>
  {% if error %}
  <p style="color:#f87171;margin-bottom:1rem;">{{ error }}</p>
  {% endif %}
  <form method="post" style="max-width:500px;">
    {% if mode == "create" %}
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Username *</label>
      <input name="username" required
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Auth provider</label>
      <select name="auth_provider" id="auth_provider"
              style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                     border-radius:6px;color:#e2e8f0;font-size:.875rem;"
              onchange="togglePassword(this.value)">
        <option value="local">Local</option>
        <option value="ldap">LDAP (created on first login)</option>
      </select>
    </div>
    {% endif %}
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Display name</label>
      <input name="display_name" value="{{ target.display_name if target else '' }}"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Email</label>
      <input name="email" type="email" value="{{ target.email if target and target.email else '' }}"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Role</label>
      <select name="role"
              style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                     border-radius:6px;color:#e2e8f0;font-size:.875rem;">
        {% for r in roles %}
          <option value="{{ r }}" {% if (target and target.role == r) or (not target and r == 'read_only') %}selected{% endif %}>
            {{ r }}
          </option>
        {% endfor %}
      </select>
    </div>
    {% if mode == "edit" and target and target.auth_provider == "ldap" %}
    <div style="margin-bottom:1rem;">
      <label style="display:flex;align-items:center;gap:.5rem;color:#a0aec0;cursor:pointer;">
        <input type="checkbox" name="role_override" value="1"
               {% if target.role_override %}checked{% endif %}>
        Override LDAP role sync for this user
      </label>
    </div>
    {% endif %}
    {% if mode == "edit" %}
    <div style="margin-bottom:1rem;">
      <label style="display:flex;align-items:center;gap:.5rem;color:#a0aec0;cursor:pointer;">
        <input type="checkbox" name="is_active" value="1"
               {% if target.is_active %}checked{% endif %}>
        Active
      </label>
    </div>
    {% endif %}
    <div id="password-section">
      <div style="margin-bottom:1rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">
          {% if mode == "create" %}Password *{% else %}New password (leave blank to keep current){% endif %}
        </label>
        <input name="{% if mode == 'create' %}password{% else %}new_password{% endif %}"
               type="password" {% if mode == "create" %}required{% endif %}
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div style="margin-bottom:1.5rem;">
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Confirm password</label>
        <input name="{% if mode == 'create' %}confirm{% else %}confirm_password{% endif %}"
               type="password"
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
    </div>
    <div style="display:flex;gap:.75rem;">
      <button type="submit" class="btn btn-primary">
        {% if mode == "create" %}Create user{% else %}Save changes{% endif %}
      </button>
      <a href="/users" class="btn">Cancel</a>
    </div>
  </form>
  <script>
  function togglePassword(provider) {
    document.getElementById("password-section").style.display =
      provider === "local" ? "block" : "none";
  }
  </script>
  {% endblock %}
  ```

- [ ] **Step 3: Write `web/templates/admin_ldap.html`**

  ```html
  {% extends "base.html" %}
  {% block content %}
  <div class="breadcrumb"><a href="/">Dashboard</a> / LDAP Configuration</div>
  <h1 style="margin-bottom:1.5rem;">LDAP / Active Directory</h1>
  {% if saved %}
  <p style="color:#4ade80;margin-bottom:1rem;">Settings saved.</p>
  {% endif %}
  <form method="post" action="/admin/ldap" style="max-width:600px;">
    <div style="margin-bottom:1rem;">
      <label style="display:flex;align-items:center;gap:.5rem;color:#a0aec0;cursor:pointer;">
        <input type="checkbox" name="enabled" value="1"
               {% if ldap.get("enabled") %}checked{% endif %}>
        Enable LDAP authentication
      </label>
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Server URL</label>
      <input name="server" value="{{ ldap.get('server','') }}" placeholder="ldap://dc.company.com"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;">
      <div>
        <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Port</label>
        <input name="port" value="{{ ldap.get('port', 389) }}"
               style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                      border-radius:6px;color:#e2e8f0;font-size:.875rem;">
      </div>
      <div>
        <label style="display:flex;align-items:center;gap:.5rem;color:#a0aec0;
                      margin-top:1.5rem;cursor:pointer;">
          <input type="checkbox" name="use_tls" value="1"
                 {% if ldap.get("use_tls") %}checked{% endif %}>
          Use TLS (LDAPS)
        </label>
      </div>
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Base DN</label>
      <input name="base_dn" value="{{ ldap.get('base_dn','') }}" placeholder="DC=company,DC=com"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">User search filter</label>
      <input name="user_search_filter" value="{{ ldap.get('user_search_filter','(sAMAccountName={username})') }}"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Group search base</label>
      <input name="group_search_base" value="{{ ldap.get('group_search_base','') }}"
             placeholder="OU=Groups,DC=company,DC=com"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Group attribute</label>
      <input name="group_attribute" value="{{ ldap.get('group_attribute','memberOf') }}"
             style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                    border-radius:6px;color:#e2e8f0;font-size:.875rem;">
    </div>
    <div style="margin-bottom:1rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">Default role</label>
      <select name="default_role"
              style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                     border-radius:6px;color:#e2e8f0;font-size:.875rem;">
        {% for r in roles %}
          <option value="{{ r }}" {% if ldap.get('default_role','read_only') == r %}selected{% endif %}>
            {{ r }}
          </option>
        {% endfor %}
      </select>
    </div>
    <div style="margin-bottom:1.5rem;">
      <label style="display:block;color:#a0aec0;margin-bottom:.25rem;">
        Role map (JSON: group DN → role)
      </label>
      <textarea name="role_map_json" rows="4"
                style="width:100%;padding:.5rem .75rem;background:#1a1f2e;border:1px solid #2d3748;
                       border-radius:6px;color:#e2e8f0;font-size:.8rem;font-family:monospace;">
{{ ldap.get('role_map', {}) | tojson(indent=2) }}</textarea>
    </div>
    <div style="display:flex;gap:.75rem;align-items:center;margin-bottom:2rem;">
      <button type="submit" class="btn btn-primary">Save settings</button>
      <button type="button" class="btn btn-sm" onclick="testConnection()">Test connection</button>
    </div>
  </form>
  <div id="test-result" style="display:none;padding:.75rem;border-radius:6px;font-size:.875rem;"></div>
  <script>
  async function testConnection() {
    const form = document.querySelector("form");
    const data = {
      server: form.server.value,
      port: parseInt(form.port.value, 10),
      use_tls: form.use_tls.checked,
      base_dn: form.base_dn.value,
      test_username: prompt("Test username:") || "",
      test_password: prompt("Test password:") || "",
    };
    const res = await fetch("/admin/ldap/test", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(data),
    });
    const json = await res.json();
    const el = document.getElementById("test-result");
    el.style.display = "block";
    el.style.background = json.ok ? "rgba(34,197,94,.1)" : "rgba(239,68,68,.1)";
    el.style.color = json.ok ? "#4ade80" : "#f87171";
    el.textContent = json.message;
  }
  </script>
  {% endblock %}
  ```

- [ ] **Step 4: Run all tests to confirm nothing broke**

  ```bash
  pytest tests/test_web_users.py tests/test_web_admin.py -v
  ```

  Expected: all tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add web/templates/users.html web/templates/user_form.html web/templates/admin_ldap.html
  git commit -m "feat: add users, user_form, and admin_ldap templates"
  ```

---

## Task 10: Update `base.html` with User Menu and Role-Conditional Nav

**Files:**
- Modify: `web/templates/base.html`

- [ ] **Step 1: Replace `web/templates/base.html`**

  ```html
  <!DOCTYPE html>
  <html lang="en">
  <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Testing Harness</title>
    <link rel="stylesheet" href="/static/style.css">
  </head>
  <body>
    <nav class="nav">
      <div style="display:flex; align-items:center; gap:1.5rem;">
        <a href="/" class="nav-brand">Testing Harness</a>
        <a href="/apps" style="color:#a0aec0; font-size:.875rem;">Apps</a>
        {% if current_user and current_user.role in ("admin",) %}
          <a href="/secrets" style="color:#a0aec0; font-size:.875rem;">Secrets</a>
          <a href="/users" style="color:#a0aec0; font-size:.875rem;">Users</a>
          <a href="/admin/ldap" style="color:#a0aec0; font-size:.875rem;">LDAP</a>
        {% endif %}
      </div>
      <div style="display:flex; align-items:center; gap:1rem;">
        {% if current_user %}
          <span style="font-size:.8rem; color:#718096;">
            {{ current_user.display_name or current_user.username }}
            <span class="badge" style="background:rgba(59,130,246,.15);color:#60a5fa;margin-left:.25rem;">
              {{ current_user.role }}
            </span>
          </span>
          <form method="post" action="/auth/logout" style="display:inline;">
            <button type="submit" class="btn btn-sm" style="border-color:#4a5568;color:#a0aec0;">
              Sign out
            </button>
          </form>
        {% endif %}
        <div class="env-switcher">
          {% for key, env in (environments or {}).items() %}
            <a href="?environment={{ key }}"
               class="env-btn {% if environment == key %}active{% endif %}">
              {{ env.label }}
            </a>
          {% endfor %}
        </div>
      </div>
    </nav>
    <main class="main">
      {% block content %}{% endblock %}
    </main>
  </body>
  </html>
  ```

- [ ] **Step 2: Run the full test suite**

  ```bash
  pytest --ignore=tests/test_browser_screenshot.py -x -q
  ```

  Expected: all tests PASS. The `current_user` variable is now used in `base.html`; templates that don't pass it will render with no user menu (acceptable for login/setup pages that don't extend base.html).

- [ ] **Step 3: Commit**

  ```bash
  git add web/templates/base.html
  git commit -m "feat: update base.html with user menu and role-conditional nav links"
  ```

---

## Task 11: Add `auth:` section to `config.yaml`

**Files:**
- Modify: `config.yaml`

- [ ] **Step 1: Add auth section to `config.yaml`**

  Open `config.yaml` and add after the `browser:` section:

  ```yaml
  auth:
    session_hours: 8
    secure_cookie: false   # set true for HTTPS deployments
    ldap:
      enabled: false
      server: ldap://dc.company.com
      port: 389
      use_tls: false
      base_dn: "DC=company,DC=com"
      user_search_filter: "(sAMAccountName={username})"
      group_search_base: "OU=Groups,DC=company,DC=com"
      group_attribute: "memberOf"
      role_map:
        # "CN=Harness-Admins,OU=Groups,DC=company,DC=com": admin
        # "CN=Harness-Runners,OU=Groups,DC=company,DC=com": runner
        # "CN=Harness-Reporting,OU=Groups,DC=company,DC=com": reporting
        # "CN=Harness-ReadOnly,OU=Groups,DC=company,DC=com": read_only
      default_role: read_only
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add config.yaml
  git commit -m "config: add auth section with LDAP template (disabled by default)"
  ```

---

## Task 12: Smoke Test

- [ ] **Step 1: Run the full test suite**

  ```bash
  pytest --ignore=tests/test_browser_screenshot.py -q
  ```

  Expected: all tests PASS, no errors.

- [ ] **Step 2: Start the server and verify first-run setup**

  ```bash
  cd C:/webtestingharness
  python -m web.main
  ```

  Open `http://localhost:9552` in a browser.

  Expected:
  - Redirects to `/setup` (first run — no users in DB)
  - Fill in username/password → creates admin, logs you in, redirects to dashboard
  - Nav bar shows username + role badge + "Sign out" button
  - "Secrets", "Users", "LDAP" links visible for admin role
  - `/auth/login` page is accessible directly

- [ ] **Step 3: Verify role gating**

  Create a second user with `runner` role via `/users/new`.
  Sign out, sign back in as runner.

  Expected:
  - "Secrets", "Users", "LDAP" nav links are hidden
  - `GET /secrets` returns 403 page (not JSON)
  - `GET /users` returns 403 page
  - Dashboard, Apps, run trigger all work normally

- [ ] **Step 4: Final commit**

  ```bash
  git add -A
  git commit -m "feat: Phase 2 complete — authentication, RBAC, and user/LDAP management"
  ```
