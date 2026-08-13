from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Callable, Iterable, Protocol
from uuid import UUID

from db_migrator.adapters.base import DdlResult, ExecutionResult
from db_migrator.adapters.error_detail import connection_test_failure_message, safe_error_detail
from db_migrator.config.models import SourceConfig, TargetConfig, WatermarkConfig
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
from db_migrator.schema.type_mapping import common_type_to_mysql, mysql_type_to_common


_MYSQL_RESERVED_WORDS = {
    "add",
    "alter",
    "and",
    "as",
    "by",
    "create",
    "delete",
    "from",
    "group",
    "index",
    "insert",
    "key",
    "order",
    "primary",
    "select",
    "table",
    "update",
    "where",
}


class MySqlAdapterError(RuntimeError):
    pass


class SourceTypeMapper(Protocol):
    def __call__(self, source_type: str, *, is_generated: bool = False) -> CommonType:
        """Map a source DBMS type into the common schema type."""


class MySqlSourceAdapter:
    def __init__(self, config: SourceConfig, source_type_mapper: SourceTypeMapper | None = None) -> None:
        self._config = config
        self._source_type_mapper = source_type_mapper or mysql_type_to_common

    def test_connection(self) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("select 1 as ok")
                    return int(cursor.fetchone()["ok"]) == 1
        except Exception as exc:
            raise MySqlAdapterError(connection_test_failure_message("MySQL/MariaDB", self._config, exc)) from exc

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
                    raise MySqlAdapterError(
                        f"MySQL/MariaDB schema metadata query failed for schema '{schema}'. "
                        f"Check source.schema and metadata permissions. detail={safe_error_detail(exc)}"
                    ) from exc
        except MySqlAdapterError:
            raise
        except Exception as exc:
            raise MySqlAdapterError(
                "MySQL/MariaDB connection failed before schema scan. "
                f"host={self._config.host} port={self._config.port} "
                f"database={self._config.database} user={self._config.user} schema={schema}. "
                f"detail={safe_error_detail(exc)}"
            ) from exc

        grouped_columns: dict[str, list[ColumnSchema]] = defaultdict(list)
        for row in columns:
            table_name = str(row["table_name"])
            source_type = _format_mysql_source_type(row)
            extra = str(row["extra"]).upper()
            is_generated = "GENERATED" in extra
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
                    auto_increment="AUTO_INCREMENT" in extra,
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
            raise MySqlAdapterError("batch_size must be greater than zero.")

        start_cursor = cursor or ReadCursor.offset_cursor()
        start_offset = start_cursor.offset
        column_sql = ", ".join(quote_mysql_identifier(column) for column in columns)
        table_sql = qualify_mysql_table_ref(table)
        order_sql = _mysql_order_by_clause(order_by)
        where_sql, params = _mysql_keyset_where_clause(start_cursor)
        offset_sql = "" if start_cursor.strategy is CursorStrategy.KEYSET else " LIMIT %s, 18446744073709551615"
        query = f"SELECT {column_sql} FROM {table_sql}{where_sql}{order_sql}{offset_sql}"
        execute_params = params if start_cursor.strategy is CursorStrategy.KEYSET else (*params, start_offset)

        try:
            from pymysql.cursors import SSDictCursor

            with self._connect() as connection:
                batch_number = 0
                current_offset = start_offset
                with connection.cursor(SSDictCursor) as db_cursor:
                    db_cursor.execute(query, execute_params)
                    while rows := tuple(dict(row) for row in db_cursor.fetchmany(batch_size)):
                        batch_number += 1
                        next_cursor = _next_read_cursor(start_cursor, rows, current_offset + len(rows))
                        yield RowBatch(
                            table=table,
                            rows=rows,
                            batch_number=batch_number,
                            start_offset=current_offset,
                            next_cursor=next_cursor,
                            start_cursor=start_cursor,
                        )
                        current_offset += len(rows)
                        start_cursor = next_cursor
        except Exception as exc:
            raise MySqlAdapterError(f"MySQL/MariaDB row streaming failed for table: {table.name}") from exc

    def count_rows(self, table: TableRef) -> int:
        table_sql = qualify_mysql_table_ref(table)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) AS row_count FROM {table_sql}")
                    return int(cursor.fetchone()["row_count"])
        except Exception as exc:
            raise MySqlAdapterError(f"MySQL/MariaDB row count failed for table: {table.name}") from exc

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
        column_sql = ", ".join(quote_mysql_identifier(column) for column in columns)
        table_sql = qualify_mysql_table_ref(table)
        order_sql = _mysql_order_by_clause(order_by, position=position)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {column_sql} FROM {table_sql}{order_sql} LIMIT %s", (sample_size,))
                    return tuple(dict(row) for row in cursor.fetchall())
        except Exception as exc:
            raise MySqlAdapterError(f"MySQL/MariaDB sample rows failed for table: {table.name}") from exc

    def read_incremental_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        watermark: WatermarkConfig,
        batch_size: int,
    ) -> Iterator[RowBatch]:
        if batch_size <= 0:
            raise MySqlAdapterError("batch_size must be greater than zero.")

        column_sql = ", ".join(quote_mysql_identifier(column) for column in columns)
        table_sql = qualify_mysql_table_ref(table)
        where_sql, params = _mysql_watermark_where_clause(watermark)
        query = f"SELECT {column_sql} FROM {table_sql} {where_sql} ORDER BY {quote_mysql_identifier(watermark.column)}"

        try:
            from pymysql.cursors import SSDictCursor

            with self._connect() as connection:
                batch_number = 0
                current_offset = 0
                with connection.cursor(SSDictCursor) as cursor:
                    cursor.execute(query, params)
                    while rows := tuple(dict(row) for row in cursor.fetchmany(batch_size)):
                        batch_number += 1
                        yield RowBatch(
                            table=table,
                            rows=rows,
                            batch_number=batch_number,
                            start_offset=current_offset,
                            next_cursor=ReadCursor(offset=current_offset + len(rows)),
                        )
                        current_offset += len(rows)
        except Exception as exc:
            raise MySqlAdapterError(f"MySQL/MariaDB incremental row streaming failed for table: {table.name}") from exc

    def _connect(self):
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise MySqlAdapterError("pymysql is not installed. Install project dependencies first.") from exc

        return pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password or "",
            charset="utf8mb4",
            cursorclass=DictCursor,
        )

    def _fetch_columns(self, connection, schema: str) -> list[dict]:
        query = """
            select
                table_name,
                column_name,
                data_type,
                column_type,
                character_maximum_length,
                numeric_precision,
                numeric_scale,
                datetime_precision,
                is_nullable,
                column_default,
                extra,
                generation_expression,
                ordinal_position
            from information_schema.columns
            where table_schema = %s
            order by table_name, ordinal_position
        """
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            return list(cursor.fetchall())

    def _fetch_primary_keys(self, connection, schema: str) -> dict[str, list[str]]:
        query = """
            select
                table_name,
                column_name
            from information_schema.key_column_usage
            where table_schema = %s
              and constraint_name = 'PRIMARY'
            order by table_name, ordinal_position
        """
        primary_keys: dict[str, list[str]] = defaultdict(list)
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                primary_keys[str(row["table_name"])].append(str(row["column_name"]))
        return primary_keys

    def _fetch_indexes(self, connection, schema: str) -> dict[str, list[IndexSchema]]:
        query = """
            select
                table_name,
                index_name,
                min(non_unique) as non_unique,
                max(index_type) as index_type,
                group_concat(column_name order by seq_in_index separator '\x1f') as column_names
            from information_schema.statistics
            where table_schema = %s
              and index_name <> 'PRIMARY'
            group by table_name, index_name
            order by table_name, index_name
        """
        indexes: dict[str, list[IndexSchema]] = defaultdict(list)
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                raw_columns = str(row["column_names"] or "")
                columns = tuple(column for column in raw_columns.split("\x1f") if column)
                index_type = str(row["index_type"] or "")
                manual_review_reason = _mysql_index_manual_review_reason(columns=columns, index_type=index_type)
                indexes[str(row["table_name"])].append(
                    IndexSchema(
                        name=str(row["index_name"]),
                        columns=columns,
                        unique=int(row["non_unique"]) == 0,
                        method=index_type,
                        auto_create_candidate=manual_review_reason is None,
                        manual_review_reason=manual_review_reason,
                    )
                )
        return indexes

    def _fetch_foreign_keys(self, connection, schema: str) -> dict[str, list[ForeignKeySchema]]:
        query = """
            select
                constraint_name,
                table_name,
                column_name,
                referenced_table_schema,
                referenced_table_name,
                referenced_column_name,
                ordinal_position
            from information_schema.key_column_usage
            where table_schema = %s
              and referenced_table_name is not null
            order by table_name, constraint_name, ordinal_position
        """
        grouped: dict[tuple[str, str, str, str], dict[str, list[str]]] = {}
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            for row in cursor.fetchall():
                key = (
                    str(row["table_name"]),
                    str(row["constraint_name"]),
                    str(row["referenced_table_schema"]),
                    str(row["referenced_table_name"]),
                )
                grouped.setdefault(key, {"columns": [], "referenced_columns": []})
                grouped[key]["columns"].append(str(row["column_name"]))
                grouped[key]["referenced_columns"].append(str(row["referenced_column_name"]))

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

    def _fetch_estimated_rows(self, connection, schema: str) -> dict[str, int]:
        query = """
            select table_name, table_rows
            from information_schema.tables
            where table_schema = %s
              and table_type = 'BASE TABLE'
        """
        with connection.cursor() as cursor:
            cursor.execute(query, (schema,))
            return {str(row["table_name"]): int(row["table_rows"] or 0) for row in cursor.fetchall()}

    def _fetch_non_table_objects(self, connection, schema: str) -> list[SchemaObjectSummary]:
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


