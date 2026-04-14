# Phase 1: Secrets Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the read-only `.env`-checked secrets page with an encrypted secrets store where admins can add, edit, and delete secrets via the web UI, with values stored Fernet-encrypted in SQLite and injected into `os.environ` before each test run.

**Architecture:** A `SecretsStore` class in `harness/secrets_store.py` owns key-file management (auto-generates `data/secret.key` on first run), encryption/decryption via HKDF-derived Fernet, and all DB CRUD. The web layer gets CRUD routes at `/secrets`. The runner calls `inject_to_env()` before executing tests so existing `$VAR` YAML references keep working unchanged.

**Tech Stack:** `cryptography` (Fernet + HKDF), SQLite via existing `Database` class, FastAPI HTML routes, Jinja2 templates.

---

## Task 1: Add Dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add all four new packages** (adding all now avoids a second requirements.txt conflict in Phase 2)

  Open `requirements.txt` and append these four lines:

  ```
  cryptography>=42.0.0
  passlib[bcrypt]>=1.7.4
  itsdangerous>=2.1.2
  ldap3>=2.9.1
  ```

- [ ] **Step 2: Install dependencies**

  ```bash
  cd C:/webtestingharness
  pip install cryptography>=42.0.0 "passlib[bcrypt]>=1.7.4" "itsdangerous>=2.1.2" "ldap3>=2.9.1"
  ```

  Expected: all four packages install without errors.

- [ ] **Step 3: Verify cryptography import works**

  ```bash
  python -c "from cryptography.fernet import Fernet; from cryptography.hazmat.primitives.kdf.hkdf import HKDF; print('ok')"
  ```

  Expected: prints `ok`.

- [ ] **Step 4: Commit**

  ```bash
  git add requirements.txt
  git commit -m "chore: add cryptography, passlib, itsdangerous, ldap3 dependencies"
  ```

---

## Task 2: Add Secrets Table Schema + DB CRUD Methods

**Files:**
- Modify: `harness/db.py`
- Create: `tests/test_secrets_db.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_secrets_db.py`:

  ```python
  import pytest
  from harness.db import Database


  def test_secrets_table_exists(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      with db._connect() as conn:
          tables = {r["name"] for r in conn.execute(
              "SELECT name FROM sqlite_master WHERE type='table'"
          ).fetchall()}
      assert "secrets" in tables


  def test_upsert_and_get_secret(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("MY_VAR", "encrypted_blob", "a test secret", None)
      row = db.get_secret("MY_VAR")
      assert row is not None
      assert row["name"] == "MY_VAR"
      assert row["encrypted_value"] == "encrypted_blob"
      assert row["description"] == "a test secret"


  def test_upsert_updates_existing(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("UP_VAR", "old_blob", "old desc", None)
      db.upsert_secret("UP_VAR", "new_blob", "new desc", None)
      row = db.get_secret("UP_VAR")
      assert row["encrypted_value"] == "new_blob"
      assert row["description"] == "new desc"


  def test_list_secrets(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("VAR_A", "blob_a", "", None)
      db.upsert_secret("VAR_B", "blob_b", "note", None)
      secrets = db.list_secrets()
      names = [s["name"] for s in secrets]
      assert "VAR_A" in names
      assert "VAR_B" in names
      # values are NOT returned by list_secrets
      for s in secrets:
          assert "encrypted_value" not in s


  def test_delete_secret(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("DEL_VAR", "blob", "", None)
      db.delete_secret("DEL_VAR")
      assert db.get_secret("DEL_VAR") is None


  def test_get_secret_by_id(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("ID_VAR", "blob", "desc", None)
      row = db.get_secret("ID_VAR")
      found = db.get_secret_by_id(row["id"])
      assert found["name"] == "ID_VAR"


  def test_update_secret_meta(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      db.upsert_secret("META_VAR", "blob", "old desc", None)
      db.update_secret_meta("META_VAR", "new desc")
      row = db.get_secret("META_VAR")
      assert row["description"] == "new desc"
      assert row["encrypted_value"] == "blob"  # unchanged
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_secrets_db.py -v
  ```

  Expected: all 7 tests FAIL with `AttributeError` or `AssertionError`.

