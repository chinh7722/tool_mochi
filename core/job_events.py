"""Structured events for logging and progress reporting between workers and UI."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class JobLogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
    SUCCESS = "SUCCESS"


@dataclass
class JobLogEvent:
    """Pickle-compatible structured log sent from a job runner to the GUI."""
    job_id: str
    message: str
    level: JobLogLevel = JobLogLevel.INFO
    account: Optional[str] = None
    stage: str = ""
    emphasis: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    task_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.job_id:
            raise ValueError("job_id cannot be empty")
        if not self.message:
            raise ValueError("message cannot be empty")
        if isinstance(self.level, str):
            try:
                self.level = JobLogLevel(self.level.upper())
            except ValueError:
                self.level = JobLogLevel.INFO


@dataclass
class JobProgressEvent:
    """Pickle-compatible progress snapshot emitted by a worker process/thread."""
    job_id: str
    target_points: int
    attempted_count: int
    success_count: int
    failure_count: int
    status: str
    error_message: Optional[str] = None
    achieved_points_value: Optional[int] = None

    @property
    def achieved_points(self) -> int:
        if self.achieved_points_value is not None:
            return self.achieved_points_value
        return self.success_count * 100
