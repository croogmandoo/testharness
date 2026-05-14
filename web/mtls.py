"""Optional mutual-TLS enforcement for worker endpoints (workers-v2 feature
#5b).

mTLS off by default. Enable via ``config.yaml``::

    worker:
      mtls:
        enabled: true
        client_ca_path: /etc/harness/client-ca.pem

When enabled, every request whose path begins with the configured prefix
(``/api/runner/`` by default) must present a client certificate. The TLS
handshake itself is performed by the ASGI server (uvicorn) — see
``docs/guides/deployment.md`` for the ``--ssl-cert-reqs 2`` invocation.

This module's job is the post-handshake checks:

  1. Reject any matching request that has no client cert at all (403).
  2. Extract the subject CN and stash it on ``request.state.worker_cn``
     so handlers + audit log entries can reference it.

The client cert is read from ``request.scope["client_cert"]`` — uvicorn
populates this when started with ``--ssl-cert-reqs 2``. We never validate
the cert chain ourselves; that's TLS's job. The middleware only asserts
that a verified cert is present.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

_log = logging.getLogger("harness.mtls")


class MTLSEnforcementMiddleware(BaseHTTPMiddleware):
    """Require a verified client certificate for requests under
    ``require_for_prefix``. Does NOT terminate TLS; relies on the ASGI
    server's already-verified handshake state.
    """

    def __init__(self, app, *, require_for_prefix: str = "/api/runner/",
                  client_ca_path: Optional[str] = None):
        super().__init__(app)
        self.require_for_prefix = require_for_prefix
        # client_ca_path is read for the log line + admin doc; uvicorn is
        # what actually loads/verifies against it. Kept here so the
        # /admin/runners page can surface "mTLS active, CA: ..." later.
        self.client_ca_path = client_ca_path

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(self.require_for_prefix):
            return await call_next(request)

        client_cert = request.scope.get("client_cert")
        if not client_cert:
            _log.warning(
                "mtls: rejected %s %s — no client cert presented",
                request.method, path,
            )
            return JSONResponse(
                {"detail": "client certificate required"},
                status_code=403,
            )

        cn = _extract_cn(client_cert)
        if cn:
            try:
                request.state.worker_cn = cn
            except Exception:
                pass
        return await call_next(request)


def _extract_cn(client_cert) -> Optional[str]:
    """Extract the subject Common Name from a parsed client certificate.

    The shape of ``client_cert`` depends on the ASGI server. Uvicorn
    exposes a dict-like ``{"subject": ((("commonName", "eu-worker-1"),),),
    ...}``. We handle that plus the case where it's already a string.
    """
    if isinstance(client_cert, str):
        return client_cert or None
    if isinstance(client_cert, dict):
        subject = client_cert.get("subject")
        if subject:
            for rdn in subject:
                # rdn is a tuple of attribute tuples, e.g. (("commonName", "x"),)
                for attr in rdn:
                    if isinstance(attr, (list, tuple)) and len(attr) == 2:
                        key, value = attr
                        if key in ("commonName", "CN"):
                            return value
    return None
