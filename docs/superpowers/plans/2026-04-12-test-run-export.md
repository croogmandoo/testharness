# Test Run Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add PDF and DOCX export for a single test run, with full detail including step logs and embedded screenshots, accessible from the detail page and dashboard.

**Architecture:** A pure-Python `harness/export.py` module builds documents using ReportLab (PDF) and python-docx (DOCX) from run+results dicts. A new `web/routes/export.py` route serves `GET /api/runs/{run_id}/export?format=pdf|docx`. `get_app_summary` gains `last_run_id` so the dashboard can link to exports without extra lookups.

**Tech Stack:** Python 3, FastAPI, ReportLab, python-docx, SQLite, Jinja2

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `requirements.txt` | Modify | Add `reportlab` and `python-docx` |
| `harness/db.py` | Modify | Add `last_run_id` to `get_app_summary` |
| `harness/export.py` | Create | `export_pdf` and `export_docx` functions |
| `web/routes/export.py` | Create | `GET /api/runs/{run_id}/export` endpoint |
| `web/main.py` | Modify | Include export router |
| `web/routes/dashboard.py` | Modify | Add `last_run_id`/`active_run_id` to fallback dict |
| `web/templates/dashboard.html` | Modify | PDF/DOCX export links per app row |
| `web/templates/detail.html` | Modify | Export PDF / Export DOCX buttons in run-meta bar |
| `tests/test_models_and_db.py` | Modify | Tests for `last_run_id` in `get_app_summary` |
| `tests/test_export.py` | Create | Unit tests for `export_pdf`/`export_docx`; route tests |

---

## Task 1: Add `last_run_id` to `get_app_summary`

**Files:**
- Modify: `harness/db.py` (lines 175–183, `get_app_summary`)
- Modify: `tests/test_models_and_db.py`

- [ ] **Step 1: Write failing tests**

Add to the bottom of `tests/test_models_and_db.py`:

```python
def test_get_app_summary_has_last_run_id_when_completed_run_exists(tmp_path):
    from harness.db import Database
    from harness.models import Run, AppState
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at="2026-01-01T00:00:00",
                         finished_at="2026-01-01T00:01:00")
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["last_run_id"] == run.id


def test_get_app_summary_last_run_id_is_none_when_no_completed_run(tmp_path):
    from harness.db import Database
    from harness.models import AppState
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    db.upsert_app_state(AppState(app="myapp", environment="prod",
                                 test_name="t", state="passing",
                                 since="2026-01-01T00:00:00"))
    summary = db.get_app_summary("prod")
    row = next(r for r in summary if r["app"] == "myapp")
    assert row["last_run_id"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_models_and_db.py::test_get_app_summary_has_last_run_id_when_completed_run_exists tests/test_models_and_db.py::test_get_app_summary_last_run_id_is_none_when_no_completed_run -v
```

Expected: FAIL — `KeyError: 'last_run_id'`

- [ ] **Step 3: Update `get_app_summary` in `harness/db.py`**

Find the `last_runs` block (lines ~175–183). Replace:

```python
        with self._connect() as conn:
            last_runs = conn.execute(
                "SELECT app, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
        for row in last_runs:
            if row["app"] in apps:
                apps[row["app"]]["last_run"] = row["last_run"]
```

With:

```python
        with self._connect() as conn:
            last_runs = conn.execute(
                "SELECT app, id, MAX(finished_at) as last_run FROM runs "
                "WHERE environment=? AND status='complete' GROUP BY app",
                (environment,)
            ).fetchall()
        for row in last_runs:
            if row["app"] in apps:
                apps[row["app"]]["last_run"] = row["last_run"]
                apps[row["app"]]["last_run_id"] = row["id"]
        # Ensure last_run_id is always present (None for apps with no completed run)
        for app_dict in apps.values():
            app_dict.setdefault("last_run_id", None)
```

- [ ] **Step 4: Run tests to confirm they pass**

```
python -m pytest tests/test_models_and_db.py -v
```

Expected: 7 passing.

- [ ] **Step 5: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 6: Commit**

