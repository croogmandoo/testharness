# URL Recorder Specification (CRO‑49)

## Overview
The **URL Recorder** captures every navigation event that occurs during a Playwright browser test and automatically generates a list of test‑step dictionaries that can be fed back into the test runner.

It enables a *guided‑journey* creation workflow where a user can:
1. Run a manual Playwright session (or a scripted test).
2. Record the sequence of URLs visited.
3. Export those URLs as reusable **navigation steps** for future automated runs.

## Public API
```python
from harness.url_recorder import URLRecorder, record_url_steps

async def record_url_steps(page: Page, base_url: str) -> list[dict]:
    """Capture navigation URLs on `page` and return a list of step dicts.

    Parameters
    ----------
    page: Page
        An active Playwright page.
    base_url: str
        The root URL of the test (e.g., "https://example.com").

    Returns
    -------
    List[dict]
        Each dict represents a step compatible with `run_browser_test`:
        ```
        {"navigate": "/relative/path"}
        ```
        Absolute URLs are converted to a relative path using `base_url`.
    """
    ...
```

### `URLRecorder` Class
```python
class URLRecorder:
    def __init__(self, page: Page, base_url: str):
        """Initialize the recorder.
        
        - Subscribes to ``page.on('framenavigated')`` events.
        - Stores the `base_url` for relative conversion.
        """
        ...

    async def start(self) -> None:
        """Begin listening for navigation events.
        Multiple calls are idempotent.
        """
        ...

    async def stop(self) -> list[dict]:
        """Stop listening and return the recorded steps.
        Returns a list of ``{"navigate": <relative_path>}`` dicts.
        Consecutive duplicate URLs are collapsed to keep the step list concise.
        """
        ...
```

## Integration with `run_browser_test`
`run_browser_test` will receive an optional boolean flag `record_url_steps` (default `False`).
When `True`:
1. Instantiate `URLRecorder(page, base_url)` before the first step.
2. Call `await recorder.start()`.
3. After each **navigation** step (including those performed by the test definition), the recorder automatically logs the URL.
4. Once the test finishes (or on early failure), call `recorded_steps = await recorder.stop()` and **prepend** these steps to the original step list, so the generated test run includes the recorded journey.

### Example Modification
```python
async def run_browser_test(..., record_url_steps: bool = False, ...):
    ...
    for i, step in enumerate(steps):
        if i == 0 and record_url_steps:
            recorder = URLRecorder(page, base_url)
            await recorder.start()
        step_result = await execute_step(page, step, base_url, ...)
        ...
    if record_url_steps:
        recorded = await recorder.stop()
        # Insert recorded navigation steps at the beginning of the log
        step_log = [StepResult(step=f"navigate {s['navigate']}", status="pass", duration_ms=0) for s in recorded] + step_log
```

## Edge Cases & Guarantees
- **Relative conversion**: If a URL does not start with `base_url`, it is stored **as‑is** (full URL) to avoid accidental truncation.
- **Duplicate suppression**: Consecutive identical URLs are merged; non‑consecutive repeats are kept to preserve true navigation flow.
- **Error handling**: If the page crashes or navigation fails, the recorder still returns the steps recorded up to that point.
- **Performance**: Recorder stores URLs in memory only; negligible overhead (< 1 ms per navigation).

## Tests (to be added in Slice 4)
- Verify that `record_url_steps` returns the correct relative paths for a series of mocked navigation events.
- Ensure duplicate consecutive URLs are collapsed.
- Integration test: enable `record_url_steps=True` in `run_browser_test` and confirm that the final `step_log` contains the auto‑generated navigation steps before any user‑defined steps.

---
*Design prepared for implementation of CRO‑49 – Guided Journey Creator URL Recording & Step Generation.*