class MySqlTargetAdapter:
    def __init__(self, config: TargetConfig) -> None:
        self._config = config
        self._dml_connection = None

    def test_connection(self) -> bool:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("select 1")
                    return cursor.fetchone()[0] == 1
        except Exception as exc:
            raise MySqlAdapterError(connection_test_failure_message("MySQL/MariaDB", self._config, exc)) from exc

    def table_exists(self, table_schema: TableSchema) -> bool:
        query = """
            select count(*)
            from information_schema.tables
            where table_schema = %s
              and table_name = %s
        """
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(query, (self._config.database, table_schema.ref.name))
                    return int(cursor.fetchone()[0]) > 0
        except Exception as exc:
            raise MySqlAdapterError(f"Failed to check target table existence: {table_schema.ref.name}") from exc

    def execute_ddl(self, ddl: str) -> ExecutionResult:
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(ddl)
                connection.commit()
            return ExecutionResult(success=True, message="DDL executed.")
        except Exception as exc:
            raise MySqlAdapterError(
                "Failed to execute target DDL. "
                f"target_database={self._config.database} ddl={_compact_sql(ddl)} detail={safe_error_detail(exc)}"
            ) from exc

    def truncate_table(self, table_schema: TableSchema) -> ExecutionResult:
        ddl = f"TRUNCATE TABLE {self._qualified_table_name(table_schema)}"
        return self.execute_ddl(ddl)

    def drop_table(self, table_schema: TableSchema) -> ExecutionResult:
        ddl = f"DROP TABLE {self._qualified_table_name(table_schema)}"
        return self.execute_ddl(ddl)

    def write_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...]) -> WriteResult:
        if not rows:
            return WriteResult(success=True, rows_written=0, message="No rows to write.")

        writable_columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        placeholders = ", ".join(["%s"] * len(writable_columns))
        column_sql = ", ".join(quote_mysql_identifier(column) for column in writable_columns)
        table_sql = self._qualified_table_name(table_schema)
        sql = f"INSERT INTO {table_sql} ({column_sql}) VALUES ({placeholders})"
        values = [_mysql_row_values(table_schema, row, writable_columns) for row in rows]

        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(sql, values)
            return WriteResult(success=True, rows_written=len(rows), message="Batch written.")
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to write target batch for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> WriteResult:
        if not keys:
            return WriteResult(success=False, rows_written=0, message="Upsert requires primary or unique key columns.")
        if not rows:
            return WriteResult(success=True, rows_written=0, message="No rows to upsert.")

        writable_columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        placeholders = ", ".join(["%s"] * len(writable_columns))
        column_sql = ", ".join(quote_mysql_identifier(column) for column in writable_columns)
        table_sql = self._qualified_table_name(table_schema)
        update_columns = tuple(column for column in writable_columns if column not in keys)
        update_sql = _mysql_upsert_update_sql(update_columns, keys)
        sql = f"INSERT INTO {table_sql} ({column_sql}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_sql}"
        values = [_mysql_row_values(table_schema, row, writable_columns) for row in rows]

        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(sql, values)
            return WriteResult(success=True, rows_written=len(rows), message="Batch upserted.")
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to upsert target batch for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def fetch_rows_by_keys(self, table_schema: TableSchema, keys: tuple[str, ...], rows: tuple[RowData, ...]) -> dict[tuple[object, ...], RowData]:
        if not rows:
            return {}
        writable_columns = tuple(column.name for column in table_schema.columns if not column.is_generated)
        table_sql = self._qualified_table_name(table_schema)
        column_sql = ", ".join(quote_mysql_identifier(column) for column in writable_columns)
        where_sql, params = _mysql_key_lookup_predicate(keys, rows)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {column_sql} FROM {table_sql} WHERE {where_sql}", params)
                    fetched_rows = cursor.fetchall()
            return {_result_row_key(_result_row_dict(row, writable_columns), keys): _result_row_dict(row, writable_columns) for row in fetched_rows}
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to fetch target rows for sync comparison: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def count_rows(self, table: TableRef) -> int:
        table_sql = self._qualified_table_ref(table)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_sql}")
                    return int(cursor.fetchone()[0])
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to count target rows for table: {table.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        temp_table = _mysql_sync_temp_table_name(table_schema)
        key_sql = ", ".join(quote_mysql_identifier(key) for key in keys)
        table_sql = self._qualified_table_name(table_schema)
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TEMPORARY TABLE IF EXISTS {temp_table}")
                cursor.execute(f"CREATE TEMPORARY TABLE {temp_table} AS SELECT {key_sql} FROM {table_sql} WHERE 1 = 0")
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to prepare target sync keys for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[RowData, ...], keys: tuple[str, ...]) -> None:
        if not rows:
            return
        temp_table = _mysql_sync_temp_table_name(table_schema)
        key_sql = ", ".join(quote_mysql_identifier(key) for key in keys)
        placeholders = ", ".join(["%s"] * len(keys))
        values = [tuple(row.get(key) for key in keys) for row in rows]
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.executemany(f"INSERT INTO {temp_table} ({key_sql}) VALUES ({placeholders})", values)
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to record target sync keys for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        temp_table = _mysql_sync_temp_table_name(table_schema)
        table_sql = self._qualified_table_name(table_schema)
        match_sql = " AND ".join(
            f"{table_sql}.{quote_mysql_identifier(key)} = {temp_table}.{quote_mysql_identifier(key)}"
            for key in keys
        )
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {table_sql} WHERE NOT EXISTS (SELECT 1 FROM {temp_table} WHERE {match_sql})")
                return int(cursor.rowcount or 0)
        except Exception as exc:
            raise MySqlAdapterError(
                "Failed to delete target rows missing from source for table: "
                f"{table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        temp_table = _mysql_sync_temp_table_name(table_schema)
        try:
            connection = self._ensure_dml_connection()
            with connection.cursor() as cursor:
                cursor.execute(f"DROP TEMPORARY TABLE IF EXISTS {temp_table}")
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to clean up target sync keys for table: {table_schema.ref.name}. detail={safe_error_detail(exc)}"
            ) from exc

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
        column_sql = ", ".join(quote_mysql_identifier(column) for column in columns)
        table_sql = self._qualified_table_ref(table)
        try:
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    order_sql = _mysql_order_by_clause(order_by, position=position)
                    cursor.execute(f"SELECT {column_sql} FROM {table_sql}{order_sql} LIMIT %s", (sample_size,))
                    rows = cursor.fetchall()
            return tuple(dict(zip(columns, row, strict=True)) for row in rows)
        except Exception as exc:
            raise MySqlAdapterError(
                f"Failed to sample target rows for table: {table.name}. detail={safe_error_detail(exc)}"
            ) from exc

    def commit(self) -> None:
        if self._dml_connection is not None:
            self._dml_connection.commit()

    def close(self) -> None:
        if self._dml_connection is not None:
            self._dml_connection.close()
            self._dml_connection = None

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:
            raise MySqlAdapterError("pymysql is not installed. Install project dependencies first.") from exc

        return pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            database=self._config.database,
            user=self._config.user,
            password=self._config.password or "",
            charset="utf8mb4",
        )

    def _ensure_dml_connection(self):
        if self._dml_connection is None:
            self._dml_connection = self._connect()
        return self._dml_connection

    def _qualified_table_name(self, table_schema: TableSchema) -> str:
        return qualify_mysql_table_name(table_schema, target_database=self._config.database)

    def _qualified_table_ref(self, table: TableRef) -> str:
        return qualify_mysql_table_ref(table, target_database=self._config.database)


