import base64
import hashlib
import os
import ssl
import tempfile
from typing import List

from harness.db import Database

BUNDLE_PATH = "data/ca-bundle.pem"


def get_ssl_context(db: Database) -> ssl.SSLContext:
    """Return system default SSL context with any stored CA certs appended.

    Raises ``ssl.SSLError`` if a stored cert is malformed — callers in the
    runner path catch this and surface the failed cert id in the run error.
    """
    ctx = ssl.create_default_context()
    certs = db.list_ca_certs()
    if certs:
        combined = "\n".join(c["pem_content"] for c in certs)
        ctx.load_verify_locations(cadata=combined)
    return ctx


def write_ca_bundle(db: Database, path: str = BUNDLE_PATH) -> None:
    """Write all CA certs to a PEM bundle file. Removes the file if no certs.

    Atomic — writes to a temp file in the same directory and ``os.replace()``
    onto the final path, so concurrent readers (e.g. Playwright reading
    ``SSL_CERT_FILE``) never see a truncated bundle. If no certs are
    stored, any existing bundle is removed.
    """
    certs = db.list_ca_certs()
    if not certs:
        if os.path.exists(path):
            os.remove(path)
        return
    target_dir = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".ca-bundle.", suffix=".pem.tmp",
                               dir=target_dir)
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(c["pem_content"] for c in certs))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise


def chromium_spki_args(path: str = BUNDLE_PATH) -> List[str]:
    """Return Chromium launch args that trust the CAs in the bundle.

    Chromium ignores ``SSL_CERT_FILE`` (that env var is OpenSSL-only) and on
    Linux uses NSS, which only sees CAs installed into ``~/.pki/nssdb``.
    Rather than mutate NSS at runtime (needs ``certutil`` in the image),
    pin trust via ``--ignore-certificate-errors-spki-list``: a comma-
    separated list of base64-encoded SHA-256 hashes over each cert's
    ``SubjectPublicKeyInfo`` DER. Chromium then accepts a TLS cert chain
    iff one of the chain's SPKIs matches a pinned hash -- so this trusts
    exactly the admin-uploaded CAs and nothing else (expired prod certs
    still fail; wrong-hostname certs still fail).

    Returns ``[]`` if the bundle file is missing or empty. Returns an
    error-free empty list (rather than raising) if any cert in the bundle
    is malformed, after letting the OpenSSL path raise -- this function is
    best-effort and shouldn't itself fail the browser run.
    """
    if not os.path.exists(path):
        return []
    try:
        # ``cryptography`` is a direct dep (requirements.txt). Import lazily
        # so ``harness.ssl_context`` keeps importing on the slim image if a
        # future trim ever drops cryptography.
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return []
    with open(path, "rb") as f:
        data = f.read()
    if not data.strip():
        return []
    try:
        certs = x509.load_pem_x509_certificates(data)
    except ValueError:
        return []
    hashes = []
    for cert in certs:
        spki_der = cert.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        digest = hashlib.sha256(spki_der).digest()
        hashes.append(base64.b64encode(digest).decode("ascii"))
    if not hashes:
        return []
    return [f"--ignore-certificate-errors-spki-list={','.join(hashes)}"]
