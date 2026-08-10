from pathlib import Path

import pytest

from db_migrator.adapters.mysql import ExecutionResult
from db_migrator.config.models import (
    AppConfig,
    ExistingTablePolicy,
    MigrationConfig,
    SafetyConfig,
    TargetConfig,
    TargetEnvironment,
)
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


class FakeDdlExecutor:
    def __init__(self, *, existing_tables: set[str] | None = None) -> None:
        self.existing_tables = existing_tables or set()
        self.executed_ddls: list[str] = []
        self.truncated_tables: list[str] = []

    def table_exists(self, table_schema) -> bool:
        return table_schema.ref.name in self.existing_tables

    def execute_ddl(self, ddl: str) -> ExecutionResult:
        self.executed_ddls.append(ddl)
        return ExecutionResult(success=True, message="executed")

    def truncate_table(self, table_schema) -> ExecutionResult:
        self.truncated_tables.append(table_schema.ref.name)
        return ExecutionResult(success=True, message="truncated")


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
