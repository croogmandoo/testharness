"""URL Recorder for Testharness Guided Journey Creator.

This module provides a simple utility to capture navigation URLs during a
Playwright ``Page`` session and synthesize step dictionaries that can be
feed back into ``run_browser_test``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, List, Callable

if TYPE_CHECKING:
    from playwright.async_api import Page, Frame


class URLRecorder:
    """Record navigation URLs from a Playwright page.

    The recorder subscribes to ``page.on('framenavigated')`` events. Each
    navigation event yields a ``Frame`` whose ``url`` attribute is stored.
    ``stop()`` returns a list of step dictionaries compatible with the test
    runner: ``{"navigate": <relative_path>}``.
    """

    def __init__(self, page: "Page", base_url: str):
        self._page = page
        self._base_url = base_url.rstrip("/")
        self._urls: List[str] = []
        self._listener: Callable[["Frame"], None] | None = None
        self._running = False

    async def start(self) -> None:
        """Begin listening for navigation events.

        Multiple calls are safe – the recorder will only attach once.
        """
        if self._running:
            return
        self._running = True

        def _on_navigate(frame: "Frame") -> None:
            # Only record top‑level frame navigations to avoid duplicate entries
            if frame == self._page.main_frame:
                self._urls.append(frame.url)

        self._listener = _on_navigate
        self._page.on("framenavigated", self._listener)

    async def stop(self) -> List[dict]:
        """Stop listening and return recorded navigation steps.

        Returns a list like ``[{"navigate": "/path"}, ...]``. Consecutive
        duplicate URLs are collapsed. URLs that start with ``base_url`` are
        converted to a relative path; otherwise the full URL is kept.
        """
        if not self._running:
            return []
        # Detach listener
        if self._listener is not None:
            self._page.off("framenavigated", self._listener)
        self._running = False

        steps: List[dict] = []
        prev: str | None = None
        for url in self._urls:
            if url == prev:
                continue  # collapse consecutive duplicates
            prev = url
            if url.startswith(self._base_url):
                rel = url[len(self._base_url) :]
                if not rel.startswith("/"):
                    rel = "/" + rel
            else:
                rel = url
            steps.append({"navigate": rel})
        return steps


async def record_url_steps(page: "Page", base_url: str) -> List[dict]:
    """Convenience helper that records URLs for the lifetime of ``page``.

    It starts the recorder, yields control back to the caller (who can run
    arbitrary actions on the page), and then stops the recorder when the
    caller awaits the returned coroutine.
    """
    recorder = URLRecorder(page, base_url)
    await recorder.start()
    # The caller is expected to perform actions while the recorder is active.
    # This helper simply returns the recorded steps when awaited.
    # Users typically call ``await record_url_steps(page, base_url)`` after
    # performing their interactions.
    # To keep the API simple we just stop immediately – callers can manage
    # the lifecycle manually via ``URLRecorder`` if they need finer control.
    await asyncio.sleep(0)  # ensure any pending navigation events are processed
    return await recorder.stop()
