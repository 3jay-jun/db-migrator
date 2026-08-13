from pathlib import Path
import sqlite3

import pytest

from db_migrator.adapters.mysql import ExecutionResult
from db_migrator.config.models import (
    AppConfig,
    ExistingTablePolicy,
    MigrationConfig,
    SafetyConfig,
    TargetConfig,
    TargetEnvironment,
    TableRunConfig,
)
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.schema.column_plan import build_column_plan
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, PrimaryKey, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeDdlExecutor:
    def __init__(self, *, existing_tables: set[str] | None = None) -> None:
        self.existing_tables = existing_tables or set()
        self.executed_ddls: list[str] = []
        self.truncated_tables: list[str] = []
        self.dropped_tables: list[str] = []

    def table_exists(self, table_schema) -> bool:
        return table_schema.ref.name in self.existing_tables

    def execute_ddl(self, ddl: str) -> ExecutionResult:
        self.executed_ddls.append(ddl)
        return ExecutionResult(success=True, message="executed")

    def truncate_table(self, table_schema) -> ExecutionResult:
        self.truncated_tables.append(table_schema.ref.name)
        return ExecutionResult(success=True, message="truncated")

    def drop_table(self, table_schema) -> ExecutionResult:
        self.dropped_tables.append(table_schema.ref.name)
        return ExecutionResult(success=True, message="dropped")


def test_execute_schema_ddl_creates_missing_tables(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor()

    summary = execute_schema_ddl(
        config=AppConfig(),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.allowed is True
    assert len(executor.executed_ddls) == 2
    assert (tmp_path / "ddl-execution.json").exists()


def test_execute_schema_ddl_uses_target_database_for_generated_ddl(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor()

    execute_schema_ddl(
        config=AppConfig(target=TargetConfig(database="target_db")),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert executor.executed_ddls
    assert all("CREATE TABLE `target_db`." in ddl for ddl in executor.executed_ddls)
    assert all("CREATE TABLE `public`." not in ddl for ddl in executor.executed_ddls)


def test_execute_schema_ddl_skips_existing_table_when_policy_is_skip(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=AppConfig(),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.tables[0].action == "skip"
    assert len(executor.executed_ddls) == 1


def test_execute_schema_ddl_append_creates_only_missing_target_tables(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=AppConfig(migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.APPEND)),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.tables[0].action == "skip"
    assert "missing target tables" in summary.tables[0].message
    assert len(executor.executed_ddls) == 1
    assert "`orders`" in executor.executed_ddls[0]


def test_execute_schema_ddl_skips_existing_table_when_policy_is_sync(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=AppConfig(migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC)),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.tables[0].action == "sync_existing"
    assert "CREATE skipped" in summary.tables[0].message
    assert len(executor.executed_ddls) == 1


def test_execute_schema_ddl_applies_sync_alter_candidates(tmp_path: Path) -> None:
    source_table = _table("public", "users", ("id", "email", "legacy_code"))
    target_table = _table("public", "users", ("id", "email"))
    config = AppConfig(
        migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC),
        tables={
            "public.users": TableRunConfig.model_validate(
                {"source_only_columns": {"legacy_code": "add_to_target"}}
            )
        },
    )
    column_plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=SchemaSnapshot(tables=(target_table,)),
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
        column_plans={source_table.ref: column_plan},
    )

    assert summary.tables[0].action == "sync_existing"
    assert summary.tables[1].action == "alter_table"
    assert executor.executed_ddls == ["ALTER TABLE `public`.`users` ADD COLUMN `legacy_code` longtext NOT NULL;"]


def test_execute_schema_ddl_applies_configured_new_target_column_candidates(tmp_path: Path) -> None:
    source_table = _table("public", "users", ("id", "legacy_code"))
    target_table = _table("public", "users", ("id",))
    config = AppConfig(
        migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC),
        tables={
            "public.users": TableRunConfig.model_validate(
                {"columns": {"legacy_id": {"source": "legacy_code"}}}
            )
        },
    )
    column_plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=SchemaSnapshot(tables=(column_plan.target_table,)),
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
        column_plans={source_table.ref: column_plan},
    )

    assert summary.tables[1].action == "alter_table"
    assert executor.executed_ddls == ["ALTER TABLE `public`.`users` ADD COLUMN `legacy_id` longtext NOT NULL;"]


def test_execute_schema_ddl_applies_configured_target_column_rename(tmp_path: Path) -> None:
    source_table = _table("public", "users", ("id", "email"))
    target_table = _table("public", "users", ("id", "email"))
    config = AppConfig(
        migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC),
        tables={
            "public.users": TableRunConfig.model_validate(
                {"columns": {"id_": {"source": "id"}}}
            )
        },
    )
    column_plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=SchemaSnapshot(tables=(column_plan.target_table,)),
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
        column_plans={source_table.ref: column_plan},
    )

    assert summary.tables[1].action == "alter_table"
    assert executor.executed_ddls == ["ALTER TABLE `public`.`users` RENAME COLUMN `id` TO `id_`;"]


