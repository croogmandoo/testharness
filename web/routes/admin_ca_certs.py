"""Admin CA certificate management routes."""
import sqlite3
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from web.auth import require_role
from web.csrf import csrf_token_for, verify_csrf
from web.templating import make_templates

router = APIRouter()
templates = make_templates()


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        "csrf_token": csrf_token_for(request),
        **extra,
    }


def _validate_pem(content: str) -> tuple[bool, str]:
    """Try to parse ``content`` as one or more PEM-encoded X.509 certs.

    Returns ``(ok, error_message)``. On success, ``error_message`` is
    empty.  This is the real validation gate — admins who paste typos,
    truncated files, or just text get rejected at upload time instead
    of crashing ``ssl.SSLContext.load_verify_locations()`` on the next
    test run.
    """
    if not content.strip():
        return False, "Certificate content is empty."
    if "-----BEGIN CERTIFICATE-----" not in content:
        return False, "Content must contain at least one -----BEGIN CERTIFICATE----- block."
    try:
        from cryptography import x509
        certs = x509.load_pem_x509_certificates(content.encode("utf-8"))
    except ValueError as e:
        return False, f"Invalid certificate: {e}"
    if not certs:
        return False, "No certificates parsed from input."
    return True, ""


@router.get("/admin/ca-certs", response_class=HTMLResponse)
async def ca_certs_list(
    request: Request,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    db = get_db()
    certs = db.list_ca_certs()
    return templates.TemplateResponse(
        request, "admin_ca_certs.html",
        _ctx(request, current_user, certs=certs, error=None),
    )


@router.post("/admin/ca-certs", response_class=HTMLResponse)
async def ca_certs_add(
    request: Request,
    name: str = Form(""),
    pem_content: str = Form(""),
    pem_file: UploadFile = File(None),
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()

    def _err(msg: str):
        certs = db.list_ca_certs()
        return templates.TemplateResponse(
            request, "admin_ca_certs.html",
            _ctx(request, current_user, certs=certs, error=msg),
            status_code=422,
        )

    # File upload takes priority over paste.
    if pem_file and pem_file.filename:
        raw = await pem_file.read()
        # Catch binary uploads (DER, PKCS#12, random files) before the
        # ``errors="replace"`` decode below mangles them into a useless
        # blob of U+FFFD that the validator can't diagnose.
        if b"-----BEGIN CERTIFICATE-----" not in raw:
            return _err(
                "Uploaded file does not contain a PEM certificate. "
                "DER/binary formats are not supported — convert to PEM first "
                "(`openssl x509 -in cert.der -inform DER -out cert.pem`).",
            )
        pem_content = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()

    name = name.strip()
    pem_content = pem_content.strip()

    if not name:
        return _err("Certificate name is required.")
    ok, err = _validate_pem(pem_content)
    if not ok:
        return _err(err)

    try:
        db.insert_ca_cert({
            "id": str(uuid.uuid4()),
            "name": name,
            "pem_content": pem_content,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "added_by": current_user["id"],
        })
    except sqlite3.IntegrityError:
        # Unique-name index — see migrations in harness/db/_base.py
        return _err(f"A certificate named {name!r} already exists.")
    write_ca_bundle(db)
    db.log_audit("ca_cert.add", user_id=current_user["id"],
                 username=current_user.get("username"), target=name)
    return RedirectResponse("/admin/ca-certs", status_code=303)


@router.post("/admin/ca-certs/{cert_id}/delete")
async def ca_certs_delete(
    request: Request,
    cert_id: str,
    current_user: dict = Depends(require_role("admin")),
    _csrf=Depends(verify_csrf),
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()
    cert = db.get_ca_cert(cert_id)
    if cert is None:
        raise HTTPException(status_code=404, detail="Certificate not found")
    db.delete_ca_cert(cert_id)
    write_ca_bundle(db)
    db.log_audit("ca_cert.delete", user_id=current_user["id"],
                 username=current_user.get("username"),
                 target=cert["name"])
    return RedirectResponse("/admin/ca-certs", status_code=303)
