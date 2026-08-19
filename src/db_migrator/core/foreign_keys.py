from __future__ import annotations

from dataclasses import dataclass

from db_migrator.adapters.mysql import ExecutionResult
from db_migrator.config.models import Dbms
from db_migrator.schema.dialect import qualified_table_name, quote_identifier
from db_migrator.schema.models import ForeignKeySchema, SchemaSnapshot, TableSchema


@dataclass(frozen=True)
class ForeignKeyDdl:
    table: str
    constraint_name: str
    ddl: str


@dataclass(frozen=True)
class ForeignKeyExecutionResult:
    table: str
    constraint_name: str
    success: bool
    message: str


class ForeignKeyExecutor:
    def execute_ddl(self, ddl: str) -> ExecutionResult:
        """Execute one FK DDL statement."""


def generate_mysql_foreign_key_ddls(snapshot: SchemaSnapshot) -> tuple[ForeignKeyDdl, ...]:
    return tuple(
        _foreign_key_ddl(table, foreign_key, target_dbms=Dbms.MYSQL)
        for table in snapshot.tables
        for foreign_key in table.foreign_keys
    )


def generate_postgres_foreign_key_ddls(snapshot: SchemaSnapshot) -> tuple[ForeignKeyDdl, ...]:
    return tuple(
        _foreign_key_ddl(table, foreign_key, target_dbms=Dbms.POSTGRESQL)
        for table in snapshot.tables
        for foreign_key in table.foreign_keys
    )


def generate_foreign_key_ddls(snapshot: SchemaSnapshot, *, target_dbms: Dbms) -> tuple[ForeignKeyDdl, ...]:
    if target_dbms is Dbms.POSTGRESQL:
        return generate_postgres_foreign_key_ddls(snapshot)
    if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return generate_mysql_foreign_key_ddls(snapshot)
    raise ValueError(f"Unsupported target DBMS for foreign key DDL: {target_dbms.value}")


def execute_foreign_key_ddls(
    *,
    ddls: tuple[ForeignKeyDdl, ...],
    executor: ForeignKeyExecutor,
) -> tuple[ForeignKeyExecutionResult, ...]:
    results = []
    for ddl in ddls:
        try:
            execution_result = executor.execute_ddl(ddl.ddl)
            results.append(
                ForeignKeyExecutionResult(
                    table=ddl.table,
                    constraint_name=ddl.constraint_name,
                    success=execution_result.success,
                    message=execution_result.message,
                )
            )
        except Exception as exc:
            results.append(
                ForeignKeyExecutionResult(
                    table=ddl.table,
                    constraint_name=ddl.constraint_name,
                    success=False,
                    message=str(exc),
                )
            )
    return tuple(results)


def _foreign_key_ddl(
    table: TableSchema,
    foreign_key: ForeignKeySchema,
    *,
    target_dbms: Dbms,
) -> ForeignKeyDdl:
    table_name = qualified_table_name(target_dbms, table.ref.schema, table.ref.name)
    columns = ", ".join(quote_identifier(target_dbms, column) for column in foreign_key.columns)
    referenced_table = qualified_table_name(target_dbms, foreign_key.referenced_table.schema, foreign_key.referenced_table.name)
    referenced_columns = ", ".join(quote_identifier(target_dbms, column) for column in foreign_key.referenced_columns)
    constraint_name = quote_identifier(target_dbms, foreign_key.name)
    ddl = (
        f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
        f"FOREIGN KEY ({columns}) REFERENCES {referenced_table} ({referenced_columns});"
    )
    return ForeignKeyDdl(table=table.ref.name, constraint_name=foreign_key.name, ddl=ddl)
