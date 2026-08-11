from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from db_migrator.adapters.postgres import PostgresDdlGenerator, PostgresTargetAdapter, _postgres_row_values
from db_migrator.config.models import TargetConfig
from db_migrator.schema.models import ColumnSchema, PrimaryKey, TableRef, TableSchema
from db_migrator.schema.type_mapping import mysql_type_to_common


def test_generate_postgres_create_table_uses_common_type_mapping_and_primary_key() -> None:
    table = TableSchema(
        ref=TableRef(schema="legacy", name="users"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="int",
                common_type=mysql_type_to_common("int"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
            ColumnSchema(
                name="email",
                source_type="varchar(255)",
                common_type=mysql_type_to_common("varchar(255)"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )

    result = PostgresDdlGenerator().generate_create_table(table)

    assert 'CREATE SCHEMA IF NOT EXISTS "legacy"' in result.ddl
    assert 'CREATE TABLE "legacy"."users"' in result.ddl
    assert '"id" integer NOT NULL' in result.ddl
    assert '"email" varchar(255) NOT NULL' in result.ddl
    assert 'PRIMARY KEY ("id")' in result.ddl


class FakePostgresCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, statement: str) -> None:
        self.statements.append(statement)


class FakePostgresConnection:
    def __init__(self) -> None:
        self.cursor_instance = FakePostgresCursor()
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FakePostgresCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commit_count += 1


def test_postgres_target_executes_generated_ddl_statements_separately() -> None:
    connection = FakePostgresConnection()
    adapter = PostgresTargetAdapter(TargetConfig(database="target_pg", port=5432))
    adapter._connect = lambda: connection

    adapter.execute_ddl('CREATE SCHEMA IF NOT EXISTS "legacy";\nCREATE TABLE "legacy"."users" ("id" integer);')

    assert connection.cursor_instance.statements == [
        'CREATE SCHEMA IF NOT EXISTS "legacy"',
        'CREATE TABLE "legacy"."users" ("id" integer)',
    ]
    assert connection.commit_count == 1


def test_postgres_row_values_normalize_mysql_source_values() -> None:
    table = TableSchema(
        ref=TableRef(schema="source", name="documents"),
        columns=(
            ColumnSchema(
                name="payload",
                source_type="json",
                common_type=mysql_type_to_common("json"),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
            ColumnSchema(
                name="identifier",
                source_type="char(36)",
                common_type=mysql_type_to_common("char(36)"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
            ColumnSchema(
                name="content",
                source_type="longblob",
                common_type=mysql_type_to_common("longblob"),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=3,
            ),
            ColumnSchema(
                name="updated_at",
                source_type="timestamp",
                common_type=mysql_type_to_common("timestamp"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=4,
            ),
            ColumnSchema(
                name="active",
                source_type="tinyint(1)",
                common_type=mysql_type_to_common("tinyint(1)"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=5,
            ),
        ),
    )

    values = _postgres_row_values(
        table,
        {
            "payload": '{"b":2,"a":[1]}',
            "identifier": "00000000-0000-4000-8000-000000000001",
            "content": memoryview(b"abc"),
            "updated_at": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
            "active": 1,
        },
        ("payload", "identifier", "content", "updated_at", "active"),
    )

    assert isinstance(values[0], Jsonb)
    assert values[1] == "00000000-0000-4000-8000-000000000001"
    assert values[2] == b"abc"
    assert values[3] == datetime(2026, 1, 1, 9, 0)
    assert values[4] is True
