import json
from datetime import datetime
from queue import Queue

from db_migrator.cli.console_events import format_event
from db_migrator.core.events import (
    CompositeEventPublisher,
    EventLevel,
    EventType,
    FileEventPublisher,
    MigrationEvent,
    ProgressSnapshot,
    QueueEventPublisher,
)


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


def test_composite_event_publisher_forwards_event_to_all_publishers() -> None:
    first_queue: Queue[MigrationEvent] = Queue()
    second_queue: Queue[MigrationEvent] = Queue()
    event = MigrationEvent(
        job_id="job-1",
        level=EventLevel.INFO,
        type=EventType.JOB_STARTED,
        message="started",
    )

    CompositeEventPublisher(QueueEventPublisher(first_queue), QueueEventPublisher(second_queue)).publish(event)

    assert first_queue.get_nowait() == event
    assert second_queue.get_nowait() == event


def test_file_event_publisher_writes_json_lines(tmp_path) -> None:
    event = MigrationEvent(
        job_id="job-1",
        level=EventLevel.WARNING,
        type=EventType.CHECKPOINT_STALE,
        message="Checkpoint stale.",
        table="users",
        progress=ProgressSnapshot(completed_units=1, total_units=2, current_unit="users"),
        payload={"status": "stale_checkpoint_ignored"},
    )

    FileEventPublisher(tmp_path, clock=lambda: datetime(2026, 8, 11)).publish(event)

    log_file = tmp_path / "migration-events-20260811.log"
    record = json.loads(log_file.read_text(encoding="utf-8"))
    assert record["job_id"] == "job-1"
    assert record["level"] == "warning"
    assert record["type"] == "checkpoint_stale"
    assert record["table"] == "users"
    assert record["payload"]["status"] == "stale_checkpoint_ignored"
    assert record["progress"]["completed_units"] == 1


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
