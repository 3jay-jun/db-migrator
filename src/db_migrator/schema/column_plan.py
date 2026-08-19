from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from db_migrator.config.models import AppConfig, Dbms, SourceOnlyColumnAction
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.dialect import qualified_table_name, quote_identifier
from db_migrator.schema.models import ColumnSchema, RowData, TableSchema
from db_migrator.schema.table_mapping import table_key
from db_migrator.schema.type_mapping import common_type_to_mysql, common_type_to_postgres, mysql_type_to_common, postgres_type_to_common


@dataclass(frozen=True)
class SourceOnlyColumnPlan:
    column: ColumnSchema
    action: SourceOnlyColumnAction
    alter_table_ddl: str | None = None
    message: str | None = None
    configured: bool = False


@dataclass(frozen=True)
class UnresolvedTargetColumn:
    column: ColumnSchema
    message: str


@dataclass(frozen=True)
class _TargetColumnMapping:
    target_column: str
    source_column: str | None = None
    default: Any | None = None
    write_null: bool = False
    write_column: bool = True


@dataclass(frozen=True)
class ColumnPlan:
    source_table: TableSchema
    target_table: TableSchema
    read_columns: tuple[str, ...]
    write_columns: tuple[str, ...]
    source_only_columns: tuple[SourceOnlyColumnPlan, ...]
    unresolved_target_columns: tuple[UnresolvedTargetColumn, ...]
    _mappings: tuple[_TargetColumnMapping, ...]
    rename_column_ddls: tuple[str, ...] = ()
    type_change_ddls: tuple[str, ...] = ()
    add_column_ddls: tuple[str, ...] = ()

    @property
    def blocks_execution(self) -> bool:
        return bool(self.unresolved_target_columns)

    def transform_rows(self, rows: tuple[RowData, ...]) -> tuple[RowData, ...]:
        return tuple(self.transform_row(row) for row in rows)

    def transform_row(self, row: RowData) -> RowData:
        transformed: RowData = {}
        for mapping in self._mappings:
            if not mapping.write_column:
                continue
            if mapping.source_column is not None:
                transformed[mapping.target_column] = row.get(mapping.source_column)
            elif mapping.write_null:
                transformed[mapping.target_column] = None
            else:
                transformed[mapping.target_column] = mapping.default
        return transformed

    def target_key_columns_for(self, source_key_columns: tuple[str, ...]) -> tuple[str, ...]:
        target_key_columns: list[str] = []
        for source_key_column in source_key_columns:
            target_column = next(
                (
                    mapping.target_column
                    for mapping in self._mappings
                    if mapping.source_column == source_key_column and mapping.write_column
                ),
                None,
            )
            if target_column is None:
                return ()
            target_key_columns.append(target_column)
        return tuple(target_key_columns)