- [ ] **Step 3: Add secrets table to SCHEMA in `harness/db.py`**

  In `harness/db.py`, locate the `SCHEMA` string (line 6) and add the secrets table. The full updated `SCHEMA` string:

  ```python
  SCHEMA = """
  CREATE TABLE IF NOT EXISTS runs (
      id           TEXT PRIMARY KEY,
      app          TEXT NOT NULL,
      environment  TEXT NOT NULL,
      triggered_by TEXT NOT NULL,
      status       TEXT NOT NULL,
      started_at   TEXT,
      finished_at  TEXT
  );
  CREATE TABLE IF NOT EXISTS test_results (
      id          TEXT PRIMARY KEY,
      run_id      TEXT NOT NULL REFERENCES runs(id),
      app         TEXT NOT NULL,
      environment TEXT NOT NULL,
      test_name   TEXT NOT NULL,
      status      TEXT NOT NULL,
      error_msg   TEXT,
      step_log    TEXT,
      screenshot  TEXT,
      duration_ms INTEGER,
      finished_at TEXT
  );
  CREATE INDEX IF NOT EXISTS idx_test_results_lookup
      ON test_results(app, environment, test_name, finished_at);
  CREATE TABLE IF NOT EXISTS app_state (
      app         TEXT NOT NULL,
      environment TEXT NOT NULL,
      test_name   TEXT NOT NULL,
      state       TEXT NOT NULL,
      since       TEXT NOT NULL,
      PRIMARY KEY (app, environment, test_name)
  );
  CREATE TABLE IF NOT EXISTS secrets (
      id              TEXT PRIMARY KEY,
      name            TEXT UNIQUE NOT NULL,
      encrypted_value TEXT NOT NULL,
      description     TEXT,
      created_at      TEXT NOT NULL,
      updated_at      TEXT NOT NULL,
      updated_by      TEXT
  );
  """
  ```

- [ ] **Step 4: Add CRUD methods to the `Database` class in `harness/db.py`**

  Add the following methods inside the `Database` class, after `get_recent_runs`:

  ```python
  # ---- Secrets ----

  def upsert_secret(self, name: str, encrypted_value: str,
                    description: str, updated_by: Optional[str]) -> None:
      from datetime import datetime, timezone
      import uuid
      now = datetime.now(timezone.utc).isoformat()
      with self._connect() as conn:
          conn.execute(
              """INSERT INTO secrets (id, name, encrypted_value, description, created_at, updated_at, updated_by)
                 VALUES (?, ?, ?, ?, ?, ?, ?)
                 ON CONFLICT(name) DO UPDATE SET
                     encrypted_value = excluded.encrypted_value,
                     description     = excluded.description,
                     updated_at      = excluded.updated_at,
                     updated_by      = excluded.updated_by""",
              (str(uuid.uuid4()), name, encrypted_value, description, now, now, updated_by),
          )

  def get_secret(self, name: str) -> Optional[dict]:
      with self._connect() as conn:
          row = conn.execute(
              "SELECT * FROM secrets WHERE name=?", (name,)
          ).fetchone()
      return dict(row) if row else None

  def get_secret_by_id(self, secret_id: str) -> Optional[dict]:
      with self._connect() as conn:
          row = conn.execute(
              "SELECT * FROM secrets WHERE id=?", (secret_id,)
          ).fetchone()
      return dict(row) if row else None

  def delete_secret(self, name: str) -> None:
      with self._connect() as conn:
          conn.execute("DELETE FROM secrets WHERE name=?", (name,))

  def list_secrets(self) -> list:
      """Return name, description, timestamps only — never the encrypted value."""
      with self._connect() as conn:
          rows = conn.execute(
              "SELECT id, name, description, created_at, updated_at, updated_by "
              "FROM secrets ORDER BY name"
          ).fetchall()
      return [dict(r) for r in rows]

  def update_secret_meta(self, name: str, description: str) -> None:
      """Update description only, leaving encrypted_value unchanged."""
      from datetime import datetime, timezone
      now = datetime.now(timezone.utc).isoformat()
      with self._connect() as conn:
          conn.execute(
              "UPDATE secrets SET description=?, updated_at=? WHERE name=?",
              (description, now, name),
          )
  ```

- [ ] **Step 5: Run tests to confirm they pass**

  ```bash
  pytest tests/test_secrets_db.py -v
  ```

  Expected: all 7 tests PASS.

- [ ] **Step 6: Run existing test suite to check for regressions**

  ```bash
  pytest tests/ -v --ignore=tests/test_secrets_db.py
  ```

  Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

  ```bash
  git add harness/db.py tests/test_secrets_db.py
  git commit -m "feat: add secrets table schema and DB CRUD methods"
  ```

---

## Task 3: Implement `harness/secrets_store.py`

**Files:**
- Create: `harness/secrets_store.py`
- Create: `tests/test_secrets_store.py`

