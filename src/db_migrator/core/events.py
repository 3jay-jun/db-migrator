from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from queue import Queue
from threading import Lock
from typing import Any, Callable, Protocol


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
    CHECKPOINT_STALE = "checkpoint_stale"
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


class CompositeEventPublisher:
    def __init__(self, *publishers: EventPublisher) -> None:
        self._publishers = publishers

    def publish(self, event: MigrationEvent) -> None:
        for publisher in self._publishers:
            publisher.publish(event)


class FileEventPublisher:
    def __init__(self, log_dir: Path = Path("logs"), *, clock: Callable[[], datetime] | None = None) -> None:
        self._log_dir = log_dir
        self._clock = clock or datetime.now
        self._lock = Lock()

    def publish(self, event: MigrationEvent) -> None:
        log_file = self._log_file()
        record = _event_to_log_record(event)
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            with self._lock:
                with log_file.open("a", encoding="utf-8") as file:
                    file.write(line + "\n")
        except OSError as exc:
            raise OSError(f"Failed to write migration event log: {log_file}") from exc

    def _log_file(self) -> Path:
        now = self._clock()
        date_label = now.strftime("%Y%m%d")
        return self._log_dir / f"migration-events-{date_label}.log"


def _event_to_log_record(event: MigrationEvent) -> dict[str, Any]:
    record: dict[str, Any] = {
        "occurred_at": event.occurred_at.isoformat(),
        "job_id": event.job_id,
        "level": event.level.value,
        "type": event.type.value,
        "message": event.message,
        "table": event.table,
        "payload": event.payload,
    }
    if event.progress is not None:
        record["progress"] = {
            "completed_units": event.progress.completed_units,
            "total_units": event.progress.total_units,
            "current_unit": event.progress.current_unit,
        }
    return record
