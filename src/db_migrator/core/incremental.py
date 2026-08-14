from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Callable, Protocol

from db_migrator.config.models import IncrementalConfig, MigrationConfig, WatermarkConfig
from db_migrator.schema.models import RowBatch, RowData, TableRef, TableSchema, WriteResult


class IncrementalSourceReader(Protocol):
    def read_incremental_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        watermark: WatermarkConfig,
        batch_size: int,
    ) -> Iterator[RowBatch]:
        """Yield source rows matching one watermark range."""


class IncrementalTargetWriter(Protocol):
    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> WriteResult:
        """Upsert one incremental batch."""

    def commit(self) -> None:
        """Commit pending writes."""


@dataclass(frozen=True)
class IncrementalTableResult:
    table: TableRef
    status: str
    rows_upserted: int
    batches_upserted: int
    watermark_column: str | None
    watermark_start_value: str | None = None
    watermark_end_value: str | None = None
    upsert_keys: tuple[str, ...] = ()
    target_table: TableRef | None = None
    message: str | None = None


@dataclass(frozen=True)
class IncrementalMigrationReport:
    job_id: str
    delete_sync_supported: bool
    tables: tuple[IncrementalTableResult, ...]

    @property
    def rows_upserted(self) -> int:
        return sum(table.rows_upserted for table in self.tables)


def migrate_incremental_tables(
    *,
    job_id: str,
    tables: tuple[TableSchema, ...],
    source: IncrementalSourceReader,
    target: IncrementalTargetWriter,
    migration_config: MigrationConfig,
    incremental_config: IncrementalConfig,
    target_table_resolver: Callable[[TableRef], TableRef] | None = None,
) -> IncrementalMigrationReport:
    results = tuple(
        _migrate_one_incremental_table(
            table=table,
            source=source,
            target=target,
            migration_config=migration_config,
            incremental_config=incremental_config,
            target_table_resolver=target_table_resolver,
        )
        for table in tables
    )
    return IncrementalMigrationReport(job_id=job_id, delete_sync_supported=False, tables=results)


def _migrate_one_incremental_table(
    *,
    table: TableSchema,
    source: IncrementalSourceReader,
    target: IncrementalTargetWriter,
    migration_config: MigrationConfig,
    incremental_config: IncrementalConfig,
    target_table_resolver: Callable[[TableRef], TableRef] | None,
) -> IncrementalTableResult:
    target_table = target_table_resolver(table.ref) if target_table_resolver is not None else None
    watermark = incremental_config.watermarks.get(table.ref.name)
    if watermark is None:
        return IncrementalTableResult(
            table=table.ref,
            status="skipped",
            rows_upserted=0,
            batches_upserted=0,
            watermark_column=None,
            target_table=target_table,
            message="Watermark column is not configured.",
        )

    keys = _upsert_keys(table)
    if not keys:
        return IncrementalTableResult(
            table=table.ref,
            status="skipped",
            rows_upserted=0,
            batches_upserted=0,
            watermark_column=watermark.column,
            watermark_start_value=watermark.start_value,
            watermark_end_value=watermark.end_value,
            target_table=target_table,
            message="Upsert requires primary key or unique index.",
        )

    rows_upserted = 0
    uncommitted_rows = 0
    batches_upserted = 0
    try:
        for batch in source.read_incremental_rows(
            table.ref,
            _writable_columns(table),
            watermark,
            migration_config.batch_size,
        ):
            write_result = target.upsert_batch(table, batch.rows, keys)
            if not write_result.success:
                return IncrementalTableResult(
                    table=table.ref,
                    status="failed",
                    rows_upserted=rows_upserted,
                    batches_upserted=batches_upserted,
                    watermark_column=watermark.column,
                    watermark_start_value=watermark.start_value,
                    watermark_end_value=watermark.end_value,
                    upsert_keys=keys,
                    target_table=target_table,
                    message=write_result.message,
                )
            rows_upserted += write_result.rows_written
            uncommitted_rows += write_result.rows_written
            batches_upserted += 1
            if uncommitted_rows >= migration_config.commit_interval:
                target.commit()
                uncommitted_rows = 0
        if uncommitted_rows > 0:
            target.commit()
    except Exception as exc:
        return IncrementalTableResult(
            table=table.ref,
            status="failed",
            rows_upserted=rows_upserted,
            batches_upserted=batches_upserted,
            watermark_column=watermark.column,
            watermark_start_value=watermark.start_value,
            watermark_end_value=watermark.end_value,
            upsert_keys=keys,
            target_table=target_table,
            message=str(exc),
        )

    return IncrementalTableResult(
        table=table.ref,
        status="completed",
        rows_upserted=rows_upserted,
        batches_upserted=batches_upserted,
        watermark_column=watermark.column,
        watermark_start_value=watermark.start_value,
        watermark_end_value=watermark.end_value,
        upsert_keys=keys,
        target_table=target_table,
    )


def _upsert_keys(table: TableSchema) -> tuple[str, ...]:
    if table.primary_key is not None and table.primary_key.columns:
        return table.primary_key.columns
    for index in table.indexes:
        if index.unique:
            return index.columns
    return ()


def _writable_columns(table: TableSchema) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if not column.is_generated)
