from queue import Queue

from db_migrator.cli.console_events import format_event
from db_migrator.core.events import EventLevel, EventType, MigrationEvent, ProgressSnapshot, QueueEventPublisher


def test_queue_event_publisher_publishes_event() -> None:
    event_queue: Queue[MigrationEvent] = Queue()
    event = MigrationEvent(
        job_id="job-1",
        level=EventLevel.INFO,
        type=EventType.JOB_STARTED,
        message="started",
    )

    QueueEventPublisher(event_queue).publish(event)

    assert event_queue.get_nowait() == event


def test_console_event_progress_never_exceeds_one_hundred_percent() -> None:
    event = MigrationEvent(
        job_id="job-1",
        level=EventLevel.INFO,
        type=EventType.BATCH_COMMITTED,
        message="Batch committed.",
        table="users",
        progress=ProgressSnapshot(completed_units=73, total_units=55, current_unit="users"),
        payload={"eta_seconds": 0},
    )

    rendered = format_event(event)

    assert "progress=73/55 (100.0%)" in rendered
    assert "132.7%" not in rendered
