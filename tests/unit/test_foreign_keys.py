from pathlib import Path

from db_migrator.adapters.mysql import ExecutionResult, MySqlDdlGenerator
from db_migrator.core.foreign_keys import execute_foreign_key_ddls, generate_mysql_foreign_key_ddls, generate_postgres_foreign_key_ddls
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FailingFkExecutor:
    def execute_ddl(self, ddl: str) -> ExecutionResult:
        raise RuntimeError("fk failed")


def test_generate_mysql_foreign_key_alter_table_ddls() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))

    ddls = generate_mysql_foreign_key_ddls(snapshot)

    assert len(ddls) == 1
    assert "ALTER TABLE `public`.`orders` ADD CONSTRAINT `orders_user_id_fkey`" in ddls[0].ddl
    assert "REFERENCES `public`.`users` (`id`)" in ddls[0].ddl


def test_generate_postgres_foreign_key_alter_table_ddls() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))

    ddls = generate_postgres_foreign_key_ddls(snapshot)

    assert len(ddls) == 1
    assert 'ALTER TABLE "public"."orders" ADD CONSTRAINT "orders_user_id_fkey"' in ddls[0].ddl
    assert 'REFERENCES "public"."users" ("id")' in ddls[0].ddl


def test_create_table_ddl_does_not_inline_foreign_keys() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    orders = next(table for table in snapshot.tables if table.ref.name == "orders")

    ddl = MySqlDdlGenerator().generate_create_table(orders).ddl

    assert "FOREIGN KEY" not in ddl


def test_fk_execution_failure_is_reported_without_raising() -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    ddls = generate_mysql_foreign_key_ddls(snapshot)

    results = execute_foreign_key_ddls(ddls=ddls, executor=FailingFkExecutor())

    assert results[0].success is False
    assert results[0].message == "fk failed"