def test_execute_schema_ddl_applies_existing_target_column_type_change(tmp_path: Path) -> None:
    source_table = _typed_table("public", "users", {"email": ("character varying(320)", CommonTypeKind.STRING, 320)})
    target_table = _typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)})
    config = AppConfig(migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.SYNC))
    column_plan = build_column_plan(config=config, source_table=source_table, target_table=target_table)
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=SchemaSnapshot(tables=(column_plan.target_table,)),
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
        column_plans={source_table.ref: column_plan},
    )

    assert summary.tables[1].action == "alter_table"
    assert executor.executed_ddls == ["ALTER TABLE `public`.`users` MODIFY COLUMN `email` varchar(320) NOT NULL;"]


def test_execute_schema_ddl_skips_foreign_keys_when_policy_is_sync(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    executor = FakeDdlExecutor(existing_tables={"users", "orders"})

    summary = execute_schema_ddl(
        config=AppConfig(
            migration=MigrationConfig(
                existing_table_policy=ExistingTablePolicy.SYNC,
                apply_foreign_keys=True,
            )
        ),
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.foreign_keys == ()


def test_execute_schema_ddl_blocks_production_truncate_without_safety_approval(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    config = AppConfig(
        target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
        migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.TRUNCATE_RELOAD),
    )

    with pytest.raises(DdlExecutionBlocked):
        execute_schema_ddl(
            config=config,
            snapshot=snapshot,
            executor=FakeDdlExecutor(existing_tables={"users", "orders"}),
            report_output_path=tmp_path / "ddl-execution.json",
        )


def test_execute_schema_ddl_truncates_existing_table_after_dry_run_requirement(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    dry_run_report = tmp_path / "summary.json"
    dry_run_report.write_text("{}", encoding="utf-8")
    config = AppConfig(
        target=TargetConfig(environment=TargetEnvironment.PRODUCTION),
        safety=SafetyConfig(allow_destructive_on_production=True),
        migration=MigrationConfig(
            existing_table_policy=ExistingTablePolicy.TRUNCATE_RELOAD,
            dry_run_report_path=str(dry_run_report),
        ),
    )
    executor = FakeDdlExecutor(existing_tables={"users", "orders"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.allowed is True
    assert executor.truncated_tables == ["users", "orders"]


def test_execute_schema_ddl_overwrites_existing_table_and_writes_audit_log(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    config = AppConfig(
        safety=SafetyConfig(allow_destructive_on_production=True),
        migration=MigrationConfig(existing_table_policy=ExistingTablePolicy.OVERWRITE),
    )
    executor = FakeDdlExecutor(existing_tables={"users"})

    summary = execute_schema_ddl(
        config=config,
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.allowed is True
    assert executor.dropped_tables == ["users"]
    assert len(executor.executed_ddls) == 2
    with sqlite3.connect(tmp_path / "overwrite-audit.sqlite") as connection:
        runs = connection.execute("select job_id, status, table_count from overwrite_runs").fetchall()
        actions = connection.execute("select source_table, action, status from overwrite_table_actions").fetchall()
    assert runs == [("db-migration-job", "completed", 1)]
    assert ("users", "drop", "completed") in actions
    assert ("users", "create", "completed") in actions
    assert ("orders", "create", "completed") not in actions


def test_execute_schema_ddl_never_applies_foreign_keys_even_when_enabled(tmp_path: Path) -> None:
    snapshot = load_schema_snapshot_from_json(Path("tests/fixtures/schema_snapshot.json"))
    config = AppConfig(
        migration=MigrationConfig(apply_foreign_keys=True),
    )
    executor = FakeDdlExecutor()

    summary = execute_schema_ddl(
        config=config,
        snapshot=snapshot,
        executor=executor,
        report_output_path=tmp_path / "ddl-execution.json",
    )

    assert summary.foreign_keys == ()
    assert len(executor.executed_ddls) == 2
    assert all("FOREIGN KEY" not in ddl for ddl in executor.executed_ddls)


def _table(schema: str, name: str, columns: tuple[str, ...]) -> TableSchema:
    return TableSchema(
        ref=TableRef(schema=schema, name=name),
        primary_key=PrimaryKey(columns=("id",)) if "id" in columns else None,
        columns=tuple(
            ColumnSchema(
                name=column,
                source_type="integer" if column == "id" else "text",
                common_type=CommonType(
                    kind=CommonTypeKind.INTEGER if column == "id" else CommonTypeKind.TEXT,
                    policy=TypePolicy.AUTO_CONVERT,
                ),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=index,
            )
            for index, column in enumerate(columns, start=1)
        ),
    )


def _typed_table(schema: str, name: str, columns: dict[str, tuple[str, CommonTypeKind, int | None]]) -> TableSchema:
    return TableSchema(
        ref=TableRef(schema=schema, name=name),
        primary_key=None,
        columns=tuple(
            ColumnSchema(
                name=column,
                source_type=source_type,
                common_type=CommonType(kind=kind, length=length, policy=TypePolicy.AUTO_CONVERT),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=index,
            )
            for index, (column, (source_type, kind, length)) in enumerate(columns.items(), start=1)
        ),
    )
