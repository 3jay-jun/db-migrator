from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db_migrator.config.models import Dbms
from db_migrator.schema.common_types import CommonType, CommonTypeKind, SchemaWarning, TypePolicy
from db_migrator.schema.models import (
    ColumnSchema,
    ForeignKeySchema,
    IndexSchema,
    PrimaryKey,
    SchemaObjectKind,
    SchemaObjectSummary,
    SchemaSnapshot,
    TableRef,
    TableSchema,
)
from db_migrator.schema.type_mapping import mysql_type_to_common, postgres_type_to_common


class SchemaSnapshotLoadError(ValueError):
    pass


def load_schema_snapshot_from_json(snapshot_path: Path, *, source_dbms: Dbms = Dbms.POSTGRESQL) -> SchemaSnapshot:
    if not snapshot_path.exists():
        raise SchemaSnapshotLoadError(f"Schema snapshot file does not exist: {snapshot_path}")

    try:
        raw_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SchemaSnapshotLoadError(f"Schema snapshot is not valid JSON: {snapshot_path}") from exc

    if not isinstance(raw_snapshot, dict):
        raise SchemaSnapshotLoadError(f"Schema snapshot must contain a JSON object: {snapshot_path}")

    return _parse_schema_snapshot(raw_snapshot, snapshot_path, source_dbms=source_dbms)


def _parse_schema_snapshot(raw_snapshot: dict[str, Any], snapshot_path: Path, *, source_dbms: Dbms) -> SchemaSnapshot:
    raw_tables = raw_snapshot.get("tables")
    if not isinstance(raw_tables, list):
        raise SchemaSnapshotLoadError(f"Schema snapshot must contain a tables array: {snapshot_path}")

    return SchemaSnapshot(
        tables=tuple(_parse_table(raw_table, snapshot_path, source_dbms=source_dbms) for raw_table in raw_tables),
        non_table_objects=tuple(_parse_schema_object(raw_object, snapshot_path) for raw_object in raw_snapshot.get("non_table_objects", [])),
    )


def _parse_table(raw_table: Any, snapshot_path: Path, *, source_dbms: Dbms) -> TableSchema:
    if not isinstance(raw_table, dict):
        raise SchemaSnapshotLoadError(f"Each table entry must be an object: {snapshot_path}")

    schema = _required_string(raw_table, "schema", snapshot_path)
    table_name = _required_string(raw_table, "name", snapshot_path)
    raw_columns = raw_table.get("columns")
    if not isinstance(raw_columns, list):
        raise SchemaSnapshotLoadError(f"Table columns must be an array: {schema}.{table_name}")

    raw_primary_key = raw_table.get("primary_key")
    primary_key = None
    if raw_primary_key is not None:
        if not isinstance(raw_primary_key, list) or not all(isinstance(column, str) for column in raw_primary_key):
            raise SchemaSnapshotLoadError(f"primary_key must be an array of strings: {schema}.{table_name}")
        primary_key = PrimaryKey(columns=tuple(raw_primary_key))

    return TableSchema(
        ref=TableRef(schema=schema, name=table_name),
        columns=tuple(_parse_column(raw_column, snapshot_path, source_dbms=source_dbms) for raw_column in raw_columns),
        primary_key=primary_key,
        indexes=tuple(_parse_index(raw_index, schema, table_name, snapshot_path) for raw_index in raw_table.get("indexes", [])),
        foreign_keys=tuple(_parse_foreign_key(raw_fk, schema, table_name, snapshot_path) for raw_fk in raw_table.get("foreign_keys", [])),
        estimated_rows=raw_table.get("estimated_rows"),
    )


def _parse_column(raw_column: Any, snapshot_path: Path, *, source_dbms: Dbms) -> ColumnSchema:
    if not isinstance(raw_column, dict):
        raise SchemaSnapshotLoadError(f"Each column entry must be an object: {snapshot_path}")

    source_type = _required_string(raw_column, "source_type", snapshot_path)
    is_generated = bool(raw_column.get("is_generated", False))
    common_type = _parse_common_type(raw_column.get("common_type"), source_type, is_generated, source_dbms=source_dbms)
    return ColumnSchema(
        name=_required_string(raw_column, "name", snapshot_path),
        source_type=source_type,
        common_type=common_type,
        nullable=bool(raw_column.get("nullable", True)),
        default=raw_column.get("default"),
        is_generated=is_generated,
        generation_expression=raw_column.get("generation_expression"),
        ordinal_position=int(raw_column.get("ordinal_position", 0)),
        auto_increment=bool(raw_column.get("auto_increment", False)),
        warnings=common_type.warnings,
    )