- [ ] **Step 1: Write the failing tests**

  Create `tests/test_secrets_store.py`:

  ```python
  import os
  import pytest
  from harness.db import Database
  from harness.secrets_store import SecretsStore


  def make_store(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      return SecretsStore(db, key_path=str(tmp_path / "secret.key"))


  def test_key_file_created_on_first_run(tmp_path):
      key_path = str(tmp_path / "secret.key")
      assert not os.path.exists(key_path)
      make_store(tmp_path)
      assert os.path.exists(key_path)


  def test_key_file_not_recreated_on_second_run(tmp_path):
      make_store(tmp_path)
      with open(str(tmp_path / "secret.key"), "rb") as f:
          first_key = f.read()
      make_store(tmp_path)
      with open(str(tmp_path / "secret.key"), "rb") as f:
          second_key = f.read()
      assert first_key == second_key


  def test_set_and_get_roundtrip(tmp_path):
      store = make_store(tmp_path)
      store.set("MY_SECRET", "supersecret")
      assert store.get("MY_SECRET") == "supersecret"


  def test_value_encrypted_at_rest(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      store = SecretsStore(db, key_path=str(tmp_path / "secret.key"))
      store.set("RAW_CHECK", "plaintext_value")
      row = db.get_secret("RAW_CHECK")
      assert row["encrypted_value"] != "plaintext_value"
      assert "plaintext_value" not in row["encrypted_value"]


  def test_get_missing_raises_key_error(tmp_path):
      store = make_store(tmp_path)
      with pytest.raises(KeyError, match="DOES_NOT_EXIST"):
          store.get("DOES_NOT_EXIST")


  def test_delete_removes_secret(tmp_path):
      store = make_store(tmp_path)
      store.set("DEL_ME", "value")
      store.delete("DEL_ME")
      with pytest.raises(KeyError):
          store.get("DEL_ME")


  def test_list_does_not_expose_values(tmp_path):
      store = make_store(tmp_path)
      store.set("LISTED", "should_not_appear", description="a note")
      items = store.list()
      assert len(items) == 1
      assert items[0]["name"] == "LISTED"
      assert items[0]["description"] == "a note"
      assert "encrypted_value" not in items[0]
      assert "should_not_appear" not in str(items)


  def test_inject_to_env(tmp_path, monkeypatch):
      store = make_store(tmp_path)
      store.set("INJECT_TEST_VAR", "injected_value")
      monkeypatch.delenv("INJECT_TEST_VAR", raising=False)
      store.inject_to_env()
      assert os.environ.get("INJECT_TEST_VAR") == "injected_value"


  def test_inject_does_not_crash_on_empty_store(tmp_path):
      store = make_store(tmp_path)
      store.inject_to_env()  # should not raise


  def test_two_stores_same_key_file_can_decrypt(tmp_path):
      """A store created later with the same key file can read values set earlier."""
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      key_path = str(tmp_path / "secret.key")
      store1 = SecretsStore(db, key_path=key_path)
      store1.set("PERSIST_VAR", "persist_me")
      store2 = SecretsStore(db, key_path=key_path)
      assert store2.get("PERSIST_VAR") == "persist_me"


  def test_session_signing_key_is_bytes(tmp_path):
      store = make_store(tmp_path)
      assert isinstance(store.session_signing_key, bytes)
      assert len(store.session_signing_key) == 32
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_secrets_store.py -v
  ```

  Expected: all 11 tests FAIL with `ModuleNotFoundError: No module named 'harness.secrets_store'`.

- [ ] **Step 3: Create `harness/secrets_store.py`**

  ```python
  import os
  import base64
  import secrets as _secrets
  from cryptography.fernet import Fernet
  from cryptography.hazmat.primitives.kdf.hkdf import HKDF
  from cryptography.hazmat.primitives import hashes
  from harness.db import Database

  _DEFAULT_KEY_PATH = os.path.join("data", "secret.key")


  def _load_or_create_key(key_path: str) -> bytes:
      """Return raw 32-byte key material, creating the key file if absent."""
      if os.path.exists(key_path):
          with open(key_path, "rb") as f:
              return base64.urlsafe_b64decode(f.read().strip())
      raw = _secrets.token_bytes(32)
      parent = os.path.dirname(os.path.abspath(key_path))
      os.makedirs(parent, exist_ok=True)
      with open(key_path, "wb") as f:
          f.write(base64.urlsafe_b64encode(raw))
      return raw


  def _hkdf(key_material: bytes, info: bytes) -> bytes:
      return HKDF(
          algorithm=hashes.SHA256(),
          length=32,
          salt=None,
          info=info,
      ).derive(key_material)


  class SecretsStore:
      """Encrypted secrets backed by SQLite. Key material lives in a separate file."""

      def __init__(self, db: Database, key_path: str = _DEFAULT_KEY_PATH):
          self._db = db
          raw = _load_or_create_key(key_path)
          fernet_key = base64.urlsafe_b64encode(_hkdf(raw, b"harness-secrets-v1"))
          self._fernet = Fernet(fernet_key)
          self._session_key = _hkdf(raw, b"harness-sessions-v1")

      @property
      def session_signing_key(self) -> bytes:
          """32-byte key for signing session cookies (used by web/auth.py in Phase 2)."""
          return self._session_key

      def set(self, name: str, value: str, description: str = "",
              updated_by: str = None) -> None:
          encrypted = self._fernet.encrypt(value.encode()).decode()
          self._db.upsert_secret(name, encrypted, description, updated_by)

      def get(self, name: str) -> str:
          row = self._db.get_secret(name)
          if row is None:
              raise KeyError(f"Secret '{name}' not found")
          return self._fernet.decrypt(row["encrypted_value"].encode()).decode()

      def delete(self, name: str) -> None:
          self._db.delete_secret(name)

      def list(self) -> list:
          return self._db.list_secrets()

      def inject_to_env(self) -> None:
          """Decrypt all secrets and set them in os.environ for the current process."""
          with self._db._connect() as conn:
              rows = conn.execute(
                  "SELECT name, encrypted_value FROM secrets"
              ).fetchall()
          for row in rows:
              try:
                  value = self._fernet.decrypt(row["encrypted_value"].encode()).decode()
                  os.environ[row["name"]] = value
              except Exception:
                  pass  # corrupted entry — skip silently, don't crash the run
  ```

