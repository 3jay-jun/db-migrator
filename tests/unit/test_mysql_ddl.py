from db_migrator.adapters.mysql import MySqlDdlGenerator, quote_mysql_identifier
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
