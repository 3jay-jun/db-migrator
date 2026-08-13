from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Protocol

from db_migrator.config.models import ExistingTablePolicy, MigrationConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.events import EventLevel, EventPublisher, EventType, MigrationEvent, ProgressSnapshot
from db_migrator.schema.column_plan import ColumnPlan
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

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> WriteResult:
        """Upsert one batch to target."""

    def fetch_rows_by_keys(
        self,
        table_schema: TableSchema,
        keys: tuple[str, ...],
        rows: tuple[RowData, ...],
    ) -> dict[tuple[object, ...], RowData]:
        """Return target rows keyed by the provided source row key values."""

    def commit(self) -> None:
        """Commit pending writes."""

    def count_rows(self, table: TableRef) -> int:
        """Return target row count when supported."""

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        """Prepare target-side key tracking for source-of-truth sync."""

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> None:
        """Record source keys seen during sync."""

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        """Delete target rows whose keys were not seen in the source."""

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        """Clean up target-side key tracking."""


@dataclass(frozen=True)
class TableMigrationResult:
    table: TableRef
    status: str
    rows_written: int
    batches_written: int
    message: str | None = None
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_deleted: int = 0
    rows_unchanged: int = 0
    rows_processed: int = 0

    @property
    def changed_rows(self) -> int:
        return self.rows_inserted + self.rows_updated + self.rows_deleted


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


