from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from db_migrator.adapters.mysql import ExecutionResult
from db_migrator.adapters.registry import DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.models import AppConfig, ExistingTablePolicy
from db_migrator.core.foreign_keys import generate_foreign_key_ddls
from db_migrator.core.overwrite_audit import OverwriteAuditStore
from db_migrator.core.safety_guard import SafetyGuardInput, TargetSafetyGuard
from db_migrator.schema.column_plan import ColumnPlan
from db_migrator.schema.models import SchemaSnapshot, TableRef, TableSchema


class TargetDdlExecutor(Protocol):
    def table_exists(self, table_schema: TableSchema) -> bool:
        """Return whether the target table already exists."""

    def execute_ddl(self, ddl: str) -> ExecutionResult:
        """Execute one DDL statement."""

    def truncate_table(self, table_schema: TableSchema) -> ExecutionResult:
        """Truncate one target table."""

    def drop_table(self, table_schema: TableSchema) -> ExecutionResult:
        """Drop one target table."""


@dataclass(frozen=True)
class DdlTableExecutionResult:
    schema: str
    table: str
    action: str
    success: bool
    message: str
    ddl: str | None = None


@dataclass(frozen=True)
class DdlExecutionSummary:
    allowed: bool
    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    tables: tuple[DdlTableExecutionResult, ...]
    foreign_keys: tuple[DdlTableExecutionResult, ...] = ()


class DdlExecutionBlocked(RuntimeError):
    def __init__(self, blocking_reasons: tuple[str, ...]) -> None:
        super().__init__(f"DDL execution blocked by Safety Guard: {', '.join(blocking_reasons)}")
        self.blocking_reasons = blocking_reasons


def execute_schema_ddl(
    *,
    config: AppConfig,
    snapshot: SchemaSnapshot,
    executor: TargetDdlExecutor,
    report_output_path: Path,
    registry: DbmsAdapterRegistry | None = None,
    column_plans: dict[TableRef, ColumnPlan] | None = None,
) -> DdlExecutionSummary:
    dry_run_report_exists = _dry_run_report_exists(config)
    guard_decision = TargetSafetyGuard().evaluate(
        SafetyGuardInput(
            target=config.target,
            safety=config.safety,
            existing_table_policy=config.migration.existing_table_policy,
            table_count=len(snapshot.tables),
            estimated_rows=sum(table.estimated_rows or 0 for table in snapshot.tables),
            dry_run_report_exists=dry_run_report_exists,
        )
    )

    if not guard_decision.allowed:
        summary = DdlExecutionSummary(
            allowed=False,
            blocking_reasons=guard_decision.blocking_reasons,
            warnings=guard_decision.warnings,
            tables=(),
        )
        write_ddl_execution_summary(summary, report_output_path)
        raise DdlExecutionBlocked(guard_decision.blocking_reasons)

    adapter_registry = registry or default_adapter_registry()
    generator = adapter_registry.create_ddl_generator(config.target.dbms, target_database=config.target.database)
    audit_store = (
        OverwriteAuditStore(report_output_path.with_name("overwrite-audit.sqlite"))
        if config.migration.existing_table_policy is ExistingTablePolicy.OVERWRITE
        else None
    )
    audit_run_id = audit_store.start_run(config=config, table_count=len(snapshot.tables)) if audit_store is not None else None
    table_results = []
    try:
        for table in snapshot.tables:
            table_exists = executor.table_exists(table)
            if table_exists and config.migration.existing_table_policy is ExistingTablePolicy.SKIP:
                table_results.append(_skipped_table_result(table, "Target table already exists."))
                continue

            if table_exists and config.migration.existing_table_policy is ExistingTablePolicy.SYNC:
                table_results.append(
                    DdlTableExecutionResult(
                        schema=table.ref.schema,
                        table=table.ref.name,
                        action="sync_existing",
                        success=True,
                        message="Target table already exists; CREATE skipped for sync policy.",
                    )
                )
                for ddl in _sync_alter_candidates(table, column_plans):
                    execution_result = executor.execute_ddl(ddl)
                    table_results.append(
                        DdlTableExecutionResult(
                            schema=table.ref.schema,
                            table=table.ref.name,
                            action="alter_table",
                            success=execution_result.success,
                            message=execution_result.message,
                            ddl=ddl,
                        )
                    )
                continue

            if table_exists and config.migration.existing_table_policy is ExistingTablePolicy.TRUNCATE_RELOAD:
                truncate_result = executor.truncate_table(table)
                table_results.append(
                    DdlTableExecutionResult(
                        schema=table.ref.schema,
                        table=table.ref.name,
                        action="truncate",
                        success=truncate_result.success,
                        message=truncate_result.message,
                    )
                )
                continue

            if table_exists and config.migration.existing_table_policy is ExistingTablePolicy.OVERWRITE:
                drop_result = executor.drop_table(table)
                _record_overwrite_action(audit_store, audit_run_id, table=table, action="drop", result=drop_result)
                table_results.append(
                    DdlTableExecutionResult(
                        schema=table.ref.schema,
                        table=table.ref.name,
                        action="drop",
                        success=drop_result.success,
                        message=drop_result.message,
                    )
                )

            ddl_result = generator.generate_create_table(table)
            execution_result = executor.execute_ddl(ddl_result.ddl)
            _record_overwrite_action(
                audit_store,
                audit_run_id,
                table=table,
                action="create",
                result=execution_result,
                ddl=ddl_result.ddl,
            )
            table_results.append(
                DdlTableExecutionResult(
                    schema=table.ref.schema,
                    table=table.ref.name,
                    action="create",
                    success=execution_result.success,
                    message=execution_result.message,
                    ddl=ddl_result.ddl,
                )
            )
    except Exception as exc:
        if audit_store is not None and audit_run_id is not None:
            audit_store.finish_run(audit_run_id, status="failed", message=str(exc))
        raise

    foreign_key_results = _execute_foreign_key_ddls(config=config, snapshot=snapshot, executor=executor)

    summary = DdlExecutionSummary(
        allowed=True,
        blocking_reasons=(),
        warnings=guard_decision.warnings,
        tables=tuple(table_results),
        foreign_keys=foreign_key_results,
    )
    write_ddl_execution_summary(summary, report_output_path)
    if audit_store is not None and audit_run_id is not None:
        status = "completed" if all(table.success for table in summary.tables) else "failed"
        audit_store.finish_run(audit_run_id, status=status)
    return summary


