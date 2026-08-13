from collections.abc import Iterator
from pathlib import Path
from queue import Queue

from db_migrator.config.models import ExistingTablePolicy, MigrationConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.dml_migration import migrate_tables
from db_migrator.core.events import EventType, MigrationEvent, QueueEventPublisher
from db_migrator.schema.column_plan import build_column_plan
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.models import CursorStrategy, ReadCursor, RowBatch, TableRef, TableSchema, WriteResult
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeSourceReader:
    def __init__(self, rows_by_table: dict[str, list[dict]]) -> None:
        self.rows_by_table = rows_by_table
        self.calls: list[tuple[str, tuple[str, ...], int, tuple[str, ...]]] = []
        self.start_cursors: list[ReadCursor] = []

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
        self.start_cursors.append(cursor or ReadCursor.offset_cursor())
        offset = cursor.offset if cursor is not None else 0
        if cursor is not None and cursor.strategy is CursorStrategy.KEYSET and cursor.last_key_values:
            key_column = cursor.key_columns[0]
            offset = next(
                (index for index, row in enumerate(rows) if row[key_column] > cursor.last_key_values[0]),
                len(rows),
            )
        batch_number = 0
        start_cursor = cursor or ReadCursor.offset_cursor()
        while offset < len(rows):
            chunk = tuple({column: row.get(column) for column in columns} for row in rows[offset : offset + batch_size])
            batch_number += 1
            offset += len(chunk)
            next_cursor = (
                ReadCursor.keyset_cursor(
                    key_columns=start_cursor.key_columns,
                    last_key_values=(chunk[-1][start_cursor.key_columns[0]],),
                    offset=offset,
                )
                if start_cursor.strategy is CursorStrategy.KEYSET
                else ReadCursor.offset_cursor(offset)
            )
            yield RowBatch(
                table=table,
                rows=chunk,
                batch_number=batch_number,
                start_offset=offset - len(chunk),
                next_cursor=next_cursor,
                start_cursor=start_cursor,
            )
            start_cursor = next_cursor


class FakeTargetWriter:
    def __init__(
        self,
        fail_on_table: str | None = None,
        *,
        raise_on_table: str | None = None,
        existing_rows: dict[str, int] | None = None,
    ) -> None:
        self.fail_on_table = fail_on_table
        self.raise_on_table = raise_on_table
        self.existing_rows = existing_rows or {}
        self.written_batches: list[tuple[str, tuple[dict, ...]]] = []
        self.upserted_batches: list[tuple[str, tuple[str, ...], tuple[dict, ...]]] = []
        self.sync_keys: list[tuple[str, tuple]] = []
        self.deleted_sync_tables: list[str] = []
        self.commit_count = 0

    def write_batch(self, table_schema: TableSchema, rows: tuple[dict, ...]) -> WriteResult:
        if table_schema.ref.name == self.raise_on_table:
            raise RuntimeError("forced exception")
        if table_schema.ref.name == self.fail_on_table:
            return WriteResult(success=False, rows_written=0, message="forced failure")
        self.written_batches.append((table_schema.ref.name, rows))
        return WriteResult(success=True, rows_written=len(rows), message="written")

    def commit(self) -> None:
        self.commit_count += 1

    def count_rows(self, table: TableRef) -> int:
        return self.existing_rows.get(table.name, 0)

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[dict, ...], keys: tuple[str, ...]) -> WriteResult:
        self.upserted_batches.append((table_schema.ref.name, keys, rows))
        return WriteResult(success=True, rows_written=len(rows), message="upserted")

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        self.sync_keys.append((table_schema.ref.name, ("begin", keys)))

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[dict, ...], keys: tuple[str, ...]) -> None:
        self.sync_keys.extend((table_schema.ref.name, tuple(row.get(key) for key in keys)) for row in rows)

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        self.deleted_sync_tables.append(table_schema.ref.name)
        return 1

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        self.sync_keys.append((table_schema.ref.name, ("end",)))


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
    users_first_checkpoint = next(
        checkpoint
        for checkpoint in checkpoints
        if checkpoint.table.name == "users" and checkpoint.status == "completed" and checkpoint.batch_number == 1
    )
    assert users_first_checkpoint.cursor_strategy == CursorStrategy.KEYSET.value
    assert users_first_checkpoint.last_key_values == (2,)
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
    assert source.start_cursors[0].strategy is CursorStrategy.KEYSET
    assert source.start_cursors[0].key_columns == ("id",)


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


def test_migrate_tables_uses_column_plan_read_columns(tmp_path) -> None:
    from db_migrator.config.models import AppConfig, SourceOnlyColumnAction, TableRunConfig

    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    target_users = TableSchema(
        ref=TableRef(schema="public", name="app_users"),
        columns=tuple(column for column in users.columns if column.name in {"id", "email"}),
        primary_key=users.primary_key,
        indexes=users.indexes,
        estimated_rows=users.estimated_rows,
    )
    column_plan = build_column_plan(
        config=AppConfig(
            tables={
                "public.users": TableRunConfig(
                    source_only_columns={
                        "profile": SourceOnlyColumnAction.IGNORE,
                        "created_at": SourceOnlyColumnAction.IGNORE,
                    }
                )
            }
        ),
        source_table=users,
        target_table=target_users,
    )
    source = FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "profile": {"ignored": True}, "created_at": "2026-01-01"}]})

    migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=100),
        column_plans={users.ref: column_plan},
    )

    assert source.calls[0][1] == ("id", "email")


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


