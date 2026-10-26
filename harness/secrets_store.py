"""
SecretsStore: encrypted secret storage using Fernet (AES-128-CBC + HMAC-SHA256).

Two keys are derived from the key file via HKDF:
  - Fernet key for encrypting secret values
  - Session signing key exposed as .session_signing_key for web/auth.py
"""
import base64
import logging
import os
import stat
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


def _read_key(key_path: str) -> bytes:
    _tighten_key_permissions(key_path)
    with open(key_path, "rb") as f:
        return base64.urlsafe_b64decode(f.read().strip())


def _load_or_create_key(key_path: str) -> bytes:
    if os.path.exists(key_path):
        return _read_key(key_path)
    parent = os.path.dirname(key_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = os.urandom(32)
    # O_EXCL: never clobber a key another process created meanwhile, and the
    # 0600 mode applies from the first byte (no chmod-after-write window).
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read_key(key_path)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.urlsafe_b64encode(raw))
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