def write_ddl_execution_summary(summary: DdlExecutionSummary, report_output_path: Path) -> None:
    report_output_path.parent.mkdir(parents=True, exist_ok=True)
    report_output_path.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _execute_foreign_key_ddls(
    *,
    config: AppConfig,
    snapshot: SchemaSnapshot,
    executor: TargetDdlExecutor,
) -> tuple[DdlTableExecutionResult, ...]:
    if not config.migration.apply_foreign_keys:
        return ()
    if config.migration.existing_table_policy is ExistingTablePolicy.SYNC:
        return ()

    results: list[DdlTableExecutionResult] = []
    for foreign_key_ddl in generate_foreign_key_ddls(snapshot, target_dbms=config.target.dbms):
        try:
            execution_result = executor.execute_ddl(foreign_key_ddl.ddl)
            results.append(
                DdlTableExecutionResult(
                    schema="",
                    table=foreign_key_ddl.table,
                    action="add_foreign_key",
                    success=execution_result.success,
                    message=execution_result.message,
                    ddl=foreign_key_ddl.ddl,
                )
            )
        except Exception as exc:
            results.append(
                DdlTableExecutionResult(
                    schema="",
                    table=foreign_key_ddl.table,
                    action="add_foreign_key",
                    success=False,
                    message=str(exc),
                    ddl=foreign_key_ddl.ddl,
                )
            )
    return tuple(results)


def _dry_run_report_exists(config: AppConfig) -> bool:
    if config.migration.dry_run_report_path is None:
        return False
    return Path(config.migration.dry_run_report_path).exists()


def _skipped_table_result(table: TableSchema, message: str) -> DdlTableExecutionResult:
    return DdlTableExecutionResult(
        schema=table.ref.schema,
        table=table.ref.name,
        action="skip",
        success=True,
        message=message,
    )


def _record_overwrite_action(
    audit_store: OverwriteAuditStore | None,
    audit_run_id: int | None,
    *,
    table: TableSchema,
    action: str,
    result: ExecutionResult,
    ddl: str | None = None,
) -> None:
    if audit_store is None or audit_run_id is None:
        return
    audit_store.record_table_action(
        audit_run_id,
        table=table,
        action=action,
        status="completed" if result.success else "failed",
        message=result.message,
        ddl=ddl,
    )


def _sync_alter_candidates(table: TableSchema, column_plans: dict[TableRef, ColumnPlan] | None) -> tuple[str, ...]:
    if not column_plans:
        return ()
    for plan in column_plans.values():
        if plan.target_table.ref == table.ref:
            return plan.rename_column_ddls + plan.type_change_ddls + plan.add_column_ddls + tuple(item.alter_table_ddl for item in plan.source_only_columns if item.alter_table_ddl)
    return ()