class MySqlDdlGenerator:
    def __init__(
        self,
        target_database: str | None = None,
        target_type_mapper: Callable[[CommonType], str] | None = None,
    ) -> None:
        self._target_database = target_database
        self._target_type_mapper = target_type_mapper or common_type_to_mysql

    def generate_create_table(self, table_schema: TableSchema) -> DdlResult:
        column_lines = [self._column_definition(column) for column in table_schema.columns]

        if table_schema.primary_key is not None and table_schema.primary_key.columns:
            columns = ", ".join(quote_mysql_identifier(column) for column in table_schema.primary_key.columns)
            column_lines.append(f"  PRIMARY KEY ({columns})")

        table_name = qualify_mysql_table_name(table_schema, target_database=self._target_database)
        ddl_body = ",\n".join(column_lines)
        ddl = f"CREATE TABLE {table_name} (\n{ddl_body}\n);"
        warnings = _unique_warning_messages(
            warning.message
            for column in table_schema.columns
            for warning in column.common_type.warnings + column.warnings
        )
        return DdlResult(table_name=table_name, ddl=ddl, warnings=warnings)

    def _column_definition(self, column: ColumnSchema) -> str:
        column_type = self._target_type_mapper(column.common_type)
        null_sql = "NULL" if column.nullable else "NOT NULL"
        auto_increment_sql = " AUTO_INCREMENT" if column.auto_increment else ""
        return f"  {quote_mysql_identifier(column.name)} {column_type} {null_sql}{auto_increment_sql}"


