from queue import Queue

from db_migrator.core.events import EventLevel, EventType, MigrationEvent, QueueEventPublisher


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
