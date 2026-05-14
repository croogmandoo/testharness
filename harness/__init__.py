# Central harness version. Workers report their own version on register/
# heartbeat (HARNESS_WORKER_VERSION env var, defaulting to "1.0.0"). The
# dispatch path compares major versions to decide whether to give a worker
# any jobs; minor-version drift is allowed but flagged in the UI. See
# harness/version_skew.py for the comparison logic.
__version__ = "1.0.0"

from harness.models import TestResult, Run, AppState, StepResult
from harness.api import run_api_test
from harness.config import ConfigError


# Lazy re-export of ``harness.browser.run_browser_test``. The browser module
# pulls in Playwright (~1.3 GB of runtime + Chromium), which the slim worker
# image (``worker/Dockerfile.slim``) deliberately omits. Eager re-export here
# would crash ``import harness`` on the slim image — even for workers whose
# capability set is ``api,availability`` only. PEP-562 module-level
# ``__getattr__`` defers the actual import until something references the
# attribute, so:
#
#   from harness import run_browser_test   # still works on full image
#   from harness import TestResult         # works on slim image too
#
# On slim, ``run_browser_test`` is never referenced (the central refuses to
# dispatch browser-typed jobs to api/availability-capable workers), so the
# Playwright import is never triggered.
def __getattr__(name):
    if name == "run_browser_test":
        from harness.browser import run_browser_test as _f
        return _f
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# AppTest base class for Python escape-hatch test files
#
# RESERVED / NOT YET WIRED: this class is the public surface for the
# Python-script test type documented in docs/guides/writing-tests.md §6,
# but the loader/runner does not yet discover or execute subclasses of
# AppTest. The class is kept here so the import path is stable and the
# eventual implementation does not break existing user scripts. See the
# "Not yet implemented" notice in docs/guides/writing-tests.md §6.
import os


class AppTest:
    """Base class for Python-defined app tests. Subclass and add test_* methods.

    NOTE: This class is reserved for a future release. The harness loader
    does not currently discover or execute AppTest subclasses — defining
    one will not run any tests yet. See docs/guides/writing-tests.md §6.
    """
    name: str = ""
    base_url: str = ""
    environments: dict | None = None

    def env(self, key: str) -> str:
        val = os.environ.get(key)
        if val is None:
            raise ConfigError(f"Environment variable '{key}' is not set")
        return val

    def resolve_base_url(self, environment: str) -> str:
        envs = self.environments or {}
        if envs and environment in envs:
            return envs[environment]
        if self.base_url:
            return self.base_url
        raise ConfigError(
            f"No base URL for app '{self.name}' in environment '{environment}'"
        )