def qualify_mysql_table_name(table_schema: TableSchema, *, target_database: str | None = None) -> str:
    return qualify_mysql_table_ref(table_schema.ref, target_database=target_database)


def qualify_mysql_table_ref(table: TableRef, *, target_database: str | None = None) -> str:
    database_name = target_database or table.schema
    return ".".join(
        [
            quote_mysql_identifier(database_name),
            quote_mysql_identifier(table.name),
        ]
    )


def quote_mysql_identifier(identifier: str) -> str:
    escaped_identifier = identifier.replace("`", "``")
    return f"`{escaped_identifier}`"


def _mysql_index_manual_review_reason(*, columns: tuple[str, ...], index_type: str) -> str | None:
    if not columns:
        return "Expression index requires manual conversion."
    if index_type and index_type.upper() not in {"BTREE"}:
        return f"MySQL/MariaDB {index_type} index type requires manual review."
    return None


def _mysql_order_by_clause(columns: tuple[str, ...], *, position: SamplePosition = SamplePosition.FIRST) -> str:
    if not columns:
        return ""
    direction = " DESC" if position is SamplePosition.LAST else ""
    column_sql = ", ".join(f"{quote_mysql_identifier(column)}{direction}" for column in columns)
    return f" ORDER BY {column_sql}"


