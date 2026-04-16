import os
import ssl
from harness.db import Database

BUNDLE_PATH = "data/ca-bundle.pem"


def get_ssl_context(db: Database) -> ssl.SSLContext:
    """Return system default SSL context with any stored CA certs appended."""
    ctx = ssl.create_default_context()
    certs = db.list_ca_certs()
    if certs:
        combined = "\n".join(c["pem_content"] for c in certs)
        ctx.load_verify_locations(cadata=combined)
    return ctx


def write_ca_bundle(db: Database, path: str = BUNDLE_PATH) -> None:
    """Write all CA certs to a PEM bundle file. Removes the file if no certs."""
    certs = db.list_ca_certs()
    if certs:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(c["pem_content"] for c in certs))
    elif os.path.exists(path):
        os.remove(path)
