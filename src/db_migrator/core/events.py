from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from queue import Queue
from typing import Any, Protocol


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class EventType(StrEnum):
    JOB_STARTED = "job_started"
    CONNECTION_TESTED = "connection_tested"
    SCHEMA_SCANNED = "schema_scanned"
    PLAN_CREATED = "plan_created"
    SAFETY_WARNING = "safety_warning"
    DML_STARTED = "dml_started"
    BATCH_COMMITTED = "batch_committed"
    CHECKPOINT_SAVED = "checkpoint_saved"
    TABLE_COMPLETED = "table_completed"
    TABLE_FAILED = "table_failed"
    JOB_CANCELLED = "job_cancelled"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"


@dataclass(frozen=True)
class ProgressSnapshot:
    completed_units: int
    total_units: int
    current_unit: str | None = None


@dataclass(frozen=True)
class MigrationEvent:
    job_id: str
    level: EventLevel
    type: EventType
    message: str
    table: str | None = None
    progress: ProgressSnapshot | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventPublisher(Protocol):
    def publish(self, event: MigrationEvent) -> None:
        """Publish one migration event to an implementation-specific sink."""


class QueueEventPublisher:
    def __init__(self, event_queue: Queue[MigrationEvent]) -> None:
        self._event_queue = event_queue

    def publish(self, event: MigrationEvent) -> None:
        self._event_queue.put(event)
