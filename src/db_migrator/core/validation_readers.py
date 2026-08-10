from __future__ import annotations

from typing import Protocol

from db_migrator.core.dml_migration import stable_order_columns
from db_migrator.schema.models import RowData, TableRef, TableSchema


class ValidationRowReader(Protocol):
    def count_rows(self, table: TableRef) -> int:
        """Return row count for a validation endpoint."""

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
    ) -> tuple[RowData, ...]:
        """Return deterministic sample rows for checksum validation."""


class SourceTargetValidationReader:
    def __init__(self, source: ValidationRowReader, target: ValidationRowReader) -> None:
        self._source = source
        self._target = target

    def count_rows(self, side: str, table: TableRef) -> int:
        if side == "source":
            return self._source.count_rows(table)
        if side == "target":
            return self._target.count_rows(table)
        raise ValueError(f"Unknown validation side: {side}")

    def sample_rows(self, side: str, table_schema: TableSchema, sample_size: int) -> tuple[RowData, ...]:
        columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        order_by = stable_order_columns(table_schema, columns)
        if side == "source":
            return self._source.sample_rows(table_schema.ref, columns, sample_size, order_by)
        if side == "target":
            return self._target.sample_rows(table_schema.ref, columns, sample_size, order_by)
        raise ValueError(f"Unknown validation side: {side}")