```bash
git add harness/db.py tests/test_models_and_db.py
git commit -m "feat: add last_run_id to get_app_summary"
```

---

## Task 2: Install dependencies and create `harness/export.py`

**Files:**
- Modify: `requirements.txt`
- Create: `harness/export.py`
- Create: `tests/test_export.py` (unit tests only — route tests in Task 3)

- [ ] **Step 1: Install and pin the new dependencies**

```
pip install reportlab==4.2.5 python-docx==1.1.2
```

Then add to `requirements.txt` (append after the last line):

```
reportlab==4.2.5
python-docx==1.1.2
```

- [ ] **Step 2: Write failing unit tests**

Create `tests/test_export.py`:

```python
import base64
import json
import os
import pytest
from harness.export import export_pdf, export_docx

# Minimal valid 1x1 PNG (for screenshot embedding tests)
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ"
    "AABjkB6QAAAABJRU5ErkJggg=="
)

SAMPLE_RUN = {
    "id": "abcd1234-0000-0000-0000-000000000000",
    "app": "myapp",
    "environment": "prod",
    "status": "complete",
    "started_at": "2026-01-01T12:00:00",
    "finished_at": "2026-01-01T12:01:00",
    "triggered_by": "test",
}

SAMPLE_RESULTS_NO_SCREENSHOTS = [
    {
        "test_name": "health",
        "status": "pass",
        "duration_ms": 120,
        "error_msg": None,
        "screenshot": None,
        "step_log": json.dumps([
            {"step": "navigate /", "status": "pass", "duration_ms": 80,
             "error": None, "screenshot": None},
        ]),
    }
]

SAMPLE_RESULTS_WITH_ERROR = [
    {
        "test_name": "login",
        "status": "fail",
        "duration_ms": 3000,
        "error_msg": "Element not found: #submit",
        "screenshot": None,
        "step_log": json.dumps([
            {"step": "navigate /login", "status": "pass", "duration_ms": 200,
             "error": None, "screenshot": None},
            {"step": "click #submit", "status": "fail", "duration_ms": 2800,
             "error": "Element not found: #submit", "screenshot": None},
        ]),
    }
]


def test_export_pdf_returns_pdf_bytes():
    result = export_pdf(SAMPLE_RUN, SAMPLE_RESULTS_NO_SCREENSHOTS)
    assert len(result) > 0
    assert result[:4] == b"%PDF"


def test_export_docx_returns_docx_bytes():
    result = export_docx(SAMPLE_RUN, SAMPLE_RESULTS_NO_SCREENSHOTS)
    assert len(result) > 0
    assert result[:2] == b"PK"


def test_export_pdf_with_error_result():
    result = export_pdf(SAMPLE_RUN, SAMPLE_RESULTS_WITH_ERROR)
    assert result[:4] == b"%PDF"


def test_export_docx_with_error_result():
    result = export_docx(SAMPLE_RUN, SAMPLE_RESULTS_WITH_ERROR)
    assert result[:2] == b"PK"


def test_export_pdf_skips_missing_failure_screenshot(tmp_path):
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    # File does NOT exist on disk — should not raise
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_docx_skips_missing_failure_screenshot(tmp_path):
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_docx(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:2] == b"PK"


def test_export_pdf_embeds_existing_failure_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test.png").write_bytes(PNG_1X1)
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_docx_embeds_existing_failure_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test.png").write_bytes(PNG_1X1)
    results = [{**SAMPLE_RESULTS_NO_SCREENSHOTS[0],
                "screenshot": "myapp/prod/run1/test.png"}]
    result = export_docx(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:2] == b"PK"


def test_export_pdf_embeds_step_screenshot(tmp_path):
    shot_path = tmp_path / "myapp" / "prod" / "run1"
    shot_path.mkdir(parents=True)
    (shot_path / "test-step-0.png").write_bytes(PNG_1X1)
    results = [{
        **SAMPLE_RESULTS_NO_SCREENSHOTS[0],
        "step_log": json.dumps([
            {"step": "screenshot", "status": "pass", "duration_ms": 50,
             "error": None, "screenshot": "myapp/prod/run1/test-step-0.png"},
        ]),
    }]
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"


def test_export_pdf_skips_missing_step_screenshot(tmp_path):
    results = [{
        **SAMPLE_RESULTS_NO_SCREENSHOTS[0],
        "step_log": json.dumps([
            {"step": "screenshot", "status": "pass", "duration_ms": 50,
             "error": None, "screenshot": "myapp/prod/run1/missing-step-0.png"},
        ]),
    }]
    # File missing — should not raise
    result = export_pdf(SAMPLE_RUN, results, screenshots_dir=str(tmp_path))
    assert result[:4] == b"%PDF"
```

