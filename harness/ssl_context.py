import os
import ssl
import tempfile
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