def build_column_plan(*, config: AppConfig, source_table: TableSchema, target_table: TableSchema) -> ColumnPlan:
    table_config = config.tables.get(table_key(source_table.ref))
    configured_columns = table_config.columns if table_config is not None else {}
    source_only_actions = table_config.source_only_columns if table_config is not None else {}
    source_columns = {column.name: column for column in source_table.columns if not column.is_generated}
    base_target_columns = tuple(column for column in target_table.columns if not column.is_generated)
    base_target_columns_by_name = {column.name: column for column in base_target_columns}
    target_column_names = {column.name for column in base_target_columns}
    configured_source_columns = {
        column_config.source
        for column_config in configured_columns.values()
        if column_config.source is not None and not column_config.skip
    }
    invalid_target_type_columns = {
        target_column: column_config
        for target_column, column_config in configured_columns.items()
        if column_config.target_type is not None and not _is_valid_target_type(config.target.dbms, column_config.target_type)
    }
    rename_columns = {
        column_config.source: target_column
        for target_column, column_config in configured_columns.items()
        if target_column not in target_column_names
        and column_config.source is not None
        and column_config.source in target_column_names
        and not column_config.skip
        and target_column not in invalid_target_type_columns
    }
    configured_add_columns = tuple(
        replace(
            source_columns[column_config.source],
            name=target_column,
            source_type=column_config.target_type or source_columns[column_config.source].source_type,
            common_type=_common_type_for_target_type(config.target.dbms, column_config.target_type) or source_columns[column_config.source].common_type,
            ordinal_position=len(base_target_columns) + index,
        )
        for index, (target_column, column_config) in enumerate(configured_columns.items(), start=1)
        if target_column not in target_column_names
        and column_config.source is not None
        and column_config.source in source_columns
        and column_config.source not in rename_columns
        and not column_config.skip
        and target_column not in invalid_target_type_columns
    )
    default_source_only_add_columns = tuple(
        replace(source_column, ordinal_position=len(base_target_columns) + len(configured_add_columns) + index)
        for index, source_column in enumerate(source_columns.values(), start=1)
        if source_column.name not in target_column_names
        and source_column.name not in configured_source_columns
        and (
            source_only_actions.get(source_column.name, SourceOnlyColumnAction.ADD_TO_TARGET)
            is SourceOnlyColumnAction.ADD_TO_TARGET
        )
    )
    renamed_target_columns = tuple(
        replace(column, name=rename_columns[column.name]) if column.name in rename_columns else column
        for column in base_target_columns
    )
    target_columns = renamed_target_columns + configured_add_columns + default_source_only_add_columns
    target_table = replace(
        target_table,
        columns=target_columns,
        primary_key=_rename_primary_key(target_table.primary_key, rename_columns),
    )
    used_source_columns: set[str] = set()
    read_columns: list[str] = []
    write_columns: list[str] = []
    mappings: list[_TargetColumnMapping] = []
    unresolved: list[UnresolvedTargetColumn] = []
    type_change_ddls: list[str] = []

    for target_column in target_columns:
        column_config = configured_columns.get(target_column.name)
        if target_column.name in invalid_target_type_columns:
            unresolved.append(
                UnresolvedTargetColumn(
                    column=target_column,
                    message=f"Configured target type is not valid for {config.target.dbms.value}: {column_config.target_type if column_config is not None else ''}",
                )
            )
            continue
        if column_config is not None and column_config.skip:
            mappings.append(_TargetColumnMapping(target_column=target_column.name, write_column=False))
            continue

        source_column = column_config.source if column_config is not None else None
        if source_column is None and target_column.name in source_columns:
            source_column = target_column.name

        if source_column is not None:
            if source_column not in source_columns:
                unresolved.append(
                    UnresolvedTargetColumn(
                        column=target_column,
                        message=f"Configured source column does not exist: {source_column}",
                    )
                )
                continue
            used_source_columns.add(source_column)
            if source_column not in read_columns:
                read_columns.append(source_column)
            write_columns.append(target_column.name)
            mappings.append(_TargetColumnMapping(target_column=target_column.name, source_column=source_column))
            original_target_column = base_target_columns_by_name.get(source_column if source_column in rename_columns else target_column.name)
            if original_target_column is not None and _requires_type_change(
                config.target.dbms,
                source_columns[source_column],
                original_target_column,
                column_config.target_type if column_config is not None else None,
            ):
                type_change_ddls.append(
                    _alter_table_change_type_ddl(config.target.dbms, target_table, target_column.name, source_columns[source_column], column_config.target_type if column_config is not None else None)
                )
            continue

        if column_config is not None and column_config.null:
            write_columns.append(target_column.name)
            mappings.append(_TargetColumnMapping(target_column=target_column.name, write_null=True))
            continue

        if column_config is not None and column_config.default is not None:
            write_columns.append(target_column.name)
            mappings.append(_TargetColumnMapping(target_column=target_column.name, default=column_config.default))
            continue

        if target_column.nullable or target_column.default is not None:
            mappings.append(_TargetColumnMapping(target_column=target_column.name, write_column=False))
            continue

        unresolved.append(
            UnresolvedTargetColumn(
                column=target_column,
                message="Required target column has no source/default/null mapping.",
            )
        )

    target_column_names = {column.name for column in target_columns}
    source_only_columns = tuple(
        _source_only_column_plan(
            source_column,
            target_table=target_table,
            target_dbms=config.target.dbms,
            action=source_only_actions.get(source_column.name, SourceOnlyColumnAction.ADD_TO_TARGET),
            configured=source_column.name in source_only_actions,
        )
        for source_column in source_columns.values()
        if source_column.name not in target_column_names
        and source_column.name not in used_source_columns
        and source_only_actions.get(source_column.name) in {SourceOnlyColumnAction.IGNORE, SourceOnlyColumnAction.MANUAL}
    )
    for target_column, column_config in invalid_target_type_columns.items():
        if target_column in {column.name for column in target_columns}:
            continue
        source_column = source_columns.get(column_config.source or target_column)
        if source_column is not None:
            unresolved.append(
                UnresolvedTargetColumn(
                    column=replace(source_column, name=target_column),
                    message=f"Configured target type is not valid for {config.target.dbms.value}: {column_config.target_type}",
                )
            )

    return ColumnPlan(
        source_table=source_table,
        target_table=target_table,
        read_columns=tuple(read_columns),
        write_columns=tuple(write_columns),
        source_only_columns=source_only_columns,
        unresolved_target_columns=tuple(unresolved),
        _mappings=tuple(mappings),
        rename_column_ddls=tuple(
            _alter_table_rename_column_ddl(config.target.dbms, target_table, source_column, target_column)
            for source_column, target_column in sorted(rename_columns.items())
        ),
        type_change_ddls=tuple(type_change_ddls),
        add_column_ddls=tuple(
            _alter_table_add_column_ddl(config.target.dbms, target_table, column)
            for column in configured_add_columns + default_source_only_add_columns
        ),
    )


