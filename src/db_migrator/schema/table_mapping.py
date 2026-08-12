from __future__ import annotations

from dataclasses import replace

from db_migrator.config.models import AppConfig, Dbms, WatermarkConfig
from db_migrator.schema.models import ForeignKeySchema, SchemaSnapshot, TableRef, TableSchema


def table_key(table: TableRef | TableSchema) -> str:
    table_ref = table.ref if isinstance(table, TableSchema) else table
    return f"{table_ref.schema}.{table_ref.name}"


class TableMappingResolver:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def target_ref_for(self, source_ref: TableRef) -> TableRef:
        table_config = self._config.tables.get(table_key(source_ref))
        target_schema = _default_target_schema_name(self._config, source_ref)
        if table_config is None:
            return TableRef(schema=target_schema, name=source_ref.name)
        return TableRef(
            schema=table_config.target_schema or target_schema,
            name=table_config.target_table or source_ref.name,
        )

    def target_schema_for(self, source_table: TableSchema) -> TableSchema:
        return replace(
            source_table,
            ref=self.target_ref_for(source_table.ref),
            foreign_keys=tuple(self._target_foreign_key(foreign_key) for foreign_key in source_table.foreign_keys),
        )

    def target_snapshot_for(self, source_snapshot: SchemaSnapshot) -> SchemaSnapshot:
        return SchemaSnapshot(
            tables=tuple(self.target_schema_for(table) for table in source_snapshot.tables),
            non_table_objects=source_snapshot.non_table_objects,
        )

    def incremental_watermarks(self) -> dict[str, WatermarkConfig]:
        watermarks = dict(self._config.incremental.watermarks)
        for key, table_config in self._config.tables.items():
            incremental = table_config.incremental
            if not incremental.watermark_column:
                continue
            source_table = key.rsplit(".", 1)[-1]
            watermarks[source_table] = WatermarkConfig(
                column=incremental.watermark_column,
                start_value=incremental.start_value,
                end_value=incremental.end_value,
            )
        return watermarks

    def _target_foreign_key(self, foreign_key: ForeignKeySchema) -> ForeignKeySchema:
        return replace(foreign_key, referenced_table=self.target_ref_for(foreign_key.referenced_table))


def _default_target_schema_name(config: AppConfig, source_ref: TableRef) -> str:
    if config.target.schema_name:
        return config.target.schema_name
    if config.target.dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return config.target.database
    return source_ref.schema
