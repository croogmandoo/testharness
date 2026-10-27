"""FastAPI routes and WebSocket handlers for Guided Journey Studio."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from harness.journey_studio import studio_manager
from web.auth import require_role

_log = logging.getLogger("web.routes.journey")
router = APIRouter(tags=["journey"])


class StartJourneyRequest(BaseModel):
    url: str
    base_url: Optional[str] = None
    app: Optional[str] = None
    environment: Optional[str] = "production"
    headers: Optional[Dict[str, str]] = None


class JourneyActionRequest(BaseModel):
    action: str
    x: Optional[int] = None
    y: Optional[int] = None
    field: Optional[str] = None
    value: Optional[str] = None
    text: Optional[str] = None
    url: Optional[str] = None
    index: Optional[int] = None


@router.post("/api/journey/start")
async def start_journey(
    req: StartJourneyRequest,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    """Start an interactive Guided Journey Studio session."""
    target_url = req.url.strip()
    if not target_url:
        raise HTTPException(status_code=400, detail="Target URL is required")

    base = (req.base_url or target_url).strip()
    session = await studio_manager.create_session(
        base_url=base,
        initial_url=target_url,
        app=req.app or "",
        environment=req.environment or "production",
        headers=req.headers,
    )
    return {
        "session_id": session.session_id,
        "base_url": session.base_url,
        "current_url": session.current_url or target_url,
        "steps": session.steps,
    }


@router.post("/api/journey/{session_id}/action")
async def perform_journey_action(
    session_id: str,
    req: JourneyActionRequest,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    """Execute an action inside an active journey recording session."""
    session = await studio_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Journey session not found or expired")

    info = {}
    if req.action == "click" and req.x is not None and req.y is not None:
        info = await session.click(req.x, req.y)
    elif req.action == "fill" and req.field is not None and req.value is not None:
        info = await session.fill(req.field, req.value)
    elif req.action == "navigate" and req.url is not None:
        await session.navigate(req.url)
    elif req.action == "assert_text" and req.text is not None:
        info = await session.assert_text(req.text)
    elif req.action == "screenshot":
        info = await session.screenshot()
    elif req.action == "remove_step" and req.index is not None:
        await session.remove_step(req.index)
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action or missing parameters: {req.action}")

    return {
        "status": "ok",
        "steps": session.steps,
        "current_url": session.current_url,
        "info": info,
    }


@router.post("/api/journey/{session_id}/save-state")
async def save_journey_storage_state(
    session_id: str,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    """Save the current browser context session state to data/storage_states/{app}_{env}.json."""
    session = await studio_manager.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        info = await session.save_storage_state()
        return {"ok": True, "info": info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/journey/{session_id}/stop")
async def stop_journey(
    session_id: str,
    current_user: dict = Depends(require_role("admin", "runner")),
):
    """Stop journey recording session and return final captured steps."""
    steps = await studio_manager.close_session(session_id)
    return {"status": "complete", "steps": steps}


@router.websocket("/ws/journey/{session_id}")
async def journey_websocket(websocket: WebSocket, session_id: str):
    """Stream live viewport screencast frames and receive user interactions."""
    session = await studio_manager.get_session(session_id)
    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()

    frame_q = session.subscribe_frames()
    steps_q = session.subscribe_steps()

    async def _send_frames():
        while True:
            frame_data = await frame_q.get()
            try:
                await websocket.send_json({"type": "frame", "data": frame_data})
            except Exception:
                break

    async def _send_steps():
        while True:
            steps_data = await steps_q.get()
            try:
                await websocket.send_json({
                    "type": "steps",
                    "steps": steps_data,
                    "url": session.current_url,
                })
            except Exception:
                break

    async def _receive_client():
        while True:
            msg_text = await websocket.receive_text()
            try:
                msg = json.loads(msg_text)
            except Exception:
                continue

            mtype = msg.get("type", "")
            if mtype == "click":
                x = int(msg.get("x", 0))
                y = int(msg.get("y", 0))
                await session.click(x, y)
            elif mtype in ("keypress", "key"):
                key = msg.get("key", "")
                if key:
                    await session.press_key(key)
            elif mtype == "fill":
                field = msg.get("field", "")
                val = msg.get("value", "")
                if field:
                    await session.fill(field, val)
            elif mtype == "navigate":
                url = msg.get("url", "")
                if url:
                    await session.navigate(url)
            elif mtype == "assert_text":
                text = msg.get("text", "")
                if text:
                    await session.assert_text(text)
            elif mtype == "screenshot":
                await session.screenshot()
            elif mtype == "remove_step":
                idx = int(msg.get("index", -1))
                if idx >= 0:
                    await session.remove_step(idx)
            elif mtype == "ping":
                await websocket.send_json({"type": "pong"})

    send_f_task = asyncio.create_task(_send_frames())
    send_s_task = asyncio.create_task(_send_steps())
    recv_task = asyncio.create_task(_receive_client())

    done, pending = await asyncio.wait(
        [send_f_task, send_s_task, recv_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()

    session.unsubscribe_frames(frame_q)
    session.unsubscribe_steps(steps_q)
