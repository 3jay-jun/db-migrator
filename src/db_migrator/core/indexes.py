from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from db_migrator.adapters.base import ExecutionResult
from db_migrator.config.models import AppConfig, Dbms, IndexApplyTiming, MigrationMode
from db_migrator.schema.models import IndexSchema, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.object_checks import create_index_ddl, is_auto_create_index


class IndexDdlExecutor(Protocol):
    def execute_ddl(self, ddl: str) -> ExecutionResult:
        """Execute one index DDL statement."""


@dataclass(frozen=True)
class IndexMigrationDecision:
    schema: str
    table: str
    index: str
    columns: tuple[str, ...]
    unique: bool
    timing: str
    auto_convertible: bool
    risk_level: str
    reason: str
    ddl: str | None = None


@dataclass(frozen=True)
class IndexExecutionResult:
    schema: str
    table: str
    index: str
    action: str
    success: bool
    message: str
    ddl: str | None = None


@dataclass(frozen=True)
class IndexExecutionSummary:
    phase: str
    decisions: tuple[IndexMigrationDecision, ...]
    indexes: tuple[IndexExecutionResult, ...]


def plan_index_migration(
    snapshot: SchemaSnapshot,
    *,
    config: AppConfig,
    target_dbms: Dbms,
) -> tuple[IndexMigrationDecision, ...]:
    return tuple(
        _index_decision(table, index, config=config, target_dbms=target_dbms)
        for table in snapshot.tables
        for index in table.indexes
    )


def execute_index_ddls(
    *,
    config: AppConfig,
    snapshot: SchemaSnapshot,
    executor: IndexDdlExecutor,
    phase: IndexApplyTiming,
    report_output_path: Path,
) -> IndexExecutionSummary:
    decisions = plan_index_migration(snapshot, config=config, target_dbms=config.target.dbms)
    results: list[IndexExecutionResult] = []
    for decision in decisions:
        if decision.timing != phase.value:
            continue
        if not config.migration.apply_indexes:
            results.append(_skipped_result(decision, "Index migration is disabled by migration.apply_indexes."))
            continue
        if decision.ddl is None:
            results.append(_skipped_result(decision, decision.reason))
            continue
        try:
            execution_result = executor.execute_ddl(decision.ddl)
            results.append(
                IndexExecutionResult(
                    schema=decision.schema,
                    table=decision.table,
                    index=decision.index,
                    action=phase.value,
                    success=execution_result.success,
                    message=execution_result.message,
                    ddl=decision.ddl,
                )
            )
        except Exception as exc:
            results.append(
                IndexExecutionResult(
                    schema=decision.schema,
                    table=decision.table,
                    index=decision.index,
                    action=phase.value,
                    success=False,
                    message=str(exc),
                    ddl=decision.ddl,
                )
            )
    summary = IndexExecutionSummary(phase=phase.value, decisions=decisions, indexes=tuple(results))
    write_index_execution_summary(summary, report_output_path)
    return summary


def write_index_execution_summary(summary: IndexExecutionSummary, report_output_path: Path) -> None:
    report_output_path.parent.mkdir(parents=True, exist_ok=True)
    report_output_path.write_text(json.dumps(asdict(summary), ensure_ascii=False, indent=2), encoding="utf-8")


def _index_decision(
    table: TableSchema,
    index: IndexSchema,
    *,
    config: AppConfig,
    target_dbms: Dbms,
) -> IndexMigrationDecision:
    override = config.migration.index_apply_overrides.get(_index_identifier(table.ref, index))
    auto_convertible = is_auto_create_index(index)
    if override is not None:
        timing = override
        reason = "Configured by migration.index_apply_overrides."
    elif not auto_convertible:
        timing = IndexApplyTiming.MANUAL_REVIEW
        reason = index.manual_review_reason or "Index definition requires manual review."
    elif _is_incremental_upsert_index(table, index, config):
        timing = IndexApplyTiming.PRE_DATA
        reason = "Incremental upsert may require this unique index before data writes."
    else:
        timing = IndexApplyTiming.POST_DATA
        reason = _post_data_reason(table, config)

    ddl = (
        create_index_ddl(table.ref, index, target_dbms=target_dbms, target_database=config.target.database)
        if auto_convertible and timing is not IndexApplyTiming.SKIP
        else None
    )
    return IndexMigrationDecision(
        schema=table.ref.schema,
        table=table.ref.name,
        index=index.name,
        columns=index.columns,
        unique=index.unique,
        timing=timing.value,
        auto_convertible=auto_convertible,
        risk_level=_risk_level(table, index, timing, config),
        reason=reason,
        ddl=ddl if timing in {IndexApplyTiming.PRE_DATA, IndexApplyTiming.POST_DATA} else None,
    )


def _is_incremental_upsert_index(table: TableSchema, index: IndexSchema, config: AppConfig) -> bool:
    if config.migration.mode is not MigrationMode.INCREMENTAL and not config.incremental.enabled:
        return False
    if table.primary_key is not None and table.primary_key.columns:
        return False
    return index.unique


def _post_data_reason(table: TableSchema, config: AppConfig) -> str:
    if (table.estimated_rows or 0) >= config.migration.index_large_table_threshold:
        return "Large table bulk load usually performs better when secondary indexes are created after data migration."
    return "Default policy creates secondary indexes after data migration to avoid unnecessary insert overhead."


def _risk_level(table: TableSchema, index: IndexSchema, timing: IndexApplyTiming, config: AppConfig) -> str:
    if timing is IndexApplyTiming.MANUAL_REVIEW:
        return "high"
    if (table.estimated_rows or 0) >= config.migration.index_large_table_threshold:
        return "medium"
    if index.unique:
        return "medium"
    return "low"


def _index_identifier(table: TableRef, index: IndexSchema) -> str:
    return f"{table.schema}.{table.name}.{index.name}"


def _skipped_result(decision: IndexMigrationDecision, message: str) -> IndexExecutionResult:
    return IndexExecutionResult(
        schema=decision.schema,
        table=decision.table,
        index=decision.index,
        action="skip",
        success=True,
        message=message,
        ddl=decision.ddl,
    )
