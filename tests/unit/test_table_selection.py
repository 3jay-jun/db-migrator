from pathlib import Path

from db_migrator.schema.models import IndexSchema, TableSchema
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json
from db_migrator.schema.table_selection import (
    key_columns_for_resume,
    key_columns_for_upsert,
    stable_order_columns,
    writable_columns,
)


def test_table_selection_uses_primary_key_for_stable_order_and_keys() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")

    assert writable_columns(users) == ("id", "email", "profile", "created_at")
    assert stable_order_columns(users, writable_columns(users)) == ("id",)
    assert key_columns_for_resume(users, ("id",)) == ("id",)
    assert key_columns_for_upsert(users) == ("id",)


def test_table_selection_falls_back_to_unique_index_for_keys() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    unique_email_users = TableSchema(
        ref=users.ref,
        columns=users.columns,
        primary_key=None,
        indexes=(IndexSchema(name="users_email_key", columns=("email",), unique=True),),
        foreign_keys=users.foreign_keys,
        estimated_rows=users.estimated_rows,
    )

    assert stable_order_columns(unique_email_users, ("id", "email")) == ("id", "email")
    assert key_columns_for_resume(unique_email_users, ("id", "email")) == ("email",)
    assert key_columns_for_upsert(unique_email_users) == ("email",)


def test_table_selection_excludes_generated_columns() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    generated_profile_users = TableSchema(
        ref=users.ref,
        columns=tuple(
            column
            if column.name != "profile"
            else type(column)(
                name=column.name,
                source_type=column.source_type,
                common_type=column.common_type,
                nullable=column.nullable,
                default=column.default,
                is_generated=True,
                generation_expression="{}",
                ordinal_position=column.ordinal_position,
                warnings=column.warnings,
            )
            for column in users.columns
        ),
        primary_key=users.primary_key,
        indexes=users.indexes,
        foreign_keys=users.foreign_keys,
        estimated_rows=users.estimated_rows,
    )

    assert writable_columns(generated_profile_users) == ("id", "email", "created_at")
