from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol
from uuid import UUID

from db_migrator.adapters.base import DdlResult, ExecutionResult
from db_migrator.adapters.error_detail import connection_test_failure_message, safe_error_detail
from db_migrator.config.models import WatermarkConfig
from db_migrator.config.models import SourceConfig, TargetConfig
from db_migrator.schema.common_types import CommonType, CommonTypeKind
from db_migrator.schema.models import (
    ColumnSchema,
    CursorStrategy,
    ForeignKeySchema,
    IndexSchema,
    PrimaryKey,
    ReadCursor,
    RowBatch,
    RowData,
    SamplePosition,
    SchemaObjectKind,
    SchemaObjectSummary,
    SchemaSnapshot,
    TableRef,
    TableSchema,
    WriteResult,
)
from db_migrator.schema.type_mapping import common_type_to_postgres, postgres_type_to_common


class PostgresAdapterError(RuntimeError):
    pass


class SourceTypeMapper(Protocol):
    def __call__(self, source_type: str, *, is_generated: bool = False) -> CommonType:
        """Map a source DBMS type into the common schema type."""


class PostgresTargetAdapter:
    def __init__(self, config: TargetConfig) -> None:
        self._config = config
        self._dml_connection = None

    def test_connection(self) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("select 1")
                    row = cursor.fetchone()
                    return _first_row_value(row) == 1
        except Exception as exc:
            raise PostgresAdapterError(connection_test_failure_message("PostgreSQL", self._config, exc)) from exc

    def table_exists(self, table_schema: TableSchema) -> bool:
        query = """
            select count(*) as table_count
            from information_schema.tables
            where table_schema = %s
              and table_name = %s
        """
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, (table_schema.ref.schema, table_schema.ref.name))
                    row = cursor.fetchone()
                    return int(row["table_count"] if isinstance(row, dict) else row[0]) > 0
        except Exception as exc:
            raise PostgresAdapterError(f"Failed to check target table existence: {table_schema.ref.name}") from exc

    def execute_ddl(self, ddl: str) -> ExecutionResult:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    for statement in _split_postgres_ddl_statements(ddl):
                        cursor.execute(statement)
                connection.commit()
            return ExecutionResult(success=True, message="DDL executed.")
        except Exception as exc:
            raise PostgresAdapterError(
                "Failed to execute target DDL. "
                f"target_database={self._config.database} ddl={_compact_sql(ddl)} detail={safe_error_detail(exc)}"
            ) from exc

    def truncate_table(self, table_schema: TableSchema) -> ExecutionResult:
        ddl = f"TRUNCATE TABLE {_qualified_postgres_table_name(table_schema)}"
        return self.execute_ddl(ddl)

    def drop_table(self, table_schema: TableSchema) -> ExecutionResult:
        ddl = f"DROP TABLE {_qualified_postgres_table_name(table_schema)}"
        return self.execute_ddl(ddl)

    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> WriteResult:
        if not rows:
            return WriteResult(success=True, rows_written=0, message="No rows to write.")

        writable_columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        placeholders = ", ".join(["%s"] * len(writable_columns))
        column_sql = ", ".join(_quote_postgres_identifier(column) for column in writable_columns)
        table_sql = _qualified_postgres_table_name(table_schema)
        sql = f"INSERT INTO {table_sql} ({column_sql}) VALUES ({placeholders})"
        values = [_postgres_row_values(table_schema, row, writable_columns) for row in rows]

        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(sql, values)
            return WriteResult(success=True, rows_written=len(rows), message="Batch written.")
        except Exception as exc:
            raise PostgresAdapterError(
                f"Failed to write target batch for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> WriteResult:
        if not keys:
            return WriteResult(success=False, rows_written=0, message="Upsert requires primary or unique key columns.")
        if not rows:
            return WriteResult(success=True, rows_written=0, message="No rows to upsert.")

        writable_columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        placeholders = ", ".join(["%s"] * len(writable_columns))
        column_sql = ", ".join(_quote_postgres_identifier(column) for column in writable_columns)
        table_sql = _qualified_postgres_table_name(table_schema)
        conflict_sql = ", ".join(_quote_postgres_identifier(column) for column in keys)
        update_columns = tuple(column for column in writable_columns if column not in keys)
        update_sql = _postgres_upsert_update_sql(update_columns, keys)
        sql = (
            f"INSERT INTO {table_sql} ({column_sql}) VALUES ({placeholders}) "
            f"ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}"
        )
        values = [_postgres_row_values(table_schema, row, writable_columns) for row in rows]

        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(sql, values)
            return WriteResult(success=True, rows_written=len(rows), message="Batch upserted.")
        except Exception as exc:
            raise PostgresAdapterError(
                f"Failed to upsert target batch for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def count_rows(self, table: TableRef) -> int:
        table_sql = _qualified_postgres_table_ref(table)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"select count(*) as row_count from {table_sql}")
                    row = cursor.fetchone()
                    return int(row["row_count"] if isinstance(row, dict) else row[0])
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL row count failed for table: {table.name}") from exc

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        temp_table = _postgres_sync_temp_table_name(table_schema)
        key_sql = ", ".join(_quote_postgres_identifier(key) for key in keys)
        table_sql = _qualified_postgres_table_name(table_schema)
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
                cursor.execute(f"CREATE TEMP TABLE {temp_table} AS SELECT {key_sql} FROM {table_sql} WHERE false")
        except Exception as exc:
            raise PostgresAdapterError(f"Failed to prepare target sync keys for table: {table_schema.ref.name}") from exc

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> None:
        if not rows:
            return
        temp_table = _postgres_sync_temp_table_name(table_schema)
        key_sql = ", ".join(_quote_postgres_identifier(key) for key in keys)
        placeholders = ", ".join(["%s"] * len(keys))
        values = [tuple(row.get(key) for key in keys) for row in rows]
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(f"INSERT INTO {temp_table} ({key_sql}) VALUES ({placeholders})", values)
        except Exception as exc:
            raise PostgresAdapterError(f"Failed to record target sync keys for table: {table_schema.ref.name}") from exc

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        temp_table = _postgres_sync_temp_table_name(table_schema)
        table_sql = _qualified_postgres_table_name(table_schema)
        match_sql = " AND ".join(
            f"{table_sql}.{_quote_postgres_identifier(key)} = {temp_table}.{_quote_postgres_identifier(key)}"
            for key in keys
        )
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {table_sql} WHERE NOT EXISTS (SELECT 1 FROM {temp_table} WHERE {match_sql})")
                return int(cursor.rowcount or 0)
        except Exception as exc:
            raise PostgresAdapterError(f"Failed to delete target rows missing from source for table: {table_schema.ref.name}") from exc

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        temp_table = _postgres_sync_temp_table_name(table_schema)
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
        except Exception as exc:
            raise PostgresAdapterError(f"Failed to clean up target sync keys for table: {table_schema.ref.name}") from exc

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        if sample_size <= 0:
            return ()
        column_sql = ", ".join(_quote_postgres_identifier(column) for column in columns)
        table_sql = _qualified_postgres_table_ref(table)
        order_sql = _postgres_order_by_clause(order_by, position=position)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"select {column_sql} from {table_sql}{order_sql} limit %s", (sample_size,))
                    return tuple(dict(row) for row in cursor.fetchall())
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL sample rows failed for table: {table.name}") from exc

    def commit(self) -> None:
        if self._dml_connection is not None:
            self._dml_connection.commit()

    def close(self) -> None:
        if self._dml_connection is not None:
            self._dml_connection.close()
            self._dml_connection = None

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise PostgresAdapterError("psycopg is not installed. Install project dependencies first.") from exc

        return psycopg.connect(
            host=self._config.host,
            port=self._config.port,
            dbname=self._config.database,
            user=self._config.user,
            password=self._config.password,
            row_factory=dict_row_factory(),
        )

    def _ensure_dml_connection(self) -> Any:
        if self._dml_connection is None:
            self._dml_connection = self._connect()
        return self._dml_connection