def _mysql_keyset_where_clause(cursor: ReadCursor) -> tuple[str, tuple]:
    if cursor.strategy is not CursorStrategy.KEYSET or not cursor.last_key_values:
        return "", ()
    columns = ", ".join(quote_mysql_identifier(column) for column in cursor.key_columns)
    placeholders = ", ".join(["%s"] * len(cursor.last_key_values))
    return f" WHERE ({columns}) > ({placeholders})", cursor.last_key_values


def _next_read_cursor(start_cursor: ReadCursor, rows: tuple[RowData, ...], next_offset: int) -> ReadCursor:
    if start_cursor.strategy is CursorStrategy.KEYSET:
        last_row = rows[-1] if rows else {}
        return ReadCursor.keyset_cursor(
            key_columns=start_cursor.key_columns,
            last_key_values=tuple(last_row.get(column) for column in start_cursor.key_columns),
            offset=next_offset,
        )
    return ReadCursor.offset_cursor(next_offset)


def _mysql_sync_temp_table_name(table_schema: TableSchema) -> str:
    return quote_mysql_identifier(f"db_migrator_sync_{table_schema.ref.name}")


def _mysql_row_values(table_schema: TableSchema, row: RowData, columns: tuple[str, ...]) -> tuple:
    column_by_name = {column.name: column for column in table_schema.columns}
    return tuple(_mysql_cell_value(row.get(column), column_by_name[column]) for column in columns)