- [ ] **Step 3: Run tests to confirm they fail**

```
python -m pytest tests/test_export.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'harness.export'`

- [ ] **Step 4: Create `harness/export.py`**

```python
import io
import json
import os
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image as RLImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from docx import Document
from docx.shared import Inches, RGBColor


def _load_steps(step_log_raw) -> list:
    """Parse step_log JSON string (or list) into a list of step dicts."""
    if not step_log_raw:
        return []
    if isinstance(step_log_raw, list):
        return step_log_raw
    return json.loads(step_log_raw)


def _rl_image(path: str, max_width_cm: float) -> RLImage | None:
    """Return a scaled ReportLab Image, or None if the file doesn't exist."""
    if not os.path.isfile(path):
        return None
    img = RLImage(path)
    max_w = max_width_cm * cm
    if img.drawWidth > max_w:
        scale = max_w / img.drawWidth
        img.drawWidth = max_w
        img.drawHeight = img.drawHeight * scale
    return img


def export_pdf(run: dict, results: list,
               screenshots_dir: str = "data/screenshots") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = []

    # ── Header ──────────────────────────────────────────────────────────────
    story.append(Paragraph(f"Test Run Report — {run['app']}", styles["Title"]))
    story.append(Spacer(1, 0.3 * cm))
    for line in [
        f"Environment: {run['environment']}",
        f"Run ID: {run['id'][:8]}",
        f"Started: {run.get('started_at') or '—'}",
        f"Status: {run['status'].upper()}",
    ]:
        story.append(Paragraph(line, styles["Normal"]))
    story.append(Spacer(1, 0.6 * cm))

    # ── Per-test sections ───────────────────────────────────────────────────
    for result in results:
        steps = _load_steps(result.get("step_log"))
        test_type = "browser" if steps else "availability/api"

        story.append(Paragraph(f"Test: {result['test_name']}", styles["Heading2"]))
        story.append(Paragraph(
            f"Type: {test_type} &nbsp;|&nbsp; "
            f"Status: {result['status']} &nbsp;|&nbsp; "
            f"Duration: {result.get('duration_ms', 0)}ms",
            styles["Normal"],
        ))

        if result.get("error_msg"):
            story.append(Paragraph(f"Error: {result['error_msg']}", styles["Normal"]))

        if steps:
            table_data = [["Step", "Status", "Duration", "Error"]]
            for s in steps:
                table_data.append([
                    s.get("step", ""),
                    s.get("status", ""),
                    f"{s.get('duration_ms', 0)}ms",
                    s.get("error") or "",
                ])
            tbl = Table(table_data, colWidths=[7 * cm, 2 * cm, 2.5 * cm, 5 * cm])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#374151")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1),
                 [colors.white, colors.HexColor("#f9fafb")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(Spacer(1, 0.2 * cm))
            story.append(tbl)
            story.append(Spacer(1, 0.3 * cm))

        # Failure screenshot
        if result.get("screenshot"):
            img = _rl_image(
                os.path.join(screenshots_dir, result["screenshot"]), 14
            )
            if img:
                story.append(Paragraph("Screenshot at failure:", styles["Normal"]))
                story.append(img)
                story.append(Spacer(1, 0.2 * cm))

        # Step screenshots
        for s in steps:
            if s.get("screenshot"):
                img = _rl_image(
                    os.path.join(screenshots_dir, s["screenshot"]), 12
                )
                if img:
                    story.append(
                        Paragraph(f"Step screenshot: {s.get('step', '')}", styles["Normal"])
                    )
                    story.append(img)
                    story.append(Spacer(1, 0.2 * cm))

        story.append(Spacer(1, 0.5 * cm))

    # ── Footer ───────────────────────────────────────────────────────────────
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        f"Generated by Web Testing Harness on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        styles["Normal"],
    ))

    doc.build(story)
    return buf.getvalue()


def export_docx(run: dict, results: list,
                screenshots_dir: str = "data/screenshots") -> bytes:
    doc = Document()

    # ── Header ──────────────────────────────────────────────────────────────
    doc.add_heading(f"Test Run Report — {run['app']}", 0)
    meta = doc.add_paragraph()
    meta.add_run(f"Environment: {run['environment']}\n")
    meta.add_run(f"Run ID: {run['id'][:8]}\n")
    meta.add_run(f"Started: {run.get('started_at') or '—'}\n")
    meta.add_run(f"Status: {run['status'].upper()}")
    doc.add_paragraph()

    # ── Per-test sections ───────────────────────────────────────────────────
    for result in results:
        steps = _load_steps(result.get("step_log"))
        test_type = "browser" if steps else "availability/api"

        doc.add_heading(f"Test: {result['test_name']}", level=2)
        doc.add_paragraph(
            f"Type: {test_type} | Status: {result['status']} "
            f"| Duration: {result.get('duration_ms', 0)}ms"
        )

        if result.get("error_msg"):
            err_p = doc.add_paragraph()
            err_run = err_p.add_run(f"Error: {result['error_msg']}")
            err_run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)

        if steps:
            tbl = doc.add_table(rows=1, cols=4)
            tbl.style = "Table Grid"
            hdr = tbl.rows[0].cells
            for i, heading in enumerate(["Step", "Status", "Duration", "Error"]):
                hdr[i].text = heading
            for s in steps:
                row = tbl.add_row().cells
                row[0].text = s.get("step", "")
                row[1].text = s.get("status", "")
                row[2].text = f"{s.get('duration_ms', 0)}ms"
                row[3].text = s.get("error") or ""
            doc.add_paragraph()

        # Failure screenshot
        if result.get("screenshot"):
            path = os.path.join(screenshots_dir, result["screenshot"])
            if os.path.isfile(path):
                doc.add_paragraph("Screenshot at failure:")
                doc.add_picture(path, width=Inches(5))
                doc.add_paragraph()

        # Step screenshots
        for s in steps:
            if s.get("screenshot"):
                path = os.path.join(screenshots_dir, s["screenshot"])
                if os.path.isfile(path):
                    doc.add_paragraph(f"Step screenshot: {s.get('step', '')}")
                    doc.add_picture(path, width=Inches(5))
                    doc.add_paragraph()

    # ── Footer ───────────────────────────────────────────────────────────────
    doc.add_paragraph()
    doc.add_paragraph(
        f"Generated by Web Testing Harness on "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
```

