from harness.models import TestResult, Run, AppState, StepResult
from harness.browser import run_browser_test
from harness.api import run_api_test
from harness.config import ConfigError

# AppTest base class for Python escape-hatch test files
import os
from typing import Optional


class AppTest:
    """Base class for Python-defined app tests. Subclass and add test_* methods."""
    name: str = ""
    base_url: str = ""
    environments: dict = {}

    def env(self, key: str) -> str:
        val = os.environ.get(key)
        if val is None:
            raise ConfigError(f"Environment variable '{key}' is not set")
        return val

    def resolve_base_url(self, environment: str) -> str:
        if self.environments and environment in self.environments:
            return self.environments[environment]
        if self.base_url:
            return self.base_url
        raise ConfigError(
            f"No base URL for app '{self.name}' in environment '{environment}'"
        )
