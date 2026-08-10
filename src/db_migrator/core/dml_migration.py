from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from db_migrator.config.models import MigrationConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.events import EventLevel, EventPublisher, EventType, MigrationEvent, ProgressSnapshot
from db_migrator.schema.models import ReadCursor, RowBatch, RowData, TableRef, TableSchema, WriteResult


class SourceRowReader(Protocol):
    def read_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        """Yield source rows in batches."""


class TargetBatchWriter(Protocol):
    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> WriteResult:
        """Write one batch to target."""

    def commit(self) -> None:
        """Commit pending writes."""


@dataclass(frozen=True)
class TableMigrationResult:
    table: TableRef
    status: str
    rows_written: int
    batches_written: int
    message: str | None = None


@dataclass(frozen=True)
class DmlMigrationResult:
    job_id: str
    tables: tuple[TableMigrationResult, ...]

    @property
    def rows_written(self) -> int:
        return sum(table.rows_written for table in self.tables)


@dataclass(frozen=True)
class ResumePlan:
    mode: str
    table_cursors: dict[TableRef, ReadCursor]
    selected_tables: tuple[TableRef, ...] | None = None


@dataclass(frozen=True)
class _PendingBatch:
    batch: RowBatch
    write_result: WriteResult


def migrate_tables(
    *,
    job_id: str,
    tables: tuple[TableSchema, ...],
    source: SourceRowReader,
    target: TargetBatchWriter,
    checkpoint_store: CheckpointStore,
    event_publisher: EventPublisher,
    migration_config: MigrationConfig,
    resume_plan: ResumePlan | None = None,
) -> DmlMigrationResult:
    results = []
    selected_tables = set(resume_plan.selected_tables) if resume_plan and resume_plan.selected_tables is not None else None
    for table in tables:
        if selected_tables is not None and table.ref not in selected_tables:
            continue
        results.append(
            _migrate_one_table(
                job_id=job_id,
                table=table,
                source=source,
                target=target,
                checkpoint_store=checkpoint_store,
                event_publisher=event_publisher,
                migration_config=migration_config,
                start_cursor=_start_cursor_for_table(resume_plan, table),
            )
        )
    return DmlMigrationResult(job_id=job_id, tables=tuple(results))


def _migrate_one_table(
    *,
    job_id: str,
    table: TableSchema,
    source: SourceRowReader,
    target: TargetBatchWriter,
    checkpoint_store: CheckpointStore,
    event_publisher: EventPublisher,
    migration_config: MigrationConfig,
    start_cursor: ReadCursor | None,
) -> TableMigrationResult:
    batch_size = _effective_batch_size(table, migration_config)
    columns = _writable_columns(table)
    order_by = stable_order_columns(table, columns)
    rows_written = 0
    batches_written = 0
    uncommitted_rows = 0
    pending_batches: list[_PendingBatch] = []
    latest_next_offset = start_cursor.offset if start_cursor is not None else 0
    event_publisher.publish(
        MigrationEvent(
            job_id=job_id,
            level=EventLevel.INFO,
            type=EventType.DML_STARTED,
            message=f"DML started for {table.ref.schema}.{table.ref.name}.",
            table=table.ref.name,
            payload={"batch_size": batch_size, "columns": columns},
        )
    )

    try:
        for batch in source.read_rows(table.ref, columns, start_cursor, batch_size, order_by):
            write_result = target.write_batch(table, batch.rows)
            if not write_result.success:
                checkpoint_store.save_batch_failure(job_id, batch, write_result.message)
                event_publisher.publish(_table_failed_event(job_id, table, write_result.message))
                return TableMigrationResult(
                    table=table.ref,
                    status="failed",
                    rows_written=rows_written,
                    batches_written=batches_written,
                    message=write_result.message,
                )

            rows_written += write_result.rows_written
            uncommitted_rows += write_result.rows_written
            batches_written += 1
            latest_next_offset = batch.next_cursor.offset if batch.next_cursor is not None else latest_next_offset
            pending_batches.append(_PendingBatch(batch=batch, write_result=write_result))
            if uncommitted_rows >= migration_config.commit_interval:
                target.commit()
                _save_committed_batches(
                    job_id=job_id,
                    table=table,
                    pending_batches=pending_batches,
                    checkpoint_store=checkpoint_store,
                    event_publisher=event_publisher,
                    rows_written=rows_written,
                    commit_interval=migration_config.commit_interval,
                )
                pending_batches.clear()
                uncommitted_rows = 0
    except KeyboardInterrupt:
        checkpoint_store.save_table_cancelled(
            job_id=job_id,
            table=table.ref,
            batch_number=batches_written + 1,
            next_offset=latest_next_offset,
            message="Migration cancelled by user.",
        )
        event_publisher.publish(
            MigrationEvent(
                job_id=job_id,
                level=EventLevel.WARNING,
                type=EventType.JOB_CANCELLED,
                message=f"Migration cancelled while processing {table.ref.name}.",
                table=table.ref.name,
            )
        )
        return TableMigrationResult(
            table=table.ref,
            status="cancelled",
            rows_written=rows_written,
            batches_written=batches_written,
            message="Migration cancelled by user.",
        )
    except Exception as exc:
        event_publisher.publish(_table_failed_event(job_id, table, str(exc)))
        return TableMigrationResult(
            table=table.ref,
            status="failed",
            rows_written=rows_written,
            batches_written=batches_written,
            message=str(exc),
        )

    if uncommitted_rows > 0:
        target.commit()
        _save_committed_batches(
            job_id=job_id,
            table=table,
            pending_batches=pending_batches,
            checkpoint_store=checkpoint_store,
            event_publisher=event_publisher,
            rows_written=rows_written,
            commit_interval=migration_config.commit_interval,
        )

    checkpoint_store.save_table_completed(
        job_id=job_id,
        table=table.ref,
        batch_number=batches_written,
        next_offset=latest_next_offset,
        committed_rows=rows_written,
    )
    event_publisher.publish(
        MigrationEvent(
            job_id=job_id,
            level=EventLevel.INFO,
            type=EventType.TABLE_COMPLETED,
            message=f"Table completed: {table.ref.name}.",
            table=table.ref.name,
            payload={"rows_written": rows_written, "batches_written": batches_written},
        )
    )
    return TableMigrationResult(
        table=table.ref,
        status="completed",
        rows_written=rows_written,
        batches_written=batches_written,
    )


