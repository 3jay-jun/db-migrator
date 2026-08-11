from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from db_migrator.schema.models import ReadCursor, RowBatch, RowData, SamplePosition, SchemaSnapshot, TableRef, TableSchema, WriteResult


@dataclass(frozen=True)
class DdlResult:
    table_name: str
    ddl: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    message: str
    rows_affected: int | None = None


class DdlGenerator(Protocol):
    def generate_create_table(self, table_schema: TableSchema) -> DdlResult:
        """Generate target CREATE TABLE DDL for a common table schema."""


class SourceAdapter(Protocol):
    def test_connection(self) -> bool:
        """Return whether the source database connection is available."""

    def scan_schema(self, schema: str) -> SchemaSnapshot:
        """Scan source database metadata into the common schema model."""

    def read_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        """Stream source rows in batches without materializing the full table."""

    def count_rows(self, table: TableRef) -> int:
        """Return the row count for one table."""

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        """Return deterministic sample rows for checksum validation."""


class TargetAdapter(Protocol):
    def test_connection(self) -> bool:
        """Return whether the target database connection is available."""

    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> WriteResult:
        """Write one row batch to the target table."""

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> WriteResult:
        """Upsert one row batch to the target table."""

    def count_rows(self, table: TableRef) -> int:
        """Return the row count for one table."""
