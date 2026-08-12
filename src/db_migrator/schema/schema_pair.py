from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from db_migrator.config.models import AppConfig
from db_migrator.schema.column_plan import ColumnPlan, build_column_plan
from db_migrator.schema.models import SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.table_mapping import TableMappingResolver


class SchemaOrigin(StrEnum):
    TARGET_EXISTING = "target_existing"
    SOURCE_MAPPED = "source_mapped"


@dataclass(frozen=True)
class ResolvedTablePair:
    source_table: TableSchema
    target_table: TableSchema
    schema_origin: SchemaOrigin
    column_plan: ColumnPlan

    @property
    def target_exists(self) -> bool:
        return self.schema_origin is SchemaOrigin.TARGET_EXISTING


@dataclass(frozen=True)
class SchemaPairPlan:
    pairs: tuple[ResolvedTablePair, ...]

    @property
    def source_snapshot(self) -> SchemaSnapshot:
        return SchemaSnapshot(tables=tuple(pair.source_table for pair in self.pairs))

    @property
    def target_snapshot(self) -> SchemaSnapshot:
        return SchemaSnapshot(tables=tuple(pair.target_table for pair in self.pairs))

    @property
    def column_plans(self) -> dict[TableRef, ColumnPlan]:
        return {pair.source_table.ref: pair.column_plan for pair in self.pairs}


class SchemaPairResolver:
    def __init__(self, config: AppConfig, *, target_schema_name: str | None = None) -> None:
        self._config = config
        self._table_mapping = TableMappingResolver(config)
        self._target_schema_name = target_schema_name

    def resolve(
        self,
        *,
        source_snapshot: SchemaSnapshot,
        target_snapshot: SchemaSnapshot | None = None,
    ) -> SchemaPairPlan:
        target_tables = _target_table_index(target_snapshot)
        pairs: list[ResolvedTablePair] = []
        for source_table in source_snapshot.tables:
            mapped_target_table = self._mapped_target_table(source_table)
            existing_target_table = target_tables.get(_table_key(mapped_target_table.ref))
            schema_origin = SchemaOrigin.TARGET_EXISTING if existing_target_table is not None else SchemaOrigin.SOURCE_MAPPED
            target_table = existing_target_table or mapped_target_table
            pairs.append(
                ResolvedTablePair(
                    source_table=source_table,
                    target_table=target_table,
                    schema_origin=schema_origin,
                    column_plan=build_column_plan(config=self._config, source_table=source_table, target_table=target_table),
                )
            )
        return SchemaPairPlan(pairs=tuple(pairs))

    def _mapped_target_table(self, source_table: TableSchema) -> TableSchema:
        mapped = self._table_mapping.target_schema_for(source_table)
        if self._target_schema_name is None:
            return mapped
        return replace(mapped, ref=TableRef(schema=self._target_schema_name, name=mapped.ref.name))


def _target_table_index(snapshot: SchemaSnapshot | None) -> dict[tuple[str, str], TableSchema]:
    if snapshot is None:
        return {}
    return {_table_key(table.ref): table for table in snapshot.tables}


def _table_key(table: TableRef) -> tuple[str, str]:
    return (table.schema, table.name)