def test_migrate_tables_progress_total_uses_actual_rows_when_estimate_is_low(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    underestimated_users = TableSchema(
        ref=users.ref,
        columns=users.columns,
        primary_key=users.primary_key,
        indexes=users.indexes,
        foreign_keys=users.foreign_keys,
        estimated_rows=1,
    )
    event_queue: Queue[MigrationEvent] = Queue()
    source = FakeSourceReader(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
            ]
        }
    )

    migrate_tables(
        job_id="job-1",
        tables=(underestimated_users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(event_queue),
        migration_config=MigrationConfig(batch_size=2, commit_interval=2),
    )

    events = [event_queue.get() for _ in range(event_queue.qsize())]
    progress_events = [event for event in events if event.type is EventType.BATCH_COMMITTED]

    assert progress_events[0].progress is not None
    assert progress_events[0].progress.completed_units == 2
    assert progress_events[0].progress.total_units == 2


def test_migrate_tables_records_failure_when_writer_raises(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")
    source = FakeSourceReader({"orders": [{"id": 1, "total_amount": "10.00"}]})

    result = migrate_tables(
        job_id="job-1",
        tables=(orders,),
        source=source,
        target=FakeTargetWriter(raise_on_table="orders"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=100),
    )

    checkpoints = CheckpointStore(tmp_path / "checkpoint.sqlite").list_checkpoints("job-1")
    assert result.tables[0].status == "failed"
    assert checkpoints[0].status == "failed"
    assert checkpoints[0].cursor_strategy == CursorStrategy.KEYSET.value


def test_migrate_tables_skips_completed_table_for_same_job(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    source = FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"}]})

    migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )
    second_target = FakeTargetWriter(existing_rows={"users": 1})
    result = migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=second_target,
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )

    assert result.tables[0].status == "skipped"
    assert second_target.written_batches == []


def test_migrate_tables_reruns_completed_checkpoint_when_target_is_empty(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    checkpoint_store = CheckpointStore(tmp_path / "checkpoint.sqlite")
    source = FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"}]})
    event_queue: Queue[MigrationEvent] = Queue()

    migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=FakeTargetWriter(),
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1),
    )
    second_target = FakeTargetWriter(existing_rows={"users": 0})
    result = migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=source,
        target=second_target,
        checkpoint_store=checkpoint_store,
        event_publisher=QueueEventPublisher(event_queue),
        migration_config=MigrationConfig(batch_size=1),
    )

    assert result.tables[0].status == "completed"
    assert second_target.written_batches
    events = [event_queue.get() for _ in range(event_queue.qsize())]
    stale_events = [event for event in events if event.type is EventType.CHECKPOINT_STALE]
    assert len(stale_events) == 1
    assert stale_events[0].message.startswith("Checkpoint stale: users.")


def test_append_policy_writes_filtered_tables_without_target_row_preflight(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")

    result = migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=FakeSourceReader({"users": [{"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"}]}),
        target=FakeTargetWriter(existing_rows={"users": 1}),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(existing_table_policy=ExistingTablePolicy.APPEND),
    )

    assert result.tables[0].status == "completed"


def test_sync_policy_upserts_source_rows_and_deletes_target_rows_missing_from_source(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    target = FakeTargetWriter(existing_rows={"users": 2})

    result = migrate_tables(
        job_id="job-1",
        tables=(users,),
        source=FakeSourceReader(
            {
                "users": [
                    {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                    {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
                ]
            }
        ),
        target=target,
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1, commit_interval=1, existing_table_policy=ExistingTablePolicy.SYNC),
    )

    assert result.tables[0].status == "completed"
    assert [batch[1] for batch in target.upserted_batches] == [("id",), ("id",)]
    assert ("users", (1,)) in target.sync_keys
    assert ("users", (2,)) in target.sync_keys
    assert target.deleted_sync_tables == ["users"]


def test_sync_policy_requires_primary_or_unique_key(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    no_key_users = TableSchema(
        ref=users.ref,
        columns=users.columns,
        primary_key=None,
        indexes=(),
        foreign_keys=users.foreign_keys,
        estimated_rows=users.estimated_rows,
    )

    result = migrate_tables(
        job_id="job-1",
        tables=(no_key_users,),
        source=FakeSourceReader({"users": []}),
        target=FakeTargetWriter(),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC),
    )

    assert result.tables[0].status == "failed"
    assert "Sync requires" in result.tables[0].message


def test_parallel_table_count_runs_table_workers_without_reordering_table_batches(tmp_path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    source = FakeSourceReader(
        {
            "users": [
                {"id": 1, "email": "a@example.com", "profile": {}, "created_at": "2026-01-01"},
                {"id": 2, "email": "b@example.com", "profile": {}, "created_at": "2026-01-02"},
            ],
            "orders": [
                {"id": 1, "total_amount": "10.00"},
                {"id": 2, "total_amount": "20.00"},
            ],
        }
    )
    target = FakeTargetWriter()

    result = migrate_tables(
        job_id="job-1",
        tables=snapshot.tables,
        source=source,
        target=target,
        checkpoint_store=CheckpointStore(tmp_path / "checkpoint.sqlite"),
        event_publisher=QueueEventPublisher(Queue()),
        migration_config=MigrationConfig(batch_size=1, commit_interval=1, parallel_table_count=2),
    )

    rows_by_table = {
        table_name: [row["id"] for _, rows in target.written_batches if _ == table_name for row in rows]
        for table_name in {"users", "orders"}
    }
    assert {table.status for table in result.tables} == {"completed"}
    assert rows_by_table["users"] == [1, 2]
    assert rows_by_table["orders"] == [1, 2]