@dataclass(frozen=True)
class _CommitContext:
    job_id: str
    table: TableSchema
    pending_batches: list[_PendingBatch]
    checkpoint_store: CheckpointStore
    event_publisher: EventPublisher
    rows_written: int
    commit_interval: int
    started_at: float
    throttle_sleep_ms: int


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
    column_plans: dict[TableRef, ColumnPlan] | None = None,
    data_execution_report: Path | None = None,
) -> DmlMigrationResult:
    work_items: list[TableSchema | TableMigrationResult] = []
    selected_tables = set(resume_plan.selected_tables) if resume_plan and resume_plan.selected_tables is not None else None
    for table in tables:
        if selected_tables is not None and table.ref not in selected_tables:
            continue
        preflight_result = _preflight_table_result(
            job_id=job_id,
            table=table,
            target=target,
            checkpoint_store=checkpoint_store,
            event_publisher=event_publisher,
            migration_config=migration_config,
            resume_plan=resume_plan,
            column_plan=(column_plans or {}).get(table.ref),
        )
        work_items.append(preflight_result or table)

    target_lock = Lock()
    if migration_config.parallel_table_count == 1:
        results = [
            item if isinstance(item, TableMigrationResult) else _migrate_one_table(
                job_id=job_id,
                table=item,
                source=source,
                target=target,
                checkpoint_store=checkpoint_store,
                event_publisher=event_publisher,
                migration_config=migration_config,
                start_cursor=_start_cursor_for_table(resume_plan, item),
                target_lock=target_lock,
                column_plan=(column_plans or {}).get(item.ref),
            )
            for item in work_items
        ]
    else:
        results = [item for item in work_items if isinstance(item, TableMigrationResult)]
        pending_tables = [item for item in work_items if isinstance(item, TableSchema)]
        with ThreadPoolExecutor(max_workers=migration_config.parallel_table_count) as executor:
            futures = [
                executor.submit(
                    _migrate_one_table,
                    job_id=job_id,
                    table=table,
                    source=source,
                    target=target,
                    checkpoint_store=checkpoint_store,
                    event_publisher=event_publisher,
                    migration_config=migration_config,
                    start_cursor=_start_cursor_for_table(resume_plan, table),
                    target_lock=target_lock,
                    column_plan=(column_plans or {}).get(table.ref),
                )
                for table in pending_tables
            ]
            results.extend(future.result() for future in futures)
    result = DmlMigrationResult(job_id=job_id, tables=tuple(results))
    if data_execution_report is not None:
        write_data_sync_execution_report(result, data_execution_report)
    return result


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
    target_lock: Lock,
    column_plan: ColumnPlan | None = None,
) -> TableMigrationResult:
    batch_size = _effective_batch_size(table, migration_config)
    columns = column_plan.read_columns if column_plan is not None and column_plan.read_columns else _writable_columns(table)
    order_by = stable_order_columns(table, columns)
    effective_start_cursor = start_cursor or _initial_cursor_for_table(table, order_by)
    sync_keys = resume_key_columns(table, order_by) if migration_config.existing_table_policy is ExistingTablePolicy.SYNC else ()
    if migration_config.existing_table_policy is ExistingTablePolicy.SYNC and not sync_keys:
        message = "Sync requires primary key or unique index columns."
        event_publisher.publish(_table_failed_event(job_id, table, message))
        return TableMigrationResult(table=table.ref, status="failed", rows_written=0, batches_written=0, message=message)
    rows_written = 0
    rows_inserted = 0
    rows_updated = 0
    rows_unchanged = 0
    batches_written = 0
    uncommitted_rows = 0
    pending_batches: list[_PendingBatch] = []
    latest_cursor = effective_start_cursor
    started_at = monotonic()
    event_publisher.publish(
        MigrationEvent(
            job_id=job_id,
            level=EventLevel.INFO,
            type=EventType.DML_STARTED,
            message=f"DML started for {table.ref.schema}.{table.ref.name}.",
            table=table.ref.name,
            payload={"batch_size": batch_size, "columns": columns, "cursor_strategy": effective_start_cursor.strategy.value},
        )
    )

    try:
        if sync_keys:
            with target_lock:
                target.begin_sync_keys(table, sync_keys)
        for batch in source.read_rows(table.ref, columns, effective_start_cursor, batch_size, order_by):
            try:
                with target_lock:
                    if sync_keys:
                        inserts, updates, unchanged = _classify_sync_rows(target, table, batch.rows, sync_keys, columns)
                        changed_rows = inserts + updates
                        write_result = target.upsert_batch(table, changed_rows, sync_keys) if changed_rows else WriteResult(success=True, rows_written=0, message="No changed rows to upsert.")
                        target.record_sync_keys(table, batch.rows, sync_keys)
                    else:
                        inserts = batch.rows
                        updates = ()
                        unchanged = ()
                        write_result = target.write_batch(table, batch.rows)
            except Exception as exc:
                checkpoint_store.save_batch_failure(job_id, batch, str(exc))
                event_publisher.publish(_table_failed_event(job_id, table, str(exc)))
                return TableMigrationResult(
                    table=table.ref,
                    status="failed",
                    rows_written=rows_written,
                    batches_written=batches_written,
                    message=str(exc),
                )
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
            rows_inserted += len(inserts)
            rows_updated += len(updates)
            rows_unchanged += len(unchanged)
            uncommitted_rows += write_result.rows_written
            batches_written += 1
            latest_cursor = batch.next_cursor or latest_cursor
            pending_batches.append(_PendingBatch(batch=batch, write_result=write_result))
            if uncommitted_rows >= migration_config.commit_interval:
                with target_lock:
                    target.commit()
                _save_committed_batches(
                    _CommitContext(
                        job_id=job_id,
                        table=table,
                        pending_batches=pending_batches,
                        checkpoint_store=checkpoint_store,
                        event_publisher=event_publisher,
                        rows_written=rows_written,
                        commit_interval=migration_config.commit_interval,
                        started_at=started_at,
                        throttle_sleep_ms=migration_config.throttle_sleep_ms,
                    )
                )
                pending_batches.clear()
                uncommitted_rows = 0
    except KeyboardInterrupt:
        checkpoint_store.save_table_cancelled(
            job_id=job_id,
            table=table.ref,
            batch_number=batches_written + 1,
            next_offset=latest_cursor.offset,
            message="Migration cancelled by user.",
            cursor=latest_cursor,
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
        if pending_batches:
            checkpoint_store.save_batch_failure(job_id, pending_batches[0].batch, str(exc))
        event_publisher.publish(_table_failed_event(job_id, table, str(exc)))
        return TableMigrationResult(
            table=table.ref,
            status="failed",
            rows_written=rows_written,
            batches_written=batches_written,
            message=str(exc),
        )

    if uncommitted_rows > 0:
        with target_lock:
            target.commit()
        _save_committed_batches(
            _CommitContext(
                job_id=job_id,
                table=table,
                pending_batches=pending_batches,
                checkpoint_store=checkpoint_store,
                event_publisher=event_publisher,
                rows_written=rows_written,
                commit_interval=migration_config.commit_interval,
                started_at=started_at,
                throttle_sleep_ms=migration_config.throttle_sleep_ms,
            )
        )

    rows_deleted = 0
    if sync_keys:
        with target_lock:
            rows_deleted = target.delete_rows_not_in_sync_keys(table, sync_keys)
            target.commit()
            target.end_sync_keys(table)

    checkpoint_store.save_table_completed(
        job_id=job_id,
        table=table.ref,
        batch_number=batches_written,
        next_offset=latest_cursor.offset,
        committed_rows=rows_written,
        cursor=latest_cursor,
    )
    event_publisher.publish(
        MigrationEvent(
            job_id=job_id,
            level=EventLevel.INFO,
            type=EventType.TABLE_COMPLETED,
            message=f"Table completed: {table.ref.name}.",
            table=table.ref.name,
            payload={"rows_written": rows_written, "rows_deleted": rows_deleted, "batches_written": batches_written},
        )
    )
    return TableMigrationResult(
        table=table.ref,
        status="completed",
        rows_written=rows_written,
        batches_written=batches_written,
        rows_inserted=rows_inserted,
        rows_updated=rows_updated,
        rows_deleted=rows_deleted,
        rows_unchanged=rows_unchanged,
        rows_processed=rows_inserted + rows_updated + rows_unchanged + rows_deleted,
    )


def write_data_sync_execution_report(result: DmlMigrationResult, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_id": result.job_id,
        "tables": [
            {
                "schema": table.table.schema,
                "table": table.table.name,
                "status": table.status,
                "rows_inserted": table.rows_inserted,
                "rows_updated": table.rows_updated,
                "rows_deleted": table.rows_deleted,
                "rows_unchanged": table.rows_unchanged,
                "rows_processed": table.rows_processed,
                "rows_written": table.rows_written,
                "changed_rows": table.changed_rows,
                "batches_written": table.batches_written,
                "message": table.message,
            }
            for table in result.tables
        ],
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _classify_sync_rows(
    target: TargetBatchWriter,
    table: TableSchema,
    rows: tuple[RowData, ...],
    keys: tuple[str, ...],
    columns: tuple[str, ...],
) -> tuple[tuple[RowData, ...], tuple[RowData, ...], tuple[RowData, ...]]:
    target_rows = target.fetch_rows_by_keys(table, keys, rows)
    inserts: list[RowData] = []
    updates: list[RowData] = []
    unchanged: list[RowData] = []
    for row in rows:
        key = _row_key(row, keys)
        target_row = target_rows.get(key)
        if target_row is None:
            inserts.append(row)
            continue
        if _row_values_equal(row, target_row, columns):
            unchanged.append(row)
        else:
            updates.append(row)
    return tuple(inserts), tuple(updates), tuple(unchanged)


def _row_key(row: RowData, keys: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row.get(key) for key in keys)


def _row_values_equal(source_row: RowData, target_row: RowData, columns: tuple[str, ...]) -> bool:
    return all(source_row.get(column) == target_row.get(column) for column in columns)


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
            table_cursors[table.ref] = _initial_cursor_for_table(table, stable_order_columns(table, _writable_columns(table)))
            selected_tables.append(table.ref)
            continue
        if checkpoint.status == "table_completed":
            continue
        if checkpoint.status == "completed" and checkpoint.next_offset is not None:
            table_cursors[table.ref] = checkpoint.next_cursor
            selected_tables.append(table.ref)
        elif checkpoint.status in {"failed", "cancelled", "checkpoint_failed_after_commit"}:
            table_cursors[table.ref] = checkpoint.next_cursor
            selected_tables.append(table.ref)
    return ResumePlan(mode="resume", table_cursors=table_cursors, selected_tables=tuple(selected_tables))


def build_retry_failed_plan(job_id: str, checkpoint_store: CheckpointStore) -> ResumePlan:
    failed_tables = checkpoint_store.failed_tables(job_id)
    table_cursors = {
        table: checkpoint.next_cursor if (checkpoint := checkpoint_store.latest_checkpoint_for_table(job_id, table)) else ReadCursor.offset_cursor()
        for table in failed_tables
    }
    return ResumePlan(mode="retry_failed", table_cursors=table_cursors, selected_tables=failed_tables)


def _writable_columns(table: TableSchema) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if not column.is_generated)


def stable_order_columns(table: TableSchema, columns: tuple[str, ...]) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return tuple(column for column in table.primary_key.columns if column in columns)
    return columns


def _initial_cursor_for_table(table: TableSchema, order_by: tuple[str, ...]) -> ReadCursor:
    key_columns = resume_key_columns(table, order_by)
    if key_columns:
        return ReadCursor.keyset_cursor(key_columns=key_columns)
    return ReadCursor.offset_cursor()


def resume_key_columns(table: TableSchema, order_by: tuple[str, ...]) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return tuple(column for column in table.primary_key.columns if column in order_by)
    for index in table.indexes:
        if index.unique:
            return tuple(column for column in index.columns if column in order_by)
    return ()


def _start_cursor_for_table(resume_plan: ResumePlan | None, table: TableSchema) -> ReadCursor | None:
    if resume_plan is None:
        return None
    return resume_plan.table_cursors.get(table.ref)


def _save_committed_batches(context: _CommitContext) -> None:
    committed_rows_before_batch = context.rows_written - sum(
        pending_batch.write_result.rows_written for pending_batch in context.pending_batches
    )
    for pending_batch in context.pending_batches:
        committed_rows_before_batch += pending_batch.write_result.rows_written
        try:
            checkpoint = context.checkpoint_store.save_batch_success(context.job_id, pending_batch.batch)
        except Exception as exc:
            try:
                context.checkpoint_store.save_checkpoint_failure_after_commit(context.job_id, pending_batch.batch, str(exc))
            except Exception:
                pass
            context.event_publisher.publish(
                MigrationEvent(
                    job_id=context.job_id,
                    level=EventLevel.ERROR,
                    type=EventType.CHECKPOINT_SAVED,
                    message=f"Checkpoint failed after target commit for {context.table.ref.name}.",
                    table=context.table.ref.name,
                    payload={"error": str(exc), "status": "checkpoint_failed_after_commit"},
                )
            )
            raise

        elapsed = max(monotonic() - context.started_at, 0.001)
        rows_per_sec = committed_rows_before_batch / elapsed
        estimated_rows = context.table.estimated_rows if context.table.estimated_rows and context.table.estimated_rows > 0 else None
        progress_total = max(estimated_rows or committed_rows_before_batch, committed_rows_before_batch)
        eta_seconds = None
        if estimated_rows and rows_per_sec > 0:
            eta_seconds = max(int((estimated_rows - committed_rows_before_batch) / rows_per_sec), 0)
        context.event_publisher.publish(
            MigrationEvent(
                job_id=context.job_id,
                level=EventLevel.INFO,
                type=EventType.BATCH_COMMITTED,
                message=f"Batch committed for {context.table.ref.name}.",
                table=context.table.ref.name,
                progress=ProgressSnapshot(
                    completed_units=committed_rows_before_batch,
                    total_units=progress_total,
                    current_unit=context.table.ref.name,
                ),
                payload={
                    "batch_number": pending_batch.batch.batch_number,
                    "rows_written": pending_batch.write_result.rows_written,
                    "commit_interval": context.commit_interval,
                    "rows_per_sec": rows_per_sec,
                    "eta_seconds": eta_seconds,
                    "cursor_strategy": checkpoint.cursor_strategy,
                    "next_offset": checkpoint.next_offset,
                    "last_key_values": checkpoint.last_key_values,
                },
            )
        )
        context.event_publisher.publish(
            MigrationEvent(
                job_id=context.job_id,
                level=EventLevel.INFO,
                type=EventType.CHECKPOINT_SAVED,
                message=f"Checkpoint saved for {context.table.ref.name}.",
                table=context.table.ref.name,
                payload={
                    "batch_number": checkpoint.batch_number,
                    "next_offset": checkpoint.next_offset,
                    "cursor_strategy": checkpoint.cursor_strategy,
                    "last_key_values": checkpoint.last_key_values,
                },
            )
        )
        if context.throttle_sleep_ms > 0:
            sleep(context.throttle_sleep_ms / 1000)


def _preflight_table_result(
    *,
    job_id: str,
    table: TableSchema,
    target: TargetBatchWriter,
    checkpoint_store: CheckpointStore,
    event_publisher: EventPublisher,
    migration_config: MigrationConfig,
    resume_plan: ResumePlan | None,
    column_plan: ColumnPlan | None,
) -> TableMigrationResult | None:
    if column_plan is not None and column_plan.unresolved_target_columns:
        message = "; ".join(f"{item.column.name}: {item.message}" for item in column_plan.unresolved_target_columns)
        event_publisher.publish(_table_failed_event(job_id, table, message))
        return TableMigrationResult(table=table.ref, status="blocked", rows_written=0, batches_written=0, message=message)

    if column_plan is not None and migration_config.strict_source_only_columns:
        unresolved_source_only = tuple(item for item in column_plan.source_only_columns if not item.configured)
        if unresolved_source_only:
            message = "Strict source-only column policy requires explicit action for: " + ", ".join(
                item.column.name for item in unresolved_source_only
            )
            event_publisher.publish(_table_failed_event(job_id, table, message))
            return TableMigrationResult(table=table.ref, status="blocked", rows_written=0, batches_written=0, message=message)

    if column_plan is not None and migration_config.existing_table_policy is ExistingTablePolicy.SYNC:
        columns = column_plan.read_columns if column_plan.read_columns else _writable_columns(table)
        source_sync_keys = resume_key_columns(table, stable_order_columns(table, columns))
        if source_sync_keys and not column_plan.target_key_columns_for(source_sync_keys):
            message = "Sync blocked because source key columns cannot be mapped to target write columns: " + ", ".join(
                source_sync_keys
            )
            event_publisher.publish(_table_failed_event(job_id, table, message))
            return TableMigrationResult(table=table.ref, status="blocked", rows_written=0, batches_written=0, message=message)

    checkpoint = checkpoint_store.latest_checkpoint_for_table(job_id, table.ref)
    if migration_config.existing_table_policy is ExistingTablePolicy.APPEND:
        if checkpoint is not None:
            message = "Append policy skipped this table because this job already has checkpoints for it."
            event_publisher.publish(_table_skipped_event(job_id, table, message, status="append_checkpoint_exists"))
            return TableMigrationResult(table=table.ref, status="skipped", rows_written=0, batches_written=0, message=message)

    if (
        resume_plan is None
        and checkpoint is not None
        and checkpoint.status == "table_completed"
        and migration_config.checkpoint_resume
        and migration_config.existing_table_policy is not ExistingTablePolicy.SYNC
    ):
        target_rows = _target_row_count(target, table.ref)
        if target_rows == 0:
            message = "Completed checkpoint is stale because target table is empty. Re-running from the beginning."
            event_publisher.publish(_checkpoint_stale_event(job_id, table, message))
            return None
        if target_rows is not None and target_rows < checkpoint.committed_rows:
            message = (
                "Completed checkpoint does not match target row count. "
                "Use resume/validate, truncate_reload, or a new job id before re-running."
            )
            event_publisher.publish(_table_failed_event(job_id, table, message))
            return TableMigrationResult(table=table.ref, status="blocked", rows_written=0, batches_written=0, message=message)
        message = "Table already completed for this job and target rows are present. Use resume/retry, truncate_reload, or a new job id to re-run."
        event_publisher.publish(_table_skipped_event(job_id, table, message, status="already_completed"))
        return TableMigrationResult(table=table.ref, status="skipped", rows_written=0, batches_written=0, message=message)
    return None


def _target_row_count(target: TargetBatchWriter, table: TableRef) -> int | None:
    count_rows = getattr(target, "count_rows", None)
    if not callable(count_rows):
        return None
    return int(count_rows(table))


def _checkpoint_stale_event(job_id: str, table: TableSchema, message: str) -> MigrationEvent:
    return MigrationEvent(
        job_id=job_id,
        level=EventLevel.WARNING,
        type=EventType.CHECKPOINT_STALE,
        message=f"Checkpoint stale: {table.ref.name}. {message}",
        table=table.ref.name,
        payload={"status": "stale_checkpoint_ignored"},
    )


def _table_skipped_event(job_id: str, table: TableSchema, message: str, *, status: str) -> MigrationEvent:
    return MigrationEvent(
        job_id=job_id,
        level=EventLevel.WARNING,
        type=EventType.TABLE_COMPLETED,
        message=f"Table skipped: {table.ref.name}. {message}",
        table=table.ref.name,
        payload={"status": status},
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
