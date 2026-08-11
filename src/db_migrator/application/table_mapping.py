from __future__ import annotations

from typing import Any

from db_migrator.schema.models import RowData, SamplePosition, TableRef, TableSchema
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