class PostgresDdlGenerator:
    def __init__(
        self,
        target_database: str | None = None,
        target_type_mapper: Callable[[CommonType], str] | None = None,
    ) -> None:
        self._target_type_mapper = target_type_mapper or common_type_to_postgres

    def generate_create_table(self, table_schema: TableSchema) -> DdlResult:
        column_lines = [self._column_definition(column) for column in table_schema.columns]

        if table_schema.primary_key is not None and table_schema.primary_key.columns:
            columns = ", ".join(_quote_postgres_identifier(column) for column in table_schema.primary_key.columns)
            column_lines.append(f"  PRIMARY KEY ({columns})")

        table_name = _qualified_postgres_table_name(table_schema)
        ddl_body = ",\n".join(column_lines)
        schema_name = _quote_postgres_identifier(table_schema.ref.schema)
        ddl = f"CREATE SCHEMA IF NOT EXISTS {schema_name};\nCREATE TABLE {table_name} (\n{ddl_body}\n);"
        warnings = _unique_warning_messages(
            warning.message
            for column in table_schema.columns
            for warning in column.common_type.warnings + column.warnings
        )
        return DdlResult(table_name=table_name, ddl=ddl, warnings=warnings)

    def _column_definition(self, column: ColumnSchema) -> str:
        column_type = self._target_type_mapper(column.common_type)
        null_sql = "NULL" if column.nullable else "NOT NULL"
        return f"  {_quote_postgres_identifier(column.name)} {column_type} {null_sql}"


