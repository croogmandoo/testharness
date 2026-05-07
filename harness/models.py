from dataclasses import dataclass, field
from typing import Dict, Optional, List
import uuid

def _new_id() -> str:
    return str(uuid.uuid4())

@dataclass
class StepResult:
    step: str
    status: str
    duration_ms: int
    error: Optional[str] = None
    screenshot: Optional[str] = None

@dataclass
class TestResult:
    run_id: str
    app: str
    environment: str
    test_name: str
    id: str = field(default_factory=_new_id)
    status: str = ""
    error_msg: Optional[str] = None
    step_log: List[StepResult] = field(default_factory=list)
    screenshot: Optional[str] = None
    duration_ms: Optional[int] = None
    finished_at: Optional[str] = None
    # Runtime-only: values pulled from the response by an `extract:` clause.
    # NOT persisted by Database.insert_test_result and NOT exposed by exports —
    # consumed only by the runner to chain into subsequent tests.
    captures: Dict[str, str] = field(default_factory=dict)

@dataclass
class Run:
    app: str
    environment: str
    triggered_by: str
    id: str = field(default_factory=_new_id)
    status: str = "pending"
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

@dataclass
class AppState:
    app: str
    environment: str
    test_name: str
    state: str
    since: str
