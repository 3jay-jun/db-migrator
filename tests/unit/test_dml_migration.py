from collections.abc import Iterator
from pathlib import Path
from queue import Queue

from db_migrator.config.models import MigrationConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.dml_migration import migrate_tables
from db_migrator.core.events import EventType, MigrationEvent, QueueEventPublisher
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.models import ReadCursor, RowBatch, TableSchema, WriteResult
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeSourceReader:
    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self.rows_by_table = rows_by_table
        self.calls: list[tuple[str, tuple[str, ...], int, tuple[str, ...]]] = []

    def read_rows(
        self,
        table,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        self.calls.append((table.name, columns, batch_size, order_by))
        rows = self.rows_by_table[table.name]
        offset = cursor.offset if cursor is not None else 0
        batch_number = 0
        while offset < len(rows):
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


class FakeTargetWriter:
    def __init__(self, fail_on_table: str | None = None) -> None:
        self.fail_on_table = fail_on_table
        self.written_batches: list[tuple[str, tuple[dict, ...]]] = []
        self.commit_count = 0

    def write_batch(self, table_schema: TableSchema, rows: tuple[dict, ...]) -> WriteResult:
        if table_schema.ref.name == self.fail_on_table:
            return WriteResult(success=False, rows_written=0, message="forced failure")
        self.written_batches.append((table_schema.ref.name, rows))
        return WriteResult(success=True, rows_written=len(rows), message="written")

    def commit(self) -> None:
        self.commit_count += 1


def test_migrate_tables_streams_batches_writes_checkpoint_and_events(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    event_queue: Queue[MigrationEvent] = Queue()
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    source = FakeSourceReader(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
                {"id": 3, "email": "c@example.com", "profile": {}, "created_at": "2026-01-03"},
            ],
            "orders": [
                {"id": 1, "total_amount": "10.00"},
            ],
        }
    )
    target = FakeTargetWriter()

    result = migrate_tables(
        job_id="job-1",
        tables=snapshot.tables,
        source=source,
        target=target,
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(event_queue),
        migration_config=MigrationConfig(batch_size=2, commit_interval=2),
    )

    assert result.rows_written == 4
    assert [batch[0] for batch in target.written_batches] == ["users", "users", "orders"]
    assert target.commit_count == 3
    checkpoints = checkpoint_store.list_checkpoints("job-1")
    assert len([checkpoint for checkpoint in checkpoints if checkpoint.status == "completed"]) == 3
    assert len([checkpoint for checkpoint in checkpoints if checkpoint.status == "table_completed"]) == 2
    event_types = [event_queue.get().type for _ in range(event_queue.qsize())]
    assert EventType.BATCH_COMMITTED in event_types
    assert EventType.CHECKPOINT_SAVED in event_types
    assert EventType.TABLE_COMPLETED in event_types


def test_migrate_tables_uses_large_row_batch_size_for_json_text_binary_tables(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    assert any(column.common_type.kind is CommonTypeKind.JSON for column in users.columns)
    source = FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"}]})

    migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=10_000, large_row_batch_size=100),
    )

    assert source.calls[0][2] == 100
    assert source.calls[0][3] == ("id",)


def test_migrate_tables_excludes_generated_columns_from_read_columns(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    generated_profile_users = TableSchema(
        ref=users.ref,
        columns=tuple(
            column if column.name != "profile" else type(column)(
                name=column.name,
                source_type=column.source_type,
                common_type=column.common_type,
                nullable=column.nullable,
                default=column.default,
                is_generated=True,
                generation_expression="{}",
                ordinal_position=column.ordinal_position,
                warnings=column.warnings,
            )
            for column in users.columns
        ),
        primary_key=users.primary_key,
        indexes=users.indexes,
        estimated_rows=users.estimated_rows,
    )
    source = FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "created_at": "2026-01-01"}]})

    migrate_tables(
        job_id="job-1",
        tables=(generated_profile_users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=100),
    )

    assert "profile" not in source.calls[0][1]


def test_migrate_tables_records_failed_table_status(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")
    source = FakeSourceReader({"orders": [{"id": 1, "total_amount": "10.00"}]})

    result = migrate_tables(
        job_id="job-1",
        tables=(orders,),
        source=source,
        target=FakeTargetWriter(fail_on_table="orders"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=100),
    )

    assert result.tables[0].status == "failed"
    checkpoints = CheckpointStore(tmp_path / "checkpoint.sqlite").list_checkpoints("job-1")
    assert checkpoints[0].status == "failed"
