import json
from fastapi import APIRouter, Response
from harness.export import export_pdf, export_docx

router = APIRouter(prefix="/api")

DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, format: str = "pdf"):
    from web.main import get_db

    db = get_db()

    run = db.get_run(run_id)
    if run is None:
        return Response(
            content='{"detail": "Run not found"}',
            status_code=404,
            media_type="application/json",
        )

    if format not in ("pdf", "docx"):
        return Response(
            content='{"detail": "format must be pdf or docx"}',
            status_code=422,
            media_type="application/json",
        )

    results = db.get_results_for_run(run_id)
    screenshots_dir = "data/screenshots"

    try:
        if format == "pdf":
            data = export_pdf(run, results, screenshots_dir=screenshots_dir)
            media_type = "application/pdf"
            ext = "pdf"
        else:
            data = export_docx(run, results, screenshots_dir=screenshots_dir)
            media_type = DOCX_MEDIA_TYPE
            ext = "docx"
    except Exception as e:
        return Response(
            content=json.dumps({"detail": str(e)}),
            status_code=500,
            media_type="application/json",
        )

    filename = f"run-{run_id[:8]}-{run['app']}.{ext}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}

    return Response(content=data, media_type=media_type, headers=headers)
