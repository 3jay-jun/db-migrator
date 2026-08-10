from queue import Queue

from db_migrator.config.models import AppConfig, JobConfig
from db_migrator.core.engine import MigrationEngine
from db_migrator.core.events import EventType, MigrationEvent, QueueEventPublisher


def test_engine_bootstrap_publishes_start_and_completion_events() -> None:
    event_queue: Queue[MigrationEvent] = Queue()
    engine = MigrationEngine(QueueEventPublisher(event_queue))

    result = engine.run_dry_bootstrap(AppConfig(job=JobConfig(name="phase1")))

    assert result.job_id == "phase1"
    assert result.status == "completed"
    assert event_queue.get_nowait().type is EventType.JOB_STARTED
    assert event_queue.get_nowait().type is EventType.JOB_COMPLETED