def _source_only_column_plan(
    column: ColumnSchema,
    *,
    target_table: TableSchema,
    target_dbms: Dbms,
    action: SourceOnlyColumnAction,
    configured: bool,
) -> SourceOnlyColumnPlan:
    if action is SourceOnlyColumnAction.ADD_TO_TARGET:
        return SourceOnlyColumnPlan(
            column=column,
            action=action,
            alter_table_ddl=_alter_table_add_column_ddl(target_dbms, target_table, column),
            message="Source-only column selected as target ALTER candidate.",
            configured=configured,
        )
    if action is SourceOnlyColumnAction.MANUAL:
        return SourceOnlyColumnPlan(
            column=column,
            action=SourceOnlyColumnAction.IGNORE,
            message="Source-only column ignored.",
            configured=configured,
        )
    return SourceOnlyColumnPlan(
        column=column,
        action=action,
        message="Source-only column ignored.",
        configured=configured,
    )


def _alter_table_add_column_ddl(target_dbms: Dbms, target_table: TableSchema, column: ColumnSchema) -> str:
    quote = lambda value: quote_identifier(target_dbms, value)
    target_type = _target_type(target_dbms, column)
    table_name = qualified_table_name(target_dbms, target_table.ref.schema, target_table.ref.name)
    nullable_sql = "" if column.nullable else " NOT NULL"
    default_sql = f" DEFAULT {column.default}" if column.default is not None else ""
    return f"ALTER TABLE {table_name} ADD COLUMN {quote(column.name)} {target_type}{default_sql}{nullable_sql};"


def _alter_table_rename_column_ddl(target_dbms: Dbms, target_table: TableSchema, source_column: str, target_column: str) -> str:
    quote = lambda value: quote_identifier(target_dbms, value)
    table_name = qualified_table_name(target_dbms, target_table.ref.schema, target_table.ref.name)
    return f"ALTER TABLE {table_name} RENAME COLUMN {quote(source_column)} TO {quote(target_column)};"


def _alter_table_change_type_ddl(target_dbms: Dbms, target_table: TableSchema, target_column: str, source_column: ColumnSchema, target_type_override: str | None) -> str:
    quote = lambda value: quote_identifier(target_dbms, value)
    target_type = target_type_override or _target_type(target_dbms, source_column)
    table_name = qualified_table_name(target_dbms, target_table.ref.schema, target_table.ref.name)
    if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        nullable_sql = "" if source_column.nullable else " NOT NULL"
        default_sql = f" DEFAULT {source_column.default}" if source_column.default is not None else ""
        return f"ALTER TABLE {table_name} MODIFY COLUMN {quote(target_column)} {target_type}{default_sql}{nullable_sql};"
    return f"ALTER TABLE {table_name} ALTER COLUMN {quote(target_column)} TYPE {target_type};"


def _requires_type_change(target_dbms: Dbms, source_column: ColumnSchema, target_column: ColumnSchema, target_type_override: str | None) -> bool:
    if target_type_override is not None:
        return _normalize_type(target_type_override) != _normalize_type(target_column.source_type)
    source_type = source_column.common_type
    target_type = target_column.common_type
    return (
        source_type.kind,
        source_type.length,
        source_type.precision,
        source_type.scale,
    ) != (
        target_type.kind,
        target_type.length,
        target_type.precision,
        target_type.scale,
    )


def _common_type_for_target_type(target_dbms: Dbms, target_type: str | None):
    if target_type is None:
        return None
    if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return mysql_type_to_common(target_type)
    return postgres_type_to_common(target_type)


def _is_valid_target_type(target_dbms: Dbms, target_type: str) -> bool:
    return _common_type_for_target_type(target_dbms, target_type).kind is not CommonTypeKind.UNKNOWN


def _normalize_type(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _rename_primary_key(primary_key, rename_columns: dict[str, str]):
    if primary_key is None:
        return None
    return replace(primary_key, columns=tuple(rename_columns.get(column, column) for column in primary_key.columns))


def _target_type(target_dbms: Dbms, column: ColumnSchema) -> str:
    if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return common_type_to_mysql(column.common_type)
    return common_type_to_postgres(column.common_type)