def _effective_batch_size(table: TableSchema, migration_config: MigrationConfig) -> int:
    if migration_config.large_row_batch_size is None:
        return migration_config.batch_size

    if any(column.common_type.kind.value in {"text", "json", "binary"} for column in table.columns):
        return migration_config.large_row_batch_size

    return migration_config.batch_size


def build_resume_plan(job_id: str, tables: tuple[TableSchema, ...], checkpoint_store: CheckpointStore) -> ResumePlan:
    table_cursors: dict[TableRef, ReadCursor] = {}
    selected_tables = []
    for table in tables:
        checkpoint = checkpoint_store.latest_checkpoint_for_table(job_id, table.ref)
        if checkpoint is None:
            table_cursors[table.ref] = ReadCursor(offset=0)
            selected_tables.append(table.ref)
            continue
        if checkpoint.status == "table_completed":
            continue
        if checkpoint.status == "completed" and checkpoint.next_offset is not None:
            table_cursors[table.ref] = ReadCursor(offset=checkpoint.next_offset)
            selected_tables.append(table.ref)
        elif checkpoint.status in {"failed", "cancelled"}:
            table_cursors[table.ref] = ReadCursor(offset=checkpoint.next_offset or 0)
            selected_tables.append(table.ref)
    return ResumePlan(mode="resume", table_cursors=table_cursors, selected_tables=tuple(selected_tables))


def build_retry_failed_plan(job_id: str, checkpoint_store: CheckpointStore) -> ResumePlan:
    failed_tables = checkpoint_store.failed_tables(job_id)
    table_cursors = {table: ReadCursor(offset=0) for table in failed_tables}
    return ResumePlan(mode="retry_failed", table_cursors=table_cursors, selected_tables=failed_tables)


def _writable_columns(table: TableSchema) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if not column.is_generated)


def stable_order_columns(table: TableSchema, columns: tuple[str, ...]) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return tuple(column for column in table.primary_key.columns if column in columns)
    return columns


def _start_cursor_for_table(resume_plan: ResumePlan | None, table: TableSchema) -> ReadCursor | None:
    if resume_plan is None:
        return None
    return resume_plan.table_cursors.get(table.ref)


def _save_committed_batches(
    *,
    job_id: str,
    table: TableSchema,
    pending_batches: list[_PendingBatch],
    checkpoint_store: CheckpointStore,
    event_publisher: EventPublisher,
    rows_written: int,
    commit_interval: int,
) -> None:
    for pending_batch in pending_batches:
        checkpoint = checkpoint_store.save_batch_success(job_id, pending_batch.batch)
        event_publisher.publish(
            MigrationEvent(
                job_id=job_id,
                level=EventLevel.INFO,
                type=EventType.BATCH_COMMITTED,
                message=f"Batch committed for {table.ref.name}.",
                table=table.ref.name,
                progress=ProgressSnapshot(
                    completed_units=rows_written,
                    total_units=table.estimated_rows or rows_written,
                    current_unit=table.ref.name,
                ),
                payload={
                    "batch_number": pending_batch.batch.batch_number,
                    "rows_written": pending_batch.write_result.rows_written,
                    "commit_interval": commit_interval,
                },
            )
        )
        event_publisher.publish(
            MigrationEvent(
                job_id=job_id,
                level=EventLevel.INFO,
                type=EventType.CHECKPOINT_SAVED,
                message=f"Checkpoint saved for {table.ref.name}.",
                table=table.ref.name,
                payload={
                    "batch_number": checkpoint.batch_number,
                    "next_offset": checkpoint.next_offset,
                },
            )
        )


def _table_failed_event(job_id: str, table: TableSchema, message: str) -> MigrationEvent:
    return MigrationEvent(
        job_id=job_id,
        level=EventLevel.ERROR,
        type=EventType.TABLE_FAILED,
        message=f"Table failed: {table.ref.name}. {message}",
        table=table.ref.name,
        payload={"error": message},
    )
