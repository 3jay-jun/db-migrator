from __future__ import annotations

from dataclasses import dataclass

from db_migrator.config.models import AppConfig
from db_migrator.core.events import EventLevel, EventPublisher, EventType, MigrationEvent


@dataclass(frozen=True)
class EngineResult:
    job_id: str
    status: str


class MigrationEngine:
    def __init__(self, event_publisher: EventPublisher) -> None:
        self._event_publisher = event_publisher

    def run_dry_bootstrap(self, config: AppConfig) -> EngineResult:
        job_id = config.job.name
        self._event_publisher.publish(
            MigrationEvent(
                job_id=job_id,
                level=EventLevel.INFO,
                type=EventType.JOB_STARTED,
                message="Migration bootstrap started.",
            )
        )
        self._event_publisher.publish(
            MigrationEvent(
                job_id=job_id,
                level=EventLevel.INFO,
                type=EventType.JOB_COMPLETED,
                message="Migration bootstrap completed.",
            )
        )
        return EngineResult(job_id=job_id, status="completed")