- [ ] **Step 5: Run tests to confirm they pass**

```
python -m pytest tests/test_export.py -v
```

Expected: 10 passing.

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt harness/export.py tests/test_export.py
git commit -m "feat: add export_pdf and export_docx to harness/export.py"
```

---

## Task 3: Create `web/routes/export.py` and route tests

**Files:**
- Create: `web/routes/export.py`
- Modify: `tests/test_export.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_export.py`:

```python
# ── Route tests ──────────────────────────────────────────────────────────────

import pytest
from fastapi.testclient import TestClient
from web.main import create_app
from harness.db import Database
from harness.models import Run


@pytest.fixture
def export_client(tmp_path):
    db = Database(str(tmp_path / "h.db"))
    db.init_schema()
    run = Run(app="myapp", environment="prod", triggered_by="test")
    db.insert_run(run)
    db.update_run_status(run.id, "complete",
                         started_at="2026-01-01T00:00:00",
                         finished_at="2026-01-01T00:01:00")
    config = {"default_environment": "prod", "environments": {"prod": {"label": "Prod"}}}
    app = create_app(db=db, config=config, apps_dir=str(tmp_path / "apps"))
    return TestClient(app), run.id


def test_export_route_pdf_returns_200(export_client):
    client, run_id = export_client
    resp = client.get(f"/api/runs/{run_id}/export?format=pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_export_route_docx_returns_200(export_client):
    client, run_id = export_client
    resp = client.get(f"/api/runs/{run_id}/export?format=docx")
    assert resp.status_code == 200
    assert "wordprocessingml" in resp.headers["content-type"]
    assert resp.content[:2] == b"PK"


def test_export_route_unknown_run_returns_404(export_client):
    client, _ = export_client
    resp = client.get("/api/runs/nonexistent-id/export?format=pdf")
    assert resp.status_code == 404


def test_export_route_bad_format_returns_422(export_client):
    client, run_id = export_client
    resp = client.get(f"/api/runs/{run_id}/export?format=xml")
    assert resp.status_code == 422


def test_export_route_content_disposition_contains_app_name(export_client):
    client, run_id = export_client
    resp = client.get(f"/api/runs/{run_id}/export?format=pdf")
    assert resp.status_code == 200
    assert "myapp" in resp.headers.get("content-disposition", "")
```

- [ ] **Step 2: Run tests to confirm they fail**

```
python -m pytest tests/test_export.py::test_export_route_pdf_returns_200 tests/test_export.py::test_export_route_docx_returns_200 tests/test_export.py::test_export_route_unknown_run_returns_404 tests/test_export.py::test_export_route_bad_format_returns_422 tests/test_export.py::test_export_route_content_disposition_contains_app_name -v
```

Expected: FAIL — 404 (route not registered yet).

- [ ] **Step 3: Create `web/routes/export.py`**

```python
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api")


@router.get("/runs/{run_id}/export")
async def export_run(run_id: str, format: str = "pdf"):
    from web.main import get_db
    db = get_db()

    if format not in ("pdf", "docx"):
        raise HTTPException(status_code=422, detail="format must be 'pdf' or 'docx'")

    run = db.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    results = db.get_results_for_run(run_id)

    from harness.export import export_pdf, export_docx
    try:
        if format == "pdf":
            content = export_pdf(run, results)
            media_type = "application/pdf"
            ext = "pdf"
        else:
            content = export_docx(run, results)
            media_type = (
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            )
            ext = "docx"
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Export failed: {exc}")

    filename = f"run-{run_id[:8]}-{run['app']}.{ext}"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

- [ ] **Step 4: Wire the router into `web/main.py`**

In `web/main.py`, after the existing router includes (around line 56), add:

```python
    from web.routes.export import router as export_router
    app.include_router(export_router)
```

The full `create_app` router section should look like:

```python
    from web.routes.api import router as api_router
    app.include_router(api_router)

    from web.routes.dashboard import router as dashboard_router
    app.include_router(dashboard_router)

    from web.routes.apps import router as apps_router
    app.include_router(apps_router)

    from web.routes.export import router as export_router
    app.include_router(export_router)
```

- [ ] **Step 5: Run route tests to confirm they pass**

```
python -m pytest tests/test_export.py -v
```

Expected: 15 passing (10 unit + 5 route).

- [ ] **Step 6: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add web/routes/export.py web/main.py tests/test_export.py
git commit -m "feat: add GET /api/runs/{run_id}/export route for PDF and DOCX"
```

---

## Task 4: Update templates and dashboard fallback dict

**Files:**
- Modify: `web/routes/dashboard.py` (lines 23–32, fallback dict)
- Modify: `web/templates/dashboard.html` (action column, line 37–39)
- Modify: `web/templates/detail.html` (run-meta bar, line 26–31)

- [ ] **Step 1: Update the dashboard fallback dict in `web/routes/dashboard.py`**

Find the fallback block (lines 23–32) that appends apps with no state:

```python
    for app_def in get_apps():
        if app_def["app"] not in known:
            summary.append({
                "app": app_def["app"],
                "total": 0,
                "passing": 0,
                "failing": 0,
                "unknown": 0,
                "last_run": None,
            })
```

Replace with:

```python
    for app_def in get_apps():
        if app_def["app"] not in known:
            summary.append({
                "app": app_def["app"],
                "total": 0,
                "passing": 0,
                "failing": 0,
                "unknown": 0,
                "last_run": None,
                "last_run_id": None,
                "active_run_id": None,
            })
```

- [ ] **Step 2: Add export links to `dashboard.html`**

Find the action `<td>` in the `{% for row in summary %}` loop (lines 37–39):

```html
      <td>
        <button class="btn btn-sm" onclick="triggerRun('{{ row.app }}')">Run</button>
      </td>
```

Replace with:

```html
      <td style="display:flex; gap:.35rem; flex-wrap:wrap;">
        <button class="btn btn-sm" onclick="triggerRun('{{ row.app }}')">Run</button>
        {% if row.last_run_id %}
        <a href="/api/runs/{{ row.last_run_id }}/export?format=pdf" class="btn btn-sm">PDF</a>
        <a href="/api/runs/{{ row.last_run_id }}/export?format=docx" class="btn btn-sm">DOCX</a>
        {% endif %}
      </td>
```

- [ ] **Step 3: Add export buttons to `detail.html`**

Find the run-meta bar (lines 26–31):

```html
<div class="run-meta">
  <span>Run: {{ selected_run.id[:8] }}</span>
  <span>Status: <strong>{{ selected_run.status }}</strong></span>
  <span>Started: {{ selected_run.started_at or "—" }}</span>
  <button class="btn btn-sm" onclick="triggerRun()">Re-run All</button>
</div>
```

Replace with:

```html
<div class="run-meta">
  <span>Run: {{ selected_run.id[:8] }}</span>
  <span>Status: <strong>{{ selected_run.status }}</strong></span>
  <span>Started: {{ selected_run.started_at or "—" }}</span>
  <button class="btn btn-sm" onclick="triggerRun()">Re-run All</button>
  <a href="/api/runs/{{ selected_run.id }}/export?format=pdf" class="btn btn-sm">Export PDF</a>
  <a href="/api/runs/{{ selected_run.id }}/export?format=docx" class="btn btn-sm">Export DOCX</a>
</div>
```

- [ ] **Step 4: Run full suite**

```
python -m pytest tests/ -q
```

Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add web/routes/dashboard.py web/templates/dashboard.html web/templates/detail.html
git commit -m "feat: add export PDF/DOCX buttons to detail page and dashboard"
```

---

## Self-Review

**Spec coverage:**
- `harness/export.py` with `export_pdf` / `export_docx` → Task 2 ✓
- `web/routes/export.py` with `GET /api/runs/{run_id}/export?format=` → Task 3 ✓
- `web/main.py` router registration → Task 3 Step 4 ✓
- `get_app_summary` gains `last_run_id` → Task 1 ✓
- Detail page export buttons → Task 4 Step 3 ✓
- Dashboard export links (only when `last_run_id` non-null) → Task 4 Step 2 ✓
- 404 for unknown run → `web/routes/export.py` line 12–13 ✓
- 422 for bad format → `web/routes/export.py` line 9–10 ✓
- Missing screenshots skipped silently → `_rl_image` returns None; `os.path.isfile` guard in docx ✓
- Footer "Generated by..." → both functions ✓
- `requirements.txt` updated → Task 2 Step 1 ✓
- Dashboard fallback dict includes `last_run_id` and `active_run_id` → Task 4 Step 1 ✓

**Placeholder scan:** No TBD, TODO, or vague steps. All code is complete.

**Type consistency:** `export_pdf(run, results, screenshots_dir)` and `export_docx(run, results, screenshots_dir)` signatures are identical across Task 2 (implementation) and Task 3 (imports). `_load_steps` and `_rl_image` helpers are defined in Task 2 and used only within `harness/export.py`. ✓
