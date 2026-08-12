from __future__ import annotations

from typing import Protocol

from db_migrator.core.dml_migration import stable_order_columns
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.models import RowData, SamplePosition, TableRef, TableSchema


class ValidationRowReader(Protocol):
    def count_rows(self, table: TableRef) -> int:
        """Return row count for a validation endpoint."""

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        """Return deterministic sample rows for checksum validation."""


class SourceTargetValidationReader:
    def __init__(self, source: ValidationRowReader, target: ValidationRowReader, column_plans: dict[TableRef, ColumnPlan] | None = None) -> None:
        self._source = source
        self._target = target
        self._plans = column_plans or {}

    def count_rows(self, side: str, table: TableRef) -> int:
        plan = self._plans.get(table)
        if side == "source":
            return self._source.count_rows(plan.source_table.ref if plan is not None else table)
        if side == "target":
            return self._target.count_rows(plan.target_table.ref if plan is not None else table)
        raise ValueError(f"Unknown validation side: {side}")

    def sample_rows(
        self,
        side: str,
        table_schema: TableSchema,
        sample_size: int,
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        plan = self._plans.get(table_schema.ref)
        if side == "source":
            if plan is None:
                columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
                order_by = stable_order_columns(table_schema, columns)
                return self._source.sample_rows(table_schema.ref, columns, sample_size, order_by, position)
            read_columns = plan.read_columns or tuple(column.name for column in table_schema.columns if not column.is_generated)
            order_by = stable_order_columns(plan.source_table, read_columns)
            return plan.transform_rows(self._source.sample_rows(plan.source_table.ref, read_columns, sample_size, order_by, position))
        if side == "target":
            target_table = plan.target_table if plan is not None else table_schema
            columns = tuple(column.name for column in target_table.columns if not column.is_generated)
            order_by = stable_order_columns(target_table, columns)
            return self._target.sample_rows(target_table.ref, columns, sample_size, order_by, position)
        raise ValueError(f"Unknown validation side: {side}")
