from collections.abc import Iterator
from pathlib import Path
from queue import Queue

from db_migrator.config.models import MigrationConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.dml_migration import build_resume_plan, build_retry_failed_plan, migrate_tables
from db_migrator.core.events import EventType, MigrationEvent, QueueEventPublisher
from db_migrator.schema.models import ReadCursor, RowBatch, TableSchema, WriteResult
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class CursorRecordingSource:
    def __init__(self, rows_by_table: dict[str, list[dict]], *, interrupt_after_batches: int | None = None) -> None:
        self.rows_by_table = rows_by_table
        self.start_offsets: list[tuple[str, int]] = []
        self.interrupt_after_batches = interrupt_after_batches

    def read_rows(
        self,
        table,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        offset = cursor.offset if cursor is not None else 0
        self.start_offsets.append((table.name, offset))
        batch_number = 0
        rows = self.rows_by_table.get(table.name, [])
        while offset < len(rows):
            if self.interrupt_after_batches is not None and batch_number >= self.interrupt_after_batches:
                raise KeyboardInterrupt
            chunk = tuple({column: row.get(column) for column in columns} for row in rows[offset : offset + batch_size])
            batch_number += 1
            offset += len(chunk)
            yield RowBatch(
                table=table,
                rows=chunk,
                batch_number=batch_number,
                start_offset=offset - len(chunk),
                next_cursor=ReadCursor(offset=offset),
            )


class RecordingTarget:
    def __init__(self, *, fail_tables: set[str] | None = None) -> None:
        self.fail_tables = fail_tables or set()
        self.written_tables: list[str] = []

    def write_batch(self, table_schema: TableSchema, rows: tuple[dict, ...]) -> WriteResult:
        if table_schema.ref.name in self.fail_tables:
            return WriteResult(success=False, rows_written=0, message="forced failure")
        self.written_tables.append(table_schema.ref.name)
        return WriteResult(success=True, rows_written=len(rows), message="written")

    def commit(self) -> None:
        return None


def _snapshot():
    return load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))


def test_resume_plan_starts_from_latest_successful_checkpoint(tmp_path) -> None:
    snapshot = _snapshot()
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    source = CursorRecordingSource(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
            ]
        }
    )
    migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=RecordingTarget(),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )

    resume_plan = build_resume_plan("job-1", (users,), checkpoint_store)

    assert resume_plan.selected_tables == ()


def test_resume_plan_restarts_failed_table_from_failed_offset(tmp_path) -> None:
    snapshot = _snapshot()
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    migrate_tables(
        job_id="job-1",
        tables=(orders,),
        source=CursorRecordingSource({"orders": [{"id": 1, "total_amount": "10.00"}]}),
        target=RecordingTarget(fail_tables={"orders"}),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )
    retry_source = CursorRecordingSource({"orders": [{"id": 1, "total_amount": "10.00"}]})

    migrate_tables(
        job_id="job-1",
        tables=(orders,),
        source=retry_source,
        target=RecordingTarget(),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
        resume_plan=build_resume_plan("job-1", (orders,), checkpoint_store),
    )

    assert retry_source.start_offsets == [("orders", 0)]


def test_retry_failed_plan_runs_only_failed_tables(tmp_path) -> None:
    snapshot = _snapshot()
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    migrate_tables(
        job_id="job-1",
        tables=snapshot.tables,
        source=CursorRecordingSource(
            {
                "users": [{"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"}],
                "orders": [{"id": 1, "total_amount": "10.00"}],
            }
        ),
        target=RecordingTarget(fail_tables={"orders"}),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )
    retry_target = RecordingTarget()

    migrate_tables(
        job_id="job-1",
        tables=snapshot.tables,
        source=CursorRecordingSource({"orders": [{"id": 1, "total_amount": "10.00"}]}),
        target=retry_target,
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
        resume_plan=build_retry_failed_plan("job-1", checkpoint_store),
    )

    assert retry_target.written_tables == ["orders"]


def test_keyboard_interrupt_records_cancelled_checkpoint_and_event(tmp_path) -> None:
    snapshot = _snapshot()
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    event_queue: Queue[MigrationEvent] = Queue()

    result = migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=CursorRecordingSource(
            {
                "users": [
                    {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                    {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
                ]
            },
            interrupt_after_batches=1,
        ),
        target=RecordingTarget(),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(event_queue),
        migration_config=MigrationConfig(batch_size=1),
    )

    assert result.tables[0].status == "cancelled"
    assert checkpoint_store.latest_checkpoint_for_table("job-1", users.ref).status == "cancelled"
    assert EventType.JOB_CANCELLED in [event_queue.get().type for _ in range(event_queue.qsize())]
