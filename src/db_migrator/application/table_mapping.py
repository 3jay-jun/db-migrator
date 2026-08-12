from __future__ import annotations

from typing import Any

from db_migrator.schema.models import RowData, SamplePosition, TableRef, TableSchema
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.table_mapping import TableMappingResolver


class TargetMappingAdapter:
    def __init__(self, target: Any, resolver: TableMappingResolver) -> None:
        self._target = target
        self._resolver = resolver

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> Any:
        return self._target.write_batch(self._resolver.target_schema_for(table_schema), rows)

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> Any:
        return self._target.upsert_batch(self._resolver.target_schema_for(table_schema), rows, keys)

    def count_rows(self, table: TableRef) -> int:
        return self._target.count_rows(self._resolver.target_ref_for(table))

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        return self._target.sample_rows(self._resolver.target_ref_for(table), columns, sample_size, order_by, position)

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        self._target.begin_sync_keys(self._resolver.target_schema_for(table_schema), keys)

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> None:
        self._target.record_sync_keys(self._resolver.target_schema_for(table_schema), rows, keys)

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        return self._target.delete_rows_not_in_sync_keys(self._resolver.target_schema_for(table_schema), keys)

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        self._target.end_sync_keys(self._resolver.target_schema_for(table_schema))


class ColumnPlanTargetAdapter:
    def __init__(self, target: Any, column_plans: dict[TableRef, ColumnPlan]) -> None:
        self._target = target
        self._plans = column_plans

    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> Any:
        plan = self._plan_for(table_schema)
        return self._target.write_batch(plan.target_table, plan.transform_rows(rows))

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> Any:
        plan = self._plan_for(table_schema)
        return self._target.upsert_batch(plan.target_table, plan.transform_rows(rows), plan.target_key_columns_for(keys))

    def count_rows(self, table: TableRef) -> int:
        plan = self._plans.get(table)
        return self._target.count_rows(plan.target_table.ref if plan is not None else table)

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        plan = self._plans.get(table)
        target_table = plan.target_table if plan is not None else None
        return self._target.sample_rows(
            target_table.ref if target_table is not None else table,
            columns,
            sample_size,
            order_by,
            position,
        )

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        plan = self._plan_for(table_schema)
        self._target.begin_sync_keys(plan.target_table, plan.target_key_columns_for(keys))

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> None:
        plan = self._plan_for(table_schema)
        self._target.record_sync_keys(plan.target_table, plan.transform_rows(rows), plan.target_key_columns_for(keys))

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        plan = self._plan_for(table_schema)
        return self._target.delete_rows_not_in_sync_keys(plan.target_table, plan.target_key_columns_for(keys))

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        self._target.end_sync_keys(self._plan_for(table_schema).target_table)

    def commit(self) -> None:
        self._target.commit()

    def _plan_for(self, table_schema: TableSchema) -> ColumnPlan:
        try:
            return self._plans[table_schema.ref]
        except KeyError as exc:
            raise ValueError(f"Column plan is missing for table: {table_schema.ref.schema}.{table_schema.ref.name}") from exc