def _parse_common_type(raw_common_type: Any, source_type: str, is_generated: bool, *, source_dbms: Dbms) -> CommonType:
    if raw_common_type is None:
        return _source_type_to_common(source_dbms, source_type, is_generated=is_generated)

    if not isinstance(raw_common_type, dict):
        raise SchemaSnapshotLoadError("common_type must be an object when provided.")

    warnings = tuple(_parse_warning(raw_warning) for raw_warning in raw_common_type.get("warnings", []))
    return CommonType(
        kind=CommonTypeKind(str(raw_common_type["kind"])),
        policy=TypePolicy(str(raw_common_type["policy"])),
        length=raw_common_type.get("length"),
        precision=raw_common_type.get("precision"),
        scale=raw_common_type.get("scale"),
        source_type=raw_common_type.get("source_type", source_type),
        warnings=warnings,
    )


def _source_type_to_common(source_dbms: Dbms, source_type: str, *, is_generated: bool) -> CommonType:
    if source_dbms is Dbms.POSTGRESQL:
        return postgres_type_to_common(source_type, is_generated=is_generated)
    if source_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return mysql_type_to_common(source_type, is_generated=is_generated)
    raise SchemaSnapshotLoadError(f"Unsupported schema snapshot source DBMS: {source_dbms.value}")


def _parse_warning(raw_warning: Any) -> SchemaWarning:
    if not isinstance(raw_warning, dict):
        raise SchemaSnapshotLoadError("warning must be an object.")
    return SchemaWarning(
        code=str(raw_warning["code"]),
        message=str(raw_warning["message"]),
        policy=TypePolicy(str(raw_warning["policy"])),
    )


def _parse_foreign_key(raw_fk: Any, schema: str, table_name: str, snapshot_path: Path) -> ForeignKeySchema:
    if not isinstance(raw_fk, dict):
        raise SchemaSnapshotLoadError(f"foreign_keys entries must be objects: {schema}.{table_name}")
    referenced = raw_fk.get("referenced_table")
    if not isinstance(referenced, dict):
        raise SchemaSnapshotLoadError(f"foreign key referenced_table must be an object: {schema}.{table_name}")
    columns = raw_fk.get("columns")
    referenced_columns = raw_fk.get("referenced_columns")
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise SchemaSnapshotLoadError(f"foreign key columns must be a string array: {schema}.{table_name}")
    if not isinstance(referenced_columns, list) or not all(isinstance(column, str) for column in referenced_columns):
        raise SchemaSnapshotLoadError(f"foreign key referenced_columns must be a string array: {schema}.{table_name}")
    return ForeignKeySchema(
        name=_required_string(raw_fk, "name", snapshot_path),
        columns=tuple(columns),
        referenced_table=TableRef(
            schema=_required_string(referenced, "schema", snapshot_path),
            name=_required_string(referenced, "name", snapshot_path),
        ),
        referenced_columns=tuple(referenced_columns),
    )


def _parse_index(raw_index: Any, schema: str, table_name: str, snapshot_path: Path) -> IndexSchema:
    if not isinstance(raw_index, dict):
        raise SchemaSnapshotLoadError(f"indexes entries must be objects: {schema}.{table_name}")
    columns = raw_index.get("columns")
    if not isinstance(columns, list) or not all(isinstance(column, str) for column in columns):
        raise SchemaSnapshotLoadError(f"index columns must be a string array: {schema}.{table_name}")
    return IndexSchema(
        name=_required_string(raw_index, "name", snapshot_path),
        columns=tuple(columns),
        unique=bool(raw_index.get("unique", False)),
        method=raw_index.get("method"),
        auto_create_candidate=bool(raw_index.get("auto_create_candidate", True)),
        manual_review_reason=raw_index.get("manual_review_reason"),
    )


def _parse_schema_object(raw_object: Any, snapshot_path: Path) -> SchemaObjectSummary:
    if not isinstance(raw_object, dict):
        raise SchemaSnapshotLoadError(f"non_table_objects entries must be objects: {snapshot_path}")
    parent_table = None
    raw_parent_table = raw_object.get("parent_table")
    if raw_parent_table is not None:
        if not isinstance(raw_parent_table, dict):
            raise SchemaSnapshotLoadError(f"parent_table must be an object: {snapshot_path}")
        parent_table = TableRef(
            schema=_required_string(raw_parent_table, "schema", snapshot_path),
            name=_required_string(raw_parent_table, "name", snapshot_path),
        )
    return SchemaObjectSummary(
        kind=SchemaObjectKind(str(raw_object["kind"])),
        schema=_required_string(raw_object, "schema", snapshot_path),
        name=_required_string(raw_object, "name", snapshot_path),
        parent_table=parent_table,
    )


def _required_string(raw_value: dict[str, Any], key: str, snapshot_path: Path) -> str:
    value = raw_value.get(key)
    if not isinstance(value, str) or not value:
        raise SchemaSnapshotLoadError(f"Missing required string '{key}' in schema snapshot: {snapshot_path}")
    return value