def _mysql_cell_value(value: object, column: ColumnSchema) -> object:
    if value is None:
        return None
    if column.common_type.kind is CommonTypeKind.JSON:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if isinstance(value, (dict, list)) else value
    if column.common_type.kind is CommonTypeKind.UUID:
        return str(value) if isinstance(value, UUID) else value
    if column.common_type.kind is CommonTypeKind.BINARY:
        return bytes(value) if isinstance(value, memoryview) else value
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _format_mysql_source_type(row: dict) -> str:
    column_type = row.get("column_type")
    if column_type:
        return str(column_type)
    data_type = str(row["data_type"])
    if data_type in {"varchar", "char"} and row["character_maximum_length"] is not None:
        return f"{data_type}({row['character_maximum_length']})"
    if data_type in {"decimal", "numeric"} and row["numeric_precision"] is not None and row["numeric_scale"] is not None:
        return f"{data_type}({row['numeric_precision']},{row['numeric_scale']})"
    if data_type in {"datetime", "timestamp", "time"} and row.get("datetime_precision") is not None:
        return f"{data_type}({row['datetime_precision']})"
    return data_type


def _mysql_watermark_where_clause(watermark: WatermarkConfig) -> tuple[str, tuple[str, ...]]:
    clauses = []
    params = []
    column = quote_mysql_identifier(watermark.column)
    if watermark.start_value is not None:
        clauses.append(f"{column} >= %s")
        params.append(watermark.start_value)
    if watermark.end_value is not None:
        clauses.append(f"{column} < %s")
        params.append(watermark.end_value)
    if not clauses:
        return "", ()
    return "WHERE " + " AND ".join(clauses), tuple(params)


def needs_mysql_identifier_warning(identifier: str) -> bool:
    normalized = identifier.lower()
    return normalized in _MYSQL_RESERVED_WORDS or normalized != identifier or not identifier.isidentifier()


def _mysql_upsert_update_sql(update_columns: tuple[str, ...], keys: tuple[str, ...]) -> str:
    columns = update_columns or keys[:1]
    return ", ".join(
        f"{quote_mysql_identifier(column)} = VALUES({quote_mysql_identifier(column)})"
        for column in columns
    )


def _mysql_key_lookup_predicate(keys: tuple[str, ...], rows: tuple[RowData, ...]) -> tuple[str, tuple[object, ...]]:
    row_predicates: list[str] = []
    params: list[object] = []
    for row in rows:
        row_predicates.append("(" + " AND ".join(f"{quote_mysql_identifier(key)} = %s" for key in keys) + ")")
        params.extend(row.get(key) for key in keys)
    return " OR ".join(row_predicates), tuple(params)


def _result_row_key(row: RowData, keys: tuple[str, ...]) -> tuple[object, ...]:
    return tuple(row.get(key) for key in keys)


def _result_row_dict(row: object, columns: tuple[str, ...]) -> RowData:
    if isinstance(row, dict):
        return row
    return dict(zip(columns, row))


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
