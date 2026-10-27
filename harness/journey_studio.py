"""Interactive Guided Journey Studio Engine.

Provides live browser streaming (CDP screencast) and interaction capture
for interactive browser test recording within the testharness web UI.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, CDPSession, Page, Playwright

_log = logging.getLogger("harness.journey_studio")


def _get_playwright_launcher():
    from harness.browser import __getattr__ as browser_getattr
    return browser_getattr("async_playwright")


class JourneySession:
    """Represents an active interactive journey recording session."""

    def __init__(self, session_id: str, base_url: str):
        self.session_id = session_id
        self.base_url = base_url.rstrip("/")
        self.current_url = ""
        self.steps: List[Dict[str, Any]] = []
        self.created_at = time.time()
        self.last_active = time.time()
        self.last_frame: Optional[str] = None  # base64 JPEG
        self.is_running = False

        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._cdp: Optional[CDPSession] = None

        self._frame_subscribers: List[asyncio.Queue] = []
        self._steps_subscribers: List[asyncio.Queue] = []
        self._prev_nav_url: Optional[str] = None

    def _normalize_url(self, url: str) -> str:
        if self.base_url and url.startswith(self.base_url):
            rel = url[len(self.base_url):]
            return rel if rel.startswith("/") else "/" + rel
        return url

    def _record_navigation(self, url: str) -> None:
        if not url or url == "about:blank":
            return
        norm = self._normalize_url(url)
        if norm == self._prev_nav_url:
            return
        self._prev_nav_url = norm
        self.current_url = url
        self.steps.append({"navigate": norm})
        self._notify_steps()

    def _notify_steps(self) -> None:
        self.last_active = time.time()
        for q in list(self._steps_subscribers):
            try:
                q.put_nowait(list(self.steps))
            except Exception:
                pass

    def _notify_frame(self, frame_b64: str) -> None:
        self.last_frame = frame_b64
        self.last_active = time.time()
        for q in list(self._frame_subscribers):
            try:
                q.put_nowait(frame_b64)
            except Exception:
                pass

    async def start(self, initial_url: Optional[str] = None, headers: Optional[Dict[str, str]] = None) -> None:
        """Launch Playwright Chromium and initialize CDP screencast."""
        if self.is_running:
            return
        self.is_running = True
        target_url = initial_url or self.base_url or "about:blank"

        ap = _get_playwright_launcher()
        self._pw = await ap().start()
        self._browser = await self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1024, "height": 768},
            ignore_https_errors=True,
        )
        self._page = await self._context.new_page()
        if headers and hasattr(self._page, "set_extra_http_headers"):
            res = self._page.set_extra_http_headers({str(k): str(v) for k, v in headers.items()})
            if inspect.isawaitable(res):
                await res

        # Listen for frame navigations
        def _on_nav(frame):
            if frame == self._page.main_frame:
                self._record_navigation(frame.url)

        self._page.on("framenavigated", _on_nav)

        # Setup CDP screencast
        try:
            self._cdp = await self._context.new_cdp_session(self._page)
            async def _on_screencast_frame(event):
                data = event.get("data", "")
                session_id = event.get("sessionId")
                if data:
                    self._notify_frame(data)
                if session_id and self._cdp:
                    try:
                        await self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                    except Exception:
                        pass

            self._cdp.on("Page.screencastFrame", lambda ev: asyncio.create_task(_on_screencast_frame(ev)))
            await self._cdp.send(
                "Page.startScreencast",
                {"format": "jpeg", "quality": 70, "maxWidth": 1024, "maxHeight": 768},
            )
        except Exception as e:
            _log.warning("CDP screencast unavailable: %s", e)

        # Initial navigation
        if target_url and target_url != "about:blank":
            try:
                await self._page.goto(target_url, timeout=30000)
            except Exception as e:
                _log.warning("Initial navigation error for %s: %s", target_url, e)

    async def navigate(self, target_url: str) -> None:
        """Navigate to a target URL."""
        if not self._page:
            return
        self.last_active = time.time()
        url = target_url if target_url.startswith("http") else f"{self.base_url.rstrip('/')}/{target_url.lstrip('/')}"
        try:
            await self._page.goto(url, timeout=30000)
        except Exception as e:
            _log.warning("Navigate error: %s", e)

    async def click(self, x: int, y: int) -> Dict[str, Any]:
        """Inspect element at (x, y), execute click, and synthesize a click step."""
        if not self._page:
            return {}
        self.last_active = time.time()

        # Generate a selector for element at (x, y)
        info = await self._page.evaluate(
            """([x, y]) => {
                const el = document.elementFromPoint(x, y);
                if (!el) return { selector: 'body', tag: 'body' };
                if (el.id) return { selector: '#' + CSS.escape(el.id), tag: el.tagName.toLowerCase(), id: el.id };
                if (el.name) return { selector: '[name=\"' + CSS.escape(el.name) + '\"]', tag: el.tagName.toLowerCase(), name: el.name };
                if (el.placeholder) return { selector: '[placeholder=\"' + CSS.escape(el.placeholder) + '\"]', tag: el.tagName.toLowerCase() };
                const text = (el.innerText || '').trim().replace(/[\\r\\n\\t]+/g, ' ').slice(0, 30);
                if (el.tagName.toLowerCase() === 'button' && text) {
                    return { selector: 'button:has-text(\"' + text.replace(/\"/g, '\\\\\"') + '\")', tag: 'button', text: text };
                }
                if (el.tagName.toLowerCase() === 'a' && text) {
                    return { selector: 'a:has-text(\"' + text.replace(/\"/g, '\\\\\"') + '\")', tag: 'a', text: text };
                }
                if (el.className && typeof el.className === 'string') {
                    const cls = el.className.trim().split(/\\s+/).filter(c => !c.includes(':') && !c.includes('/'))[0];
                    if (cls) return { selector: '.' + CSS.escape(cls), tag: el.tagName.toLowerCase() };
                }
                return { selector: el.tagName.toLowerCase(), tag: el.tagName.toLowerCase() };
            }""",
            [x, y],
        )

        selector = (info or {}).get("selector", "body")
        tag = (info or {}).get("tag", "")

        # Execute the real click in Playwright
        try:
            await self._page.mouse.click(x, y)
        except Exception as e:
            _log.warning("Click error at (%d, %d): %s", x, y, e)

        step = {"click": selector}
        self.steps.append(step)
        self._notify_steps()
        return {"step": step, "tag": tag, "info": info}

    async def fill(self, field: str, value: str) -> Dict[str, Any]:
        """Fill an input field with text and synthesize a fill step."""
        if not self._page:
            return {}
        self.last_active = time.time()
        try:
            await self._page.fill(field, value, timeout=10000)
        except Exception as e:
            _log.warning("Fill error for %s: %s", field, e)

        step = {"fill": {"field": field, "value": value}}
        self.steps.append(step)
        self._notify_steps()
        return {"step": step}

    async def assert_text(self, text: str) -> Dict[str, Any]:
        """Record an assert_text step."""
        self.last_active = time.time()
        step = {"assert_text": text}
        self.steps.append(step)
        self._notify_steps()
        return {"step": step}

    async def screenshot(self) -> Dict[str, Any]:
        """Record a screenshot step."""
        self.last_active = time.time()
        step = {"screenshot": None}
        self.steps.append(step)
        self._notify_steps()
        return {"step": step}

    async def remove_step(self, index: int) -> List[Dict[str, Any]]:
        """Remove a step at the given index."""
        self.last_active = time.time()
        if 0 <= index < len(self.steps):
            self.steps.pop(index)
            self._notify_steps()
        return list(self.steps)

    def subscribe_frames(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=10)
        self._frame_subscribers.append(q)
        if self.last_frame:
            q.put_nowait(self.last_frame)
        return q

    def unsubscribe_frames(self, q: asyncio.Queue) -> None:
        if q in self._frame_subscribers:
            self._frame_subscribers.remove(q)

    def subscribe_steps(self) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=10)
        self._steps_subscribers.append(q)
        q.put_nowait(list(self.steps))
        return q

    def unsubscribe_steps(self, q: asyncio.Queue) -> None:
        if q in self._steps_subscribers:
            self._steps_subscribers.remove(q)

    async def close(self) -> List[Dict[str, Any]]:
        """Stop screencast, close browser, and return captured steps."""
        if not self.is_running:
            return list(self.steps)
        self.is_running = False

        if self._cdp:
            try:
                await self._cdp.send("Page.stopScreencast")
            except Exception:
                pass
            self._cdp = None

        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

        return list(self.steps)


class JourneyStudioManager:
    """Manages active interactive journey recording sessions."""

    def __init__(self):
        self._sessions: Dict[str, JourneySession] = {}
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        base_url: str,
        initial_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> JourneySession:
        session_id = str(uuid.uuid4())
        session = JourneySession(session_id, base_url)
        await session.start(initial_url=initial_url, headers=headers)
        async with self._lock:
            self._sessions[session_id] = session
        return session

    async def get_session(self, session_id: str) -> Optional[JourneySession]:
        async with self._lock:
            return self._sessions.get(session_id)

    async def close_session(self, session_id: str) -> List[Dict[str, Any]]:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session:
            return await session.close()
        return []

    async def cleanup_stale_sessions(self, max_idle_seconds: int = 900) -> None:
        now = time.time()
        to_close = []
        async with self._lock:
            for sid, s in self._sessions.items():
                if now - s.last_active > max_idle_seconds:
                    to_close.append(sid)
        for sid in to_close:
            await self.close_session(sid)


# Global singleton instance
studio_manager = JourneyStudioManager()
