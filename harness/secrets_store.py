"""
SecretsStore: encrypted secret storage using Fernet (AES-128-CBC + HMAC-SHA256).

Two keys are derived from the key file via HKDF:
  - Fernet key for encrypting secret values
  - Session signing key exposed as .session_signing_key for web/auth.py
"""
import base64
import binascii
import logging
import os
import stat
import tempfile
import time
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

_log = logging.getLogger(__name__)


def _hkdf(raw_key: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(raw_key)


def _tighten_key_permissions(key_path: str) -> None:
    """Best-effort: drop group/other access from an existing key file.

    Key files created before 0600 creation was enforced inherit the umask
    (often 0644). POSIX only; failures are logged, never fatal."""
    if os.name != "posix":
        return
    try:
        mode = stat.S_IMODE(os.stat(key_path).st_mode)
        if mode & 0o077:
            os.chmod(key_path, 0o600)
            _log.warning(
                "%s had permissions %o; tightened to 600", key_path, mode,
            )
    except OSError as exc:
        _log.warning("Could not check/tighten permissions on %s: %s", key_path, exc)


KEY_BYTES = 32
# How long to wait for a key file another process is still writing (only
# reachable on the non-hardlink fallback path) before giving up.
_KEY_READ_TIMEOUT = 2.0
_KEY_READ_INTERVAL = 0.05


def _decode_key(data: bytes) -> Optional[bytes]:
    try:
        raw = base64.urlsafe_b64decode(data.strip())
    except (binascii.Error, ValueError):
        return None
    return raw if len(raw) == KEY_BYTES else None


def _read_key(key_path: str) -> bytes:
    """Load and validate the key file.

    The key must decode to exactly 32 bytes. An empty or short key would
    still feed HKDF and yield keys derived from a public constant, so it is
    never accepted: after a short retry window (covering a concurrent first
    boot still writing the file) a clear error is raised instead."""
    _tighten_key_permissions(key_path)
    deadline = time.monotonic() + _KEY_READ_TIMEOUT
    while True:
        with open(key_path, "rb") as f:
            raw = _decode_key(f.read())
        if raw is not None:
            return raw
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Key file {key_path} is empty or corrupt (expected a base64-encoded "
                f"{KEY_BYTES}-byte key). Restore it from backup; deleting it makes "
                "stored secrets unrecoverable and invalidates all sessions."
            )
        time.sleep(_KEY_READ_INTERVAL)


def _create_key_exclusive(key_path: str, encoded: bytes) -> bool:
    """Fallback for filesystems without hard links: O_EXCL create at 0600.
    Returns False if the file already exists. A concurrent reader may see
    it half-written; ``_read_key``'s validated retry covers that."""
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    return True


def _load_or_create_key(key_path: str) -> bytes:
    if os.path.exists(key_path):
        return _read_key(key_path)
    parent = os.path.dirname(key_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = os.urandom(KEY_BYTES)
    encoded = base64.urlsafe_b64encode(raw)

    # Publish atomically: write the complete key to a private (0600) temp
    # file in the same directory, then hard-link it into place. link() fails
    # if the target exists, so concurrent first boots agree on one key, and
    # nobody can ever observe a partially written key file.
    # (os.replace would silently overwrite a key another process published.)
    fd, tmp_path = tempfile.mkstemp(prefix=".secret.key.", dir=parent or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp_path, key_path)
        except FileExistsError:
            return _read_key(key_path)
        except OSError as exc:
            _log.info("Hard links unsupported for %s (%s); using O_EXCL create",
                      key_path, exc)
            if not _create_key_exclusive(key_path, encoded):
                return _read_key(key_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return raw


class SecretsStore:
    def __init__(self, db, key_path: str = "data/secret.key"):
        raw = _load_or_create_key(key_path)
        fernet_key = base64.urlsafe_b64encode(_hkdf(raw, b"harness-secrets-v1"))
        self._fernet = Fernet(fernet_key)
        self._session_key = _hkdf(raw, b"harness-sessions-v1")
        self._db = db

    @property
    def session_signing_key(self) -> bytes:
        return self._session_key

    def set(self, name: str, value: str, description: Optional[str] = None,
            user_id: Optional[str] = None) -> None:
        encrypted = self._fernet.encrypt(value.encode()).decode()
        self._db.upsert_secret(name, encrypted, description=description, user_id=user_id)

    def get(self, name: str) -> Optional[str]:
        row = self._db.get_secret(name)
        if row is None:
            return None
        return self._fernet.decrypt(row["encrypted_value"].encode()).decode()

    def delete(self, name: str) -> None:
        self._db.delete_secret(name)

    def list(self) -> list:
        return self._db.list_secrets()

    def get_all_decrypted(self) -> dict:
        """Return all secrets as {name: plaintext} without writing to os.environ."""
        result = {}
        for row in self._db.list_secrets():
            try:
                value = self.get(row["name"])
                if value is not None:
                    result[row["name"]] = value
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to decrypt secret %r: %s", row["name"], e
                )
        return result

    def inject_to_env(self) -> None:
        """Deprecated — prefer ``get_all_decrypted()`` passed as ``extra_vars=``
        to resolvers. Mutating ``os.environ`` globally is no longer the
        supported pattern; this method is retained only for backwards
        compatibility with callers that haven't migrated yet.
        """
        import os as _os
        import logging
        _log = logging.getLogger(__name__)
        for row in self._db.list_secrets():
            try:
                value = self.get(row["name"])
                if value is not None:
                    _os.environ[row["name"]] = value
            except Exception as e:
                _log.warning("Failed to decrypt secret %r: %s", row["name"], e)
