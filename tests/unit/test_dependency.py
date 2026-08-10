from dataclasses import replace
from pathlib import Path

from db_migrator.schema.dependency import plan_table_creation_order
from db_migrator.schema.models import ForeignKeySchema, SchemaSnapshot, TableRef
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


def test_dependency_plan_orders_referenced_tables_first() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))

    plan = plan_table_creation_order(snapshot)

    assert [table.name for table in plan.creation_order] == ["users", "orders"]
    assert plan.manual_review == ()


def test_dependency_plan_reports_excluded_referenced_table() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")

    plan = plan_table_creation_order(SchemaSnapshot(tables=(orders,)))

    assert plan.creation_order == (orders.ref,)
    assert "references excluded table" in plan.manual_review[0]


def test_dependency_plan_reports_cycle() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    users = next(table for table in snapshot.tables if table.ref.name == "users")
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")
    cyclic_users = replace(
        users,
        foreign_keys=(
            ForeignKeySchema(
                name="users_order_id_fkey",
                columns=("id",),
                referenced_table=orders.ref,
                referenced_columns=("id",),
            ),
        ),
    )

    plan = plan_table_creation_order(SchemaSnapshot(tables=(cyclic_users, orders)))

    assert any("Cycle detected" in review for review in plan.manual_review)
