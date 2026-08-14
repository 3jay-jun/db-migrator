from datetime import datetime, timezone
from uuid import UUID

from db_migrator.adapters.mysql import MySqlDdlGenerator, _mysql_row_values, quote_mysql_identifier
from db_migrator.schema.models import ColumnSchema, PrimaryKey, TableRef, TableSchema
from db_migrator.schema.type_mapping import postgres_type_to_common


def test_quote_mysql_identifier_escapes_backticks() -> None:
    assert quote_mysql_identifier("odd`name") == "`odd``name`"


def test_generate_create_table_uses_common_type_mapping_and_primary_key() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="users"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="integer",
                common_type=postgres_type_to_common("integer"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
            ColumnSchema(
                name="email",
                source_type="character varying(255)",
                common_type=postgres_type_to_common("character varying(255)"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )

    result = MySqlDdlGenerator().generate_create_table(table)

    assert "`id` int NOT NULL" in result.ddl
    assert "`email` varchar(255) NOT NULL" in result.ddl
    assert "PRIMARY KEY (`id`)" in result.ddl
    assert result.ddl.endswith(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;")


def test_generate_create_table_can_qualify_with_target_database() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="users"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="integer",
                common_type=postgres_type_to_common("integer"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )

    result = MySqlDdlGenerator(target_database="target_db").generate_create_table(table)

    assert "CREATE TABLE `target_db`.`users`" in result.ddl
    assert "`public`.`users`" not in result.ddl


def test_generate_create_table_preserves_auto_increment_column_property() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="privacy_body"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="bigint",
                common_type=postgres_type_to_common("bigint"),
                nullable=False,
                default="nextval('privacy_body_id_seq'::regclass)",
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
                auto_increment=True,
            ),
        ),
        primary_key=PrimaryKey(columns=("id",)),
    )

    result = MySqlDdlGenerator().generate_create_table(table)

    assert "`id` bigint NOT NULL AUTO_INCREMENT" in result.ddl
    assert "PRIMARY KEY (`id`)" in result.ddl


def test_mysql_row_values_normalize_driver_specific_python_values() -> None:
    table = TableSchema(
        ref=TableRef(schema="public", name="documents"),
        columns=(
            ColumnSchema(
                name="payload",
                source_type="jsonb",
                common_type=postgres_type_to_common("jsonb"),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
            ColumnSchema(
                name="identifier",
                source_type="uuid",
                common_type=postgres_type_to_common("uuid"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=2,
            ),
            ColumnSchema(
                name="content",
                source_type="bytea",
                common_type=postgres_type_to_common("bytea"),
                nullable=True,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=3,
            ),
            ColumnSchema(
                name="updated_at",
                source_type="timestamp with time zone",
                common_type=postgres_type_to_common("timestamp with time zone"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=4,
            ),
        ),
    )

    values = _mysql_row_values(
        table,
        {
            "payload": {"b": 2, "a": [1]},
            "identifier": UUID("00000000-0000-4000-8000-000000000001"),
            "content": memoryview(b"abc"),
            "updated_at": datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
        },
        ("payload", "identifier", "content", "updated_at"),
    )

    assert values == (
        '{"a":[1],"b":2}',
        "00000000-0000-4000-8000-000000000001",
        b"abc",
        datetime(2026, 1, 1, 9, 0),
    )