- [ ] **Step 4: Run tests to confirm they pass**

  ```bash
  pytest tests/test_secrets_store.py -v
  ```

  Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

  ```bash
  git add harness/secrets_store.py tests/test_secrets_store.py
  git commit -m "feat: implement SecretsStore with Fernet encryption and key file management"
  ```

---

## Task 4: Wire `inject_to_env()` into the Test Runner

**Files:**
- Modify: `harness/runner.py`
- Modify: `web/routes/api.py`

- [ ] **Step 1: Add optional `secrets_store` parameter to `run_app` in `harness/runner.py`**

  Replace the `run_app` function signature and first line (lines 20-29) with:

  ```python
  async def run_app(app_def: dict, environment: str, triggered_by: str,
                    db: Database, config: dict, run_id: str = None,
                    secrets_store=None) -> str:
      if secrets_store is not None:
          secrets_store.inject_to_env()
      if run_id:
          run = Run(id=run_id, app=app_def["app"], environment=environment, triggered_by=triggered_by)
      else:
          run = Run(app=app_def["app"], environment=environment, triggered_by=triggered_by)
          db.insert_run(run)
  ```

  The rest of the function body is unchanged.

- [ ] **Step 2: Pass `secrets_store` from the API route**

  In `web/routes/api.py`, update the `trigger_run` route. Find the two `background_tasks.add_task` calls (lines 48 and in the loop) and add `secrets_store` argument:

  ```python
  @router.post("/runs", status_code=202)
  async def trigger_run(req: RunRequest, background_tasks: BackgroundTasks):
      from web.main import get_db, get_config, get_apps, get_secrets_store
      db = get_db()
      config = get_config()
      all_apps = get_apps()
      secrets_store = get_secrets_store()

      target_apps = [a for a in all_apps if req.app is None or a["app"] == req.app]
      if req.app and not target_apps:
          if db.is_run_active(req.app, req.environment):
              raise HTTPException(
                  status_code=409,
                  detail=f"A run for {req.app} ({req.environment}) is already in progress"
              )
          raise HTTPException(status_code=404, detail=f"App '{req.app}' not found")

      queued = []
      for app_def in target_apps:
          if db.is_run_active(app_def["app"], req.environment):
              if req.app:
                  raise HTTPException(
                      status_code=409,
                      detail=f"A run for {app_def['app']} ({req.environment}) is already in progress"
                  )
              continue
          run = Run(app=app_def["app"], environment=req.environment, triggered_by=req.triggered_by)
          db.insert_run(run)
          background_tasks.add_task(
              run_app, app_def, req.environment, req.triggered_by, db, config,
              run_id=run.id, secrets_store=secrets_store
          )
          queued.append(run.id)

      return {"run_id": queued[0] if queued else None, "run_ids": queued, "apps": [a["app"] for a in target_apps]}
  ```