class PostgresSourceAdapter:
    def __init__(self, config: SourceConfig, source_type_mapper: SourceTypeMapper | None = None) -> None:
        self._config = config
        self._source_type_mapper = source_type_mapper or postgres_type_to_common

    def test_connection(self) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("select 1")
                    row = cursor.fetchone()
                    return _first_row_value(row) == 1
        except Exception as exc:
            raise PostgresAdapterError(connection_test_failure_message("PostgreSQL", self._config, exc)) from exc

    def scan_schema(self, schema: str) -> SchemaSnapshot:
        try:
            with self._connect() as connection:
                try:
                    columns = self._fetch_columns(connection, schema)
                    primary_keys = self._fetch_primary_keys(connection, schema)
                    indexes = self._fetch_indexes(connection, schema)
                    foreign_keys = self._fetch_foreign_keys(connection, schema)
                    estimated_rows = self._fetch_estimated_rows(connection, schema)
                    non_table_objects = self._fetch_non_table_objects(connection, schema)
                except Exception as exc:
                    raise PostgresAdapterError(
                        f"PostgreSQL schema metadata query failed for schema '{schema}'. "
                        f"Check source.schema and metadata permissions. detail={safe_error_detail(exc)}"
                    ) from exc
        except PostgresAdapterError:
            raise
        except Exception as exc:
            raise PostgresAdapterError(
                "PostgreSQL connection failed before schema scan. "
                f"host={self._config.host} port={self._config.port} "
                f"database={self._config.database} user={self._config.user} schema={schema}. "
                f"detail={safe_error_detail(exc)}"
            ) from exc

        grouped_columns: dict[str, list[ColumnSchema]] = defaultdict(list)
        for row in columns:
            table_name = str(row["table_name"])
            source_type = _format_source_type(row)
            is_generated = row["is_generated"] == "ALWAYS"
            common_type = self._source_type_mapper(source_type, is_generated=is_generated)
            grouped_columns[table_name].append(
                ColumnSchema(
                    name=str(row["column_name"]),
                    source_type=source_type,
                    common_type=common_type,
                    nullable=row["is_nullable"] == "YES",
                    default=row["column_default"],
                    is_generated=is_generated,
                    generation_expression=row["generation_expression"],
                    ordinal_position=int(row["ordinal_position"]),
                    warnings=common_type.warnings,
                )
            )

        tables = tuple(
            TableSchema(
                ref=TableRef(schema=schema, name=table_name),
                columns=tuple(sorted(table_columns, key=lambda column: column.ordinal_position)),
                primary_key=PrimaryKey(tuple(primary_keys[table_name])) if primary_keys[table_name] else None,
                indexes=tuple(indexes[table_name]),
                foreign_keys=tuple(foreign_keys[table_name]),
                estimated_rows=estimated_rows.get(table_name),
            )
            for table_name, table_columns in sorted(grouped_columns.items())
        )
        return SchemaSnapshot(tables=tables, non_table_objects=tuple(non_table_objects))

    def read_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        if batch_size <= 0:
            raise PostgresAdapterError("batch_size must be greater than zero.")

        start_cursor = cursor or ReadCursor.offset_cursor()
        start_offset = start_cursor.offset
        try:
            with self._connect() as connection:
                with connection.cursor(name=f"db_migrator_{table.name}") as server_cursor:
                    server_cursor.itersize = batch_size
                    column_sql = ", ".join(_quote_postgres_identifier(column) for column in columns)
                    table_sql = f"{_quote_postgres_identifier(table.schema)}.{_quote_postgres_identifier(table.name)}"
                    order_sql = _postgres_order_by_clause(order_by)
                    where_sql, params = _postgres_keyset_where_clause(start_cursor)
                    offset_sql = "" if start_cursor.strategy is CursorStrategy.KEYSET else " offset %s"
                    execute_params = params if start_cursor.strategy is CursorStrategy.KEYSET else (*params, start_offset)
                    server_cursor.execute(f"select {column_sql} from {table_sql}{where_sql}{order_sql}{offset_sql}", execute_params)

                    batch_number = 0
                    current_offset = start_offset
                    while rows := server_cursor.fetchmany(batch_size):
                        row_batch = tuple(dict(row) for row in rows)
                        current_offset += len(row_batch)
                        batch_number += 1
                        next_cursor = _next_read_cursor(start_cursor, row_batch, current_offset)
                        yield RowBatch(
                            table=table,
                            rows=row_batch,
                            batch_number=batch_number,
                            start_offset=current_offset - len(row_batch),
                            next_cursor=next_cursor,
                            start_cursor=start_cursor,
                        )
                        start_cursor = next_cursor
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL row streaming failed for table: {table.name}") from exc

    def count_rows(self, table: TableRef) -> int:
        table_sql = f"{_quote_postgres_identifier(table.schema)}.{_quote_postgres_identifier(table.name)}"
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"select count(*) from {table_sql}")
                    row = cursor.fetchone()
                    return int(row["count"] if isinstance(row, dict) else row[0])
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL row count failed for table: {table.name}") from exc

    def sample_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        sample_size: int,
        order_by: tuple[str, ...],
        position: SamplePosition = SamplePosition.FIRST,
    ) -> tuple[RowData, ...]:
        if sample_size <= 0:
            return ()
        column_sql = ", ".join(_quote_postgres_identifier(column) for column in columns)
        table_sql = f"{_quote_postgres_identifier(table.schema)}.{_quote_postgres_identifier(table.name)}"
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    order_sql = _postgres_order_by_clause(order_by, position=position)
                    cursor.execute(f"select {column_sql} from {table_sql}{order_sql} limit %s", (sample_size,))
                    return tuple(dict(row) for row in cursor.fetchall())
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL sample rows failed for table: {table.name}") from exc

    def read_incremental_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        watermark: WatermarkConfig,
        batch_size: int,
    ) -> Iterator[RowBatch]:
        if batch_size <= 0:
            raise PostgresAdapterError("batch_size must be greater than zero.")

        try:
            with self._connect() as connection:
                with connection.cursor(name=f"db_migrator_inc_{table.name}") as server_cursor:
                    server_cursor.itersize = batch_size
                    column_sql = ", ".join(_quote_postgres_identifier(column) for column in columns)
                    table_sql = f"{_quote_postgres_identifier(table.schema)}.{_quote_postgres_identifier(table.name)}"
                    where_sql, params = _watermark_where_clause(watermark)
                    server_cursor.execute(
                        f"select {column_sql} from {table_sql} {where_sql} order by {_quote_postgres_identifier(watermark.column)}",
                        params,
                    )

                    batch_number = 0
                    current_offset = 0
                    while rows := server_cursor.fetchmany(batch_size):
                        row_batch = tuple(dict(row) for row in rows)
                        current_offset += len(row_batch)
                        batch_number += 1
                        yield RowBatch(
                            table=table,
                            rows=row_batch,
                            batch_number=batch_number,
                            start_offset=current_offset - len(row_batch),
                            next_cursor=ReadCursor(offset=current_offset),
                        )
        except Exception as exc:
            raise PostgresAdapterError(f"PostgreSQL incremental row streaming failed for table: {table.name}") from exc

    def _connect(self) -> Any:
        try:
            import psycopg
        except ImportError as exc:
            raise PostgresAdapterError("psycopg is not installed. Install project dependencies first.") from exc

        return psycopg.connect(
            host=self._config.host,
            port=self._config.port,
            dbname=self._config.database,
            user=self._config.user,
            password=self._config.password,
            row_factory=dict_row_factory(),
        )

    def _fetch_columns(self, connection: Any, schema: str) -> list[dict[str, Any]]:
        query = """
            select
                table_name,
                column_name,
                data_type,
                udt_name,
                character_maximum_length,
                numeric_precision,
                numeric_scale,
                datetime_precision,
                is_nullable,
                column_default,
                is_generated,
                generation_expression,
                ordinal_position
            from information_schema.columns
            where table_schema = %s
            order by table_name, ordinal_position
        """
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            return list(cursor.fetchall())

    def _fetch_primary_keys(self, connection: Any, schema: str) -> dict[str, list[str]]:
        query = """
            select
                kcu.table_name,
                kcu.column_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_name = kcu.constraint_name
             and tc.table_schema = kcu.table_schema
             and tc.table_name = kcu.table_name
            where tc.constraint_type = 'PRIMARY KEY'
              and tc.table_schema = %s
            order by kcu.table_name, kcu.ordinal_position
        """
        primary_keys: dict[str, list[str]] = defaultdict(list)
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                primary_keys[str(row["table_name"])].append(str(row["column_name"]))
        return primary_keys

    def _fetch_estimated_rows(self, connection: Any, schema: str) -> dict[str, int]:
        query = """
            select c.relname as table_name, c.reltuples::bigint as estimated_rows
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            where n.nspname = %s
              and c.relkind = 'r'
        """
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            return {str(row["table_name"]): int(row["estimated_rows"]) for row in cursor.fetchall()}

    def _fetch_foreign_keys(self, connection: Any, schema: str) -> dict[str, list[ForeignKeySchema]]:
        query = """
            select
                tc.constraint_name,
                kcu.table_name,
                kcu.column_name,
                ccu.table_schema as referenced_schema,
                ccu.table_name as referenced_table,
                ccu.column_name as referenced_column
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_name = kcu.constraint_name
             and tc.table_schema = kcu.table_schema
             and tc.table_name = kcu.table_name
            join information_schema.constraint_column_usage ccu
              on ccu.constraint_name = tc.constraint_name
             and ccu.table_schema = tc.table_schema
            where tc.constraint_type = 'FOREIGN KEY'
              and tc.table_schema = %s
            order by kcu.table_name, tc.constraint_name, kcu.ordinal_position
        """
        grouped: dict[tuple[str, str, str, str], dict[str, list[str] | str]] = {}
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                key = (
                    str(row["table_name"]),
                    str(row["constraint_name"]),
                    str(row["referenced_schema"]),
                    str(row["referenced_table"]),
                )
                grouped.setdefault(
                    key,
                    {
                        "columns": [],
                        "referenced_columns": [],
                    },
                )
                grouped[key]["columns"].append(str(row["column_name"]))
                grouped[key]["referenced_columns"].append(str(row["referenced_column"]))

        foreign_keys: dict[str, list[ForeignKeySchema]] = defaultdict(list)
        for (table_name, constraint_name, referenced_schema, referenced_table), values in grouped.items():
            foreign_keys[table_name].append(
                ForeignKeySchema(
                    name=constraint_name,
                    columns=tuple(values["columns"]),
                    referenced_table=TableRef(schema=referenced_schema, name=referenced_table),
                    referenced_columns=tuple(values["referenced_columns"]),
                )
            )
        return foreign_keys

    def _fetch_indexes(self, connection: Any, schema: str) -> dict[str, list[IndexSchema]]:
        query = """
            select
                table_name,
                index_name,
                bool_or(is_unique) as is_unique,
                max(index_method) as index_method,
                max(predicate_expression) as predicate_expression,
                max(index_expression) as index_expression,
                array_agg(column_name order by ordinal_position) filter (where column_name is not null) as column_names
            from (
                select
                    tab.relname as table_name,
                    idx.relname as index_name,
                    ix.indisunique as is_unique,
                    am.amname as index_method,
                    pg_get_expr(ix.indpred, ix.indrelid) as predicate_expression,
                    pg_get_expr(ix.indexprs, ix.indrelid) as index_expression,
                    att.attname as column_name,
                    keys.ordinality as ordinal_position,
                    ix.indisprimary as is_primary
                from pg_class tab
                join pg_namespace ns on ns.oid = tab.relnamespace
                join pg_index ix on ix.indrelid = tab.oid
                join pg_class idx on idx.oid = ix.indexrelid
                join pg_am am on am.oid = idx.relam
                join unnest(ix.indkey) with ordinality as keys(attnum, ordinality) on true
                left join pg_attribute att on att.attrelid = tab.oid and att.attnum = keys.attnum
                where ns.nspname = %s
                  and tab.relkind = 'r'
                  and not ix.indisprimary
            ) indexed_columns
            group by table_name, index_name
            order by table_name, index_name
        """
        indexes: dict[str, list[IndexSchema]] = defaultdict(list)
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                columns = tuple(str(column) for column in (row["column_names"] or ()))
                manual_review_reason = _postgres_index_manual_review_reason(
                    columns=columns,
                    method=str(row["index_method"]),
                    predicate=row["predicate_expression"],
                    expression=row["index_expression"],
                )
                indexes[str(row["table_name"])].append(
                    IndexSchema(
                        name=str(row["index_name"]),
                        columns=columns,
                        unique=bool(row["is_unique"]),
                        method=str(row["index_method"]),
                        auto_create_candidate=manual_review_reason is None,
                        manual_review_reason=manual_review_reason,
                    )
                )
        return indexes

    def _fetch_non_table_objects(self, connection: Any, schema: str) -> list[SchemaObjectSummary]:
        objects: list[SchemaObjectSummary] = []
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select table_schema, table_name
                from information_schema.views
                where table_schema = %s
                order by table_name
                """,
                (schema,),
            )
            objects.extend(
                SchemaObjectSummary(kind=SchemaObjectKind.VIEW, schema=str(row["table_schema"]), name=str(row["table_name"]))
                for row in cursor.fetchall()
            )
            cursor.execute(
                """
                select routine_schema, routine_name, routine_type
                from information_schema.routines
                where routine_schema = %s
                order by routine_type, routine_name
                """,
                (schema,),
            )
            for row in cursor.fetchall():
                routine_type = str(row["routine_type"]).lower()
                kind = SchemaObjectKind.PROCEDURE if routine_type == "procedure" else SchemaObjectKind.FUNCTION
                objects.append(SchemaObjectSummary(kind=kind, schema=str(row["routine_schema"]), name=str(row["routine_name"])))
            cursor.execute(
                """
                select trigger_schema, trigger_name, event_object_schema, event_object_table
                from information_schema.triggers
                where trigger_schema = %s
                order by trigger_name
                """,
                (schema,),
            )
            objects.extend(
                SchemaObjectSummary(
                    kind=SchemaObjectKind.TRIGGER,
                    schema=str(row["trigger_schema"]),
                    name=str(row["trigger_name"]),
                    parent_table=TableRef(schema=str(row["event_object_schema"]), name=str(row["event_object_table"])),
                )
                for row in cursor.fetchall()
            )
        return objects


def dict_row_factory() -> Any:
    from psycopg.rows import dict_row

    return dict_row


def _postgres_index_manual_review_reason(
    *,
    columns: tuple[str, ...],
    method: str,
    predicate: object,
    expression: object,
) -> str | None:
    if not columns:
        return "Expression index requires manual conversion."
    if str(method).lower() != "btree":
        return f"PostgreSQL {method} index method requires target DBMS review."
    if predicate is not None:
        return "Partial index predicate requires manual conversion."
    if expression is not None:
        return "Expression index requires manual conversion."
    return None


def _format_source_type(row: dict[str, Any]) -> str:
    data_type = str(row["data_type"])
    if data_type in {"character varying", "character"} and row["character_maximum_length"] is not None:
        return f"{data_type}({row['character_maximum_length']})"
    if data_type == "numeric" and row["numeric_precision"] is not None and row["numeric_scale"] is not None:
        return f"numeric({row['numeric_precision']},{row['numeric_scale']})"
    if data_type in {
        "timestamp with time zone",
        "timestamp without time zone",
        "time with time zone",
        "time without time zone",
    } and row.get("datetime_precision") is not None:
        return f"{data_type}({row['datetime_precision']})"
    if data_type == "USER-DEFINED":
        return str(row["udt_name"])
    return data_type


def _quote_postgres_identifier(identifier: str) -> str:
    return quote_postgres_identifier(identifier)


def quote_postgres_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _qualified_postgres_table_name(table_schema: TableSchema) -> str:
    return _qualified_postgres_table_ref(table_schema.ref)


def _qualified_postgres_table_ref(table: TableRef) -> str:
    return ".".join(
        [
            _quote_postgres_identifier(table.schema),
            _quote_postgres_identifier(table.name),
        ]
    )


def _postgres_order_by_clause(columns: tuple[str, ...], *, position: SamplePosition = SamplePosition.FIRST) -> str:
    if not columns:
        return ""
    direction = " desc" if position is SamplePosition.LAST else ""
    column_sql = ", ".join(f"{_quote_postgres_identifier(column)}{direction}" for column in columns)
    return f" order by {column_sql}"


def _postgres_keyset_where_clause(cursor: ReadCursor) -> tuple[str, tuple[Any, ...]]:
    if cursor.strategy is not CursorStrategy.KEYSET or not cursor.last_key_values:
        return "", ()
    columns = ", ".join(_quote_postgres_identifier(column) for column in cursor.key_columns)
    placeholders = ", ".join(["%s"] * len(cursor.last_key_values))
    return f" where ({columns}) > ({placeholders})", cursor.last_key_values


def _next_read_cursor(start_cursor: ReadCursor, rows: tuple[RowData, ...], next_offset: int) -> ReadCursor:
    if start_cursor.strategy is CursorStrategy.KEYSET:
        last_row = rows[-1] if rows else {}
        return ReadCursor.keyset_cursor(
            key_columns=start_cursor.key_columns,
            last_key_values=tuple(last_row.get(column) for column in start_cursor.key_columns),
            offset=next_offset,
        )
    return ReadCursor.offset_cursor(next_offset)


def _postgres_sync_temp_table_name(table_schema: TableSchema) -> str:
    return _quote_postgres_identifier(f"db_migrator_sync_{table_schema.ref.name}")


def _postgres_row_values(table_schema: TableSchema, row: RowData, columns: tuple[str, ...]) -> tuple[Any, ...]:
    column_by_name = {column.name: column for column in table_schema.columns}
    return tuple(_postgres_cell_value(row.get(column), column_by_name[column]) for column in columns)


def _postgres_cell_value(value: object, column: ColumnSchema) -> object:
    if value is None:
        return None
    if column.common_type.kind is CommonTypeKind.JSON:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise PostgresAdapterError("psycopg JSON adapter is unavailable. Install project dependencies first.") from exc
        json_value = json.loads(value) if isinstance(value, str) else value
        return Jsonb(json_value)
    if column.common_type.kind is CommonTypeKind.BOOLEAN and isinstance(value, int):
        return bool(value)
    if column.common_type.kind is CommonTypeKind.UUID and isinstance(value, str):
        return UUID(value)
    if column.common_type.kind is CommonTypeKind.BINARY and isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _watermark_where_clause(watermark: WatermarkConfig) -> tuple[str, tuple[str, ...]]:
    clauses = []
    params = []
    column = _quote_postgres_identifier(watermark.column)
    if watermark.start_value is not None:
        clauses.append(f"{column} >= %s")
        params.append(watermark.start_value)
    if watermark.end_value is not None:
        clauses.append(f"{column} < %s")
        params.append(watermark.end_value)
    if not clauses:
        return "", ()
    return "where " + " and ".join(clauses), tuple(params)


def _postgres_upsert_update_sql(update_columns: tuple[str, ...], keys: tuple[str, ...]) -> str:
    columns = update_columns or keys[:1]
    return ", ".join(
        f"{_quote_postgres_identifier(column)} = EXCLUDED.{_quote_postgres_identifier(column)}"
        for column in columns
    )


def _unique_warning_messages(warnings: Iterable[str]) -> tuple[str, ...]:
    unique_warnings: list[str] = []
    for warning in warnings:
        if isinstance(warning, str) and warning not in unique_warnings:
            unique_warnings.append(warning)
    return tuple(unique_warnings)


def _compact_sql(sql: str) -> str:
    compact = " ".join(sql.split())
    if len(compact) <= 500:
        return compact
    return compact[:497] + "..."


def _first_row_value(row: Any) -> Any:
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def _split_postgres_ddl_statements(ddl: str) -> tuple[str, ...]:
    return tuple(statement.strip() for statement in ddl.split(";") if statement.strip())
