"""Admin CA certificate management routes."""
import os
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from web.auth import require_role

router = APIRouter()
templates = Jinja2Templates(
    directory=os.path.join(os.path.dirname(__file__), "..", "templates")
)


def _ctx(request: Request, current_user: dict, **extra) -> dict:
    from web.main import get_config
    config = get_config()
    return {
        "request": request,
        "environments": config.get("environments", {}),
        "environment": None,
        "current_user": current_user,
        **extra,
    }


def _valid_pem(content: str) -> bool:
    return "-----BEGIN CERTIFICATE-----" in content


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
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()

    # File upload takes priority over paste
    if pem_file and pem_file.filename:
        raw = await pem_file.read()
        pem_content = raw.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()

    name = name.strip()
    pem_content = pem_content.strip()

    def _err(msg: str):
        certs = db.list_ca_certs()
        return templates.TemplateResponse(
            request, "admin_ca_certs.html",
            _ctx(request, current_user, certs=certs, error=msg),
            status_code=422,
        )

    if not name:
        return _err("Certificate name is required.")
    if not _valid_pem(pem_content):
        return _err("Content must contain at least one -----BEGIN CERTIFICATE----- block.")

    db.insert_ca_cert({
        "id": str(uuid.uuid4()),
        "name": name,
        "pem_content": pem_content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "added_by": current_user["id"],
    })
    write_ca_bundle(db)
    return RedirectResponse("/admin/ca-certs", status_code=303)


@router.post("/admin/ca-certs/{cert_id}/delete")
async def ca_certs_delete(
    request: Request,
    cert_id: str,
    current_user: dict = Depends(require_role("admin")),
):
    from web.main import get_db
    from harness.ssl_context import write_ca_bundle
    db = get_db()
    db.delete_ca_cert(cert_id)
    write_ca_bundle(db)
    return RedirectResponse("/admin/ca-certs", status_code=303)
