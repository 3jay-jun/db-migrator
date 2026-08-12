from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from db_migrator.config.models import Dbms, MigrationConfig
from db_migrator.core.dml_migration import stable_order_columns
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.models import ReadCursor, RowBatch, RowData, TableRef, TableSchema


class ManualSourceReader(Protocol):
    def read_rows(
        self,
        table: TableRef,
        columns: tuple[str, ...],
        cursor: ReadCursor | None,
        batch_size: int,
        order_by: tuple[str, ...],
    ) -> Iterator[RowBatch]:
        """Yield source rows for manual export."""


@dataclass(frozen=True)
class ManualTableExport:
    schema: str
    table: str
    rows_exported: int
    csv_file: Path


@dataclass(frozen=True)
class ManualMigrationExport:
    tables: tuple[ManualTableExport, ...]
    ddl_file: Path
    load_sql_file: Path

    @property
    def rows_exported(self) -> int:
        return sum(table.rows_exported for table in self.tables)


def export_manual_migration_files(
    *,
    source: ManualSourceReader,
    source_tables: tuple[TableSchema, ...],
    target_tables: tuple[TableSchema, ...],
    target_dbms: Dbms,
    target_database: str,
    migration_config: MigrationConfig,
    ddl_sql: str,
    output_dir: Path,
    column_plans: dict[TableRef, ColumnPlan] | None = None,
) -> ManualMigrationExport:
    manual_dir = output_dir / "manual-migration"
    data_dir = manual_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    ddl_file = manual_dir / "ddl.sql"
    ddl_file.write_text(ddl_sql, encoding="utf-8")

    exports: list[ManualTableExport] = []
    load_statements: list[str] = []
    for source_table, target_table in zip(source_tables, target_tables, strict=True):
        column_plan = (column_plans or {}).get(source_table.ref)
        read_columns = column_plan.read_columns if column_plan is not None and column_plan.read_columns else _writable_columns(source_table)
        write_columns = column_plan.write_columns if column_plan is not None else read_columns
        csv_file = data_dir / f"{target_table.ref.schema}.{target_table.ref.name}.csv"
        rows_exported = _write_table_csv(
            source=source,
            table=source_table,
            read_columns=read_columns,
            write_columns=write_columns,
            csv_file=csv_file,
            migration_config=migration_config,
            column_plan=column_plan,
        )
        exports.append(
            ManualTableExport(
                schema=target_table.ref.schema,
                table=target_table.ref.name,
                rows_exported=rows_exported,
                csv_file=csv_file,
            )
        )
        load_statements.append(_load_statement(target_table, write_columns, csv_file, target_dbms, target_database))

    load_sql_file = manual_dir / "load-data.sql"
    load_sql_file.write_text("\n\n".join(load_statements) + ("\n" if load_statements else ""), encoding="utf-8")
    return ManualMigrationExport(tables=tuple(exports), ddl_file=ddl_file, load_sql_file=load_sql_file)


def _write_table_csv(
    *,
    source: ManualSourceReader,
    table: TableSchema,
    read_columns: tuple[str, ...],
    write_columns: tuple[str, ...],
    csv_file: Path,
    migration_config: MigrationConfig,
    column_plan: ColumnPlan | None,
) -> int:
    row_count = 0
    order_by = stable_order_columns(table, read_columns)
    with csv_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=write_columns, extrasaction="ignore")
        writer.writeheader()
        for batch in source.read_rows(table.ref, read_columns, None, migration_config.batch_size, order_by):
            rows = column_plan.transform_rows(batch.rows) if column_plan is not None else batch.rows
            for row in rows:
                writer.writerow(_csv_row(row, write_columns))
            row_count += batch.row_count
    return row_count


def _csv_row(row: RowData, columns: tuple[str, ...]) -> dict[str, object | None]:
    return {column: row.get(column) for column in columns}


def _writable_columns(table: TableSchema) -> tuple[str, ...]:
    return tuple(column.name for column in table.columns if not column.is_generated)


def _load_statement(table: TableSchema, columns: tuple[str, ...], csv_file: Path, target_dbms: Dbms, target_database: str) -> str:
    if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        column_sql = ", ".join(_quote_mysql_identifier(column) for column in columns)
        return (
            f"LOAD DATA LOCAL INFILE '{_sql_path(csv_file)}'\n"
            f"INTO TABLE {_mysql_table_name(table, target_database)}\n"
            "CHARACTER SET utf8mb4\n"
            "FIELDS TERMINATED BY ',' OPTIONALLY ENCLOSED BY '\"'\n"
            "LINES TERMINATED BY '\\n'\n"
            "IGNORE 1 LINES\n"
            f"({column_sql});"
        )
    if target_dbms is Dbms.POSTGRESQL:
        column_sql = ", ".join(_quote_postgres_identifier(column) for column in columns)
        return f"\\copy {_postgres_table_name(table)} ({column_sql}) FROM '{_sql_path(csv_file)}' WITH (FORMAT csv, HEADER true);"
    raise ValueError(f"Unsupported target DBMS for manual migration export: {target_dbms.value}")


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace("'", "''")


def _mysql_table_name(table: TableSchema, target_database: str) -> str:
    return f"{_quote_mysql_identifier(target_database)}.{_quote_mysql_identifier(table.ref.name)}"


def _postgres_table_name(table: TableSchema) -> str:
    return f"{_quote_postgres_identifier(table.ref.schema)}.{_quote_postgres_identifier(table.ref.name)}"


def _quote_mysql_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def _quote_postgres_identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) + chr(34))}"'
