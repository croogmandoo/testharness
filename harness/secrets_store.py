"""
SecretsStore: encrypted secret storage using Fernet (AES-128-CBC + HMAC-SHA256).

Two keys are derived from the key file via HKDF:
  - Fernet key for encrypting secret values
  - Session signing key exposed as .session_signing_key for web/auth.py
"""
import os
import base64
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes


def _hkdf(raw_key: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(raw_key)


def _load_or_create_key(key_path: str) -> bytes:
    if os.path.exists(key_path):
        with open(key_path, "rb") as f:
            return base64.urlsafe_b64decode(f.read().strip())
    parent = os.path.dirname(key_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    raw = os.urandom(32)
    with open(key_path, "wb") as f:
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

    def inject_to_env(self) -> None:
        import os as _os
        for row in self._db.list_secrets():
            try:
                value = self.get(row["name"])
                if value is not None:
                    _os.environ[row["name"]] = value
            except Exception:
                pass