- [ ] **Step 3: Run existing runner tests to confirm no regressions**

  ```bash
  pytest tests/test_runner.py tests/test_api_engine.py -v
  ```

  Expected: all existing tests still pass (`secrets_store=None` default means nothing changes for tests that don't provide it).

- [ ] **Step 4: Commit**

  ```bash
  git add harness/runner.py web/routes/api.py
  git commit -m "feat: inject secrets into os.environ before each test run"
  ```

---

## Task 5: Implement `web/routes/secrets.py`

**Files:**
- Create: `web/routes/secrets.py`
- Create: `tests/test_web_secrets.py`

- [ ] **Step 1: Write failing route tests**

  Create `tests/test_web_secrets.py`:

  ```python
  import pytest
  from fastapi.testclient import TestClient
  from harness.db import Database
  from harness.secrets_store import SecretsStore
  from web.main import create_app
  import web.main as web_main


  @pytest.fixture
  def client(tmp_path):
      db = Database(str(tmp_path / "test.db"))
      db.init_schema()
      store = SecretsStore(db, key_path=str(tmp_path / "secret.key"))
      app = create_app(db=db, config={}, apps_dir=str(tmp_path / "apps"))
      web_main._secrets_store = store
      with TestClient(app, follow_redirects=True) as c:
          yield c


  def test_secrets_list_empty(client):
      resp = client.get("/secrets")
      assert resp.status_code == 200
      assert "No secrets" in resp.text


  def test_create_secret(client):
      resp = client.post("/secrets/new", data={
          "name": "TEST_VAR",
          "value": "s3cr3t",
          "description": "a test secret",
      })
      assert resp.status_code == 200  # follows redirect to /secrets
      assert "TEST_VAR" in resp.text


  def test_create_secret_name_uppercased(client):
      client.post("/secrets/new", data={"name": "lower_var", "value": "val", "description": ""})
      resp = client.get("/secrets")
      assert "LOWER_VAR" in resp.text


  def test_create_duplicate_shows_error(client):
      client.post("/secrets/new", data={"name": "DUP_VAR", "value": "v1", "description": ""})
      resp = client.post("/secrets/new", data={"name": "DUP_VAR", "value": "v2", "description": ""})
      assert resp.status_code == 200
      assert "already exists" in resp.text


  def test_create_missing_value_shows_error(client):
      resp = client.post("/secrets/new", data={"name": "NO_VAL", "value": "", "description": ""})
      assert resp.status_code == 200
      assert "required" in resp.text.lower()


  def test_delete_secret(client):
      client.post("/secrets/new", data={"name": "DEL_VAR", "value": "val", "description": ""})
      resp = client.get("/secrets")
      assert "DEL_VAR" in resp.text
      # Get the secret id from the db directly
      from web.main import get_db
      db = get_db()
      row = db.get_secret("DEL_VAR")
      client.post(f"/secrets/{row['id']}/delete")
      resp2 = client.get("/secrets")
      assert "DEL_VAR" not in resp2.text


  def test_edit_updates_description_without_value(client):
      client.post("/secrets/new", data={"name": "EDIT_VAR", "value": "original", "description": "old"})
      from web.main import get_db
      db = get_db()
      row = db.get_secret("EDIT_VAR")
      client.post(f"/secrets/{row['id']}/edit", data={"value": "", "description": "new desc"})
      updated = db.get_secret("EDIT_VAR")
      assert updated["description"] == "new desc"
      # Value should be unchanged — store.get() should still return original
      from web.main import get_secrets_store
      assert get_secrets_store().get("EDIT_VAR") == "original"
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  pytest tests/test_web_secrets.py -v
  ```

  Expected: FAIL with `ImportError` or 404 responses.

- [ ] **Step 3: Create `web/routes/secrets.py`**

  ```python
  from fastapi import APIRouter, Request, Form
  from fastapi.responses import HTMLResponse, RedirectResponse
  from fastapi.templating import Jinja2Templates
  import os

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
      }


  @router.get("/secrets", response_class=HTMLResponse)
  async def secrets_list(request: Request):
      from web.main import get_secrets_store
      store = get_secrets_store()
      secrets = store.list() if store else []
      return templates.TemplateResponse(request, "secrets.html", {
          **_nav_ctx(request),
          "secrets": secrets,
      })


  @router.get("/secrets/new", response_class=HTMLResponse)
  async def secret_new(request: Request):
      return templates.TemplateResponse(request, "secret_form.html", {
          **_nav_ctx(request),
          "mode": "create",
          "secret": {},
          "error": None,
      })


  @router.post("/secrets/new")
  async def secret_create(
      request: Request,
      name: str = Form(...),
      value: str = Form(...),
      description: str = Form(""),
  ):
      from web.main import get_db, get_secrets_store
      store = get_secrets_store()
      name = name.strip().upper()
      if not name:
          return templates.TemplateResponse(request, "secret_form.html", {
              **_nav_ctx(request),
              "mode": "create",
              "secret": {"name": name, "description": description},
              "error": "Name is required.",
          })
      if not value:
          return templates.TemplateResponse(request, "secret_form.html", {
              **_nav_ctx(request),
              "mode": "create",
              "secret": {"name": name, "description": description},
              "error": "Value is required when creating a secret.",
          })
      db = get_db()
      if db.get_secret(name):
          return templates.TemplateResponse(request, "secret_form.html", {
              **_nav_ctx(request),
              "mode": "create",
              "secret": {"name": name, "description": description},
              "error": f"A secret named '{name}' already exists. Use Edit to update its value.",
          })
      store.set(name, value, description)
      return RedirectResponse("/secrets", status_code=303)


  @router.get("/secrets/{secret_id}/edit", response_class=HTMLResponse)
  async def secret_edit(request: Request, secret_id: str):
      from web.main import get_db
      row = get_db().get_secret_by_id(secret_id)
      if not row:
          return RedirectResponse("/secrets", status_code=303)
      return templates.TemplateResponse(request, "secret_form.html", {
          **_nav_ctx(request),
          "mode": "edit",
          "secret": row,
          "error": None,
      })


  @router.post("/secrets/{secret_id}/edit")
  async def secret_update(
      request: Request,
      secret_id: str,
      value: str = Form(""),
      description: str = Form(""),
  ):
      from web.main import get_db, get_secrets_store
      db = get_db()
      row = db.get_secret_by_id(secret_id)
      if not row:
          return RedirectResponse("/secrets", status_code=303)
      if value:
          get_secrets_store().set(row["name"], value, description)
      else:
          db.update_secret_meta(row["name"], description)
      return RedirectResponse("/secrets", status_code=303)


  @router.post("/secrets/{secret_id}/delete")
  async def secret_delete(request: Request, secret_id: str):
      from web.main import get_db, get_secrets_store
      db = get_db()
      row = db.get_secret_by_id(secret_id)
      if row:
          get_secrets_store().delete(row["name"])
      return RedirectResponse("/secrets", status_code=303)
  ```

- [ ] **Step 4: Run tests** (will still fail — templates don't exist yet, that's fine for now)

  ```bash
  pytest tests/test_web_secrets.py -v 2>&1 | head -30
  ```

  Expected: FAIL with `TemplateNotFound` errors (routes exist, templates don't yet).

- [ ] **Step 5: Commit the route**

  ```bash
  git add web/routes/secrets.py tests/test_web_secrets.py
  git commit -m "feat: add secrets CRUD routes"
  ```

---

## Task 6: Replace Secrets Templates

**Files:**
- Modify: `web/templates/secrets.html`
- Create: `web/templates/secret_form.html`

- [ ] **Step 1: Replace `web/templates/secrets.html`**

  Overwrite the existing file:

  ```html
  {% extends "base.html" %}
  {% block content %}
  <div class="toolbar">
    <h1>Secrets</h1>
    <a href="/secrets/new" class="btn btn-primary">+ New Secret</a>
  </div>

  <p style="color:#718096; font-size:.875rem; margin-bottom:1.5rem;">
    Secret values are encrypted at rest and never displayed.
    They are injected as environment variables before each test run,
    so <code>$VAR_NAME</code> references in your app YAML files resolve automatically.
  </p>

  {% if not secrets %}
  <p class="empty">No secrets stored yet. <a href="/secrets/new">Add one.</a></p>
  {% else %}
  <table class="table">
    <thead>
      <tr>
        <th>Name</th>
        <th>Description</th>
        <th>Last Updated</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {% for s in secrets %}
      <tr>
        <td style="font-family:monospace;">{{ s.name }}</td>
        <td style="color:#718096; font-size:.875rem;">{{ s.description or "—" }}</td>
        <td style="color:#718096; font-size:.875rem;">{{ s.updated_at[:10] if s.updated_at else "—" }}</td>
        <td style="display:flex; gap:.5rem; justify-content:flex-end;">
          <a href="/secrets/{{ s.id }}/edit" class="btn btn-sm">Edit</a>
          <form method="post" action="/secrets/{{ s.id }}/delete" style="margin:0;"
                onsubmit="return confirm('Delete secret {{ s.name | e }}? This cannot be undone.')">
            <button type="submit" class="btn btn-sm"
                    style="border-color:#ef4444; color:#f87171;">Delete</button>
          </form>
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}
  {% endblock %}
  ```

- [ ] **Step 2: Create `web/templates/secret_form.html`**

  ```html
  {% extends "base.html" %}
  {% block content %}
  <div class="breadcrumb">
    <a href="/secrets">Secrets</a> / {{ "Edit" if mode == "edit" else "New Secret" }}
  </div>
  <h1 style="margin-bottom:1.5rem;">
    {{ ("Edit " + secret.name) if mode == "edit" else "New Secret" }}
  </h1>

  {% if error %}
  <div style="background:rgba(239,68,68,.15); color:#f87171; padding:.75rem 1rem;
              border-radius:6px; margin-bottom:1rem; font-size:.875rem;">
    {{ error }}
  </div>
  {% endif %}

  <form method="post" style="max-width:500px;">
    <div style="margin-bottom:1rem;">
      <label style="display:block; margin-bottom:.25rem; font-size:.8rem; color:#a0aec0;">
        Variable Name *
      </label>
      <input type="text" name="name" class="search"
             value="{{ secret.name | default('') | e }}"
             {{ 'readonly style="opacity:.6; cursor:not-allowed;"' if mode == "edit" else '' }}
             placeholder="MY_SECRET_VAR"
             style="max-width:100%; text-transform:uppercase;"
             oninput="this.value=this.value.toUpperCase()">
      <p style="font-size:.75rem; color:#718096; margin:.25rem 0 0;">
        Uppercase letters, digits, and underscores only. Referenced in YAML as <code>$NAME</code>.
      </p>
    </div>

    <div style="margin-bottom:1rem;">
      <label style="display:block; margin-bottom:.25rem; font-size:.8rem; color:#a0aec0;">
        Description <span style="color:#718096;">(optional)</span>
      </label>
      <input type="text" name="description" class="search"
             value="{{ secret.description | default('') | e }}"
             placeholder="What this secret is for"
             style="max-width:100%;">
    </div>

    <div style="margin-bottom:1.5rem;">
      <label style="display:block; margin-bottom:.25rem; font-size:.8rem; color:#a0aec0;">
        Value {% if mode == "create" %}*{% endif %}
      </label>
      <input type="password" name="value" class="search"
             placeholder="{{ 'Leave blank to keep current value' if mode == 'edit' else 'Enter secret value' }}"
             autocomplete="new-password"
             style="max-width:100%;">
      {% if mode == "edit" %}
      <p style="font-size:.75rem; color:#718096; margin:.25rem 0 0;">
        Leave blank to keep the current value unchanged.
      </p>
      {% endif %}
    </div>

    <div style="display:flex; gap:.75rem;">
      <button type="submit" class="btn btn-primary">Save</button>
      <a href="/secrets" class="btn">Cancel</a>
    </div>
  </form>
  {% endblock %}
  ```

- [ ] **Step 3: Run tests to confirm they now pass**

  ```bash
  pytest tests/test_web_secrets.py -v
  ```

  Expected: all 7 tests PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add web/templates/secrets.html web/templates/secret_form.html
  git commit -m "feat: add secrets management templates"
  ```

---

## Task 7: Wire Router into `web/main.py`, Remove Old `/secrets` from `apps.py`

**Files:**
- Modify: `web/main.py`
- Modify: `web/routes/apps.py`

- [ ] **Step 1: Add `_secrets_store` global and `get_secrets_store()` to `web/main.py`**

  After the existing globals at the top of `web/main.py` (after `_apps_dir: str = "apps"`), add:

  ```python
  _secrets_store = None


  def get_secrets_store():
      return _secrets_store
  ```

- [ ] **Step 2: Initialize `SecretsStore` inside `create_app()` in `web/main.py`**

  Inside `create_app()`, after the `_db.init_schema()` call, add:

  ```python
      global _secrets_store
      from harness.secrets_store import SecretsStore
      key_path = os.path.join("data", "secret.key")
      _secrets_store = SecretsStore(db=_db, key_path=key_path)
  ```

- [ ] **Step 3: Register the secrets router in `create_app()` in `web/main.py`**

  After the existing router registrations (after `app.include_router(export_router)`), add:

  ```python
      from web.routes.secrets import router as secrets_router
      app.include_router(secrets_router)
  ```

- [ ] **Step 4: Remove the old `/secrets` route from `web/routes/apps.py`**

  Delete the entire `secrets_page` function and its route (lines 68–78):

  ```python
  # DELETE these lines entirely:
  @router.get("/secrets", response_class=HTMLResponse)
  async def secrets_page(request: Request):
      from web.main import get_apps_dir
      apps_dir = get_apps_dir()
      var_names = get_known_vars(apps_dir=apps_dir)
      # Check presence only — never read values
      vars_with_status = [(v, os.environ.get(v[1:]) is not None) for v in var_names]
      return templates.TemplateResponse(request, "secrets.html", {
          "vars": vars_with_status,
          **_nav_ctx(request),
      })
  ```

  Also remove the unused import at the top of `apps.py`:

  ```python
  # DELETE this line:
  from harness.app_manager import get_known_vars
  ```

- [ ] **Step 5: Run the full test suite**

  ```bash
  pytest tests/ -v
  ```

  Expected: all tests pass. Any test that previously imported `get_known_vars` from `apps.py` will need to be checked.

- [ ] **Step 6: Commit**

  ```bash
  git add web/main.py web/routes/apps.py
  git commit -m "feat: wire secrets router, remove legacy read-only secrets route"
  ```

---

## Task 8: Write `docs/secrets-migration.md`

**Files:**
- Create: `docs/secrets-migration.md`

- [ ] **Step 1: Create the migration guide**

  ```markdown
  # Secrets Migration Guide

  The harness stores encrypted secrets in `data/harness.db`. The encryption key lives in
  a separate file: `data/secret.key`. Both files must be preserved when moving to a new
  host. Without the key file, stored secrets are permanently unreadable.

  ---

  ## What to Back Up

  | File | Contains | Required for recovery |
  |------|----------|-----------------------|
  | `data/harness.db` | Encrypted secret blobs + all run history | Yes |
  | `data/secret.key` | Master key material | **Yes — critical** |

  Back these up together. Treat `secret.key` with the same care as a private key or
  password file — do not commit it to version control, and restrict file permissions
  (`chmod 600 data/secret.key` on Linux/macOS).

  ---

  ## Moving to a New Windows Server

  1. Stop the harness Windows service.
  2. Copy the entire `data\` directory to the same path on the new host.
  3. Install the harness on the new host.
  4. Start the service. The harness reads the existing `data\secret.key` and all secrets
     decrypt correctly.

  ---

  ## Moving to a New Docker Container

  Mount `data/` as a named volume so it survives container replacements:

  ```yaml
  # docker-compose.yml
  services:
    harness:
      image: your-harness-image
      volumes:
        - harness-data:/app/data

  volumes:
    harness-data:
  ```

  The volume persists `harness.db` and `secret.key` across `docker compose up --build`
  and image upgrades. Never use a bind mount to a directory that gets wiped on deploy.

  ---

  ## Lost Key File Recovery

  If `data/secret.key` is lost there is no way to decrypt the stored secrets.

  Recovery procedure:

  1. Delete `data/secret.key` (or it is already missing).
  2. Start the harness — it auto-generates a new `secret.key`.
  3. Navigate to **Secrets** in the web UI.
  4. Re-enter all secret values. The names are visible (stored as plaintext); only the
     values are unrecoverable.

  To reduce recovery effort, keep an offline backup of secret values in a password
  manager or secrets vault (e.g., Bitwarden, 1Password, Azure Key Vault).

  ---

  ## Rotating the Key

  Key rotation is not yet automated. To rotate:

  1. Export all secret values from a running instance (use the harness API or note them
     from your password manager backup).
  2. Stop the harness.
  3. Delete `data/secret.key`.
  4. Start the harness — new key is generated.
  5. Re-enter all secret values via the Secrets UI.

  ---

  ## File Permissions

  On Linux/macOS, restrict access to the key file immediately after first run:

  ```bash
  chmod 600 data/secret.key
  chown harness-user:harness-user data/secret.key
  ```

  On Windows, set NTFS permissions so only the service account can read the file.
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add docs/secrets-migration.md
  git commit -m "docs: add secrets key file migration and backup guide"
  ```

---

## Task 9: Smoke Test End-to-End

- [ ] **Step 1: Start the harness**

  ```bash
  python -m web.main
  ```

- [ ] **Step 2: Navigate to http://localhost:9552/secrets**

  Expected: Secrets page loads, shows empty state "No secrets stored yet."

- [ ] **Step 3: Add a secret**

  Click "+ New Secret". Fill in:
  - Name: `TEST_SECRET`
  - Value: `hello123`
  - Description: `smoke test`

  Click Save. Expected: redirect back to `/secrets`, `TEST_SECRET` appears in the table.

- [ ] **Step 4: Edit the description without changing the value**

  Click Edit on `TEST_SECRET`. Leave Value blank, change Description to "updated desc". Save.
  Expected: description updates, no error.

- [ ] **Step 5: Verify the value is still correct via the Python REPL**

  ```bash
  python -c "
  from harness.db import Database
  from harness.secrets_store import SecretsStore
  db = Database('data/harness.db')
  store = SecretsStore(db, 'data/secret.key')
  print(store.get('TEST_SECRET'))
  "
  ```

  Expected: prints `hello123`.

- [ ] **Step 6: Delete the secret and confirm it's gone**

  Click Delete on `TEST_SECRET`, confirm the browser confirm dialog. Expected: row removed.

- [ ] **Step 7: Verify `data/secret.key` exists**

  ```bash
  ls -la data/secret.key
  ```

  Expected: file exists, is non-empty.

- [ ] **Step 8: Run the full test suite one final time**

  ```bash
  pytest tests/ -v
  ```

  Expected: all tests pass.
