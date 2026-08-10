from db_migrator.adapters.postgres import PostgresDdlGenerator
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

    assert 'CREATE TABLE "legacy"."users"' in result.ddl
    assert '"id" integer NOT NULL' in result.ddl
    assert '"email" varchar(255) NOT NULL' in result.ddl
    assert 'PRIMARY KEY ("id")' in result.ddl
