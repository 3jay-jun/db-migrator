from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any, Iterator

from db_migrator.adapters.base import SourceAdapter, TargetAdapter
from db_migrator.adapters.mysql import MySqlAdapterError
from db_migrator.adapters.postgres import PostgresAdapterError
from db_migrator.adapters.registry import AdapterRegistryError, DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import AppConfig, ColumnTransformConfig, Dbms, IndexApplyTiming, SourceConfig, SourceOnlyColumnAction, TableRunConfig
from db_migrator.connection import ResolvedAppConfig, TunnelError, TunnelFactory, TunnelManager
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.core.dml_migration import build_resume_plan, build_retry_failed_plan, migrate_tables, stable_order_columns
from db_migrator.core.engine import MigrationEngine
from db_migrator.core.events import EventPublisher, MigrationEvent, QueueEventPublisher
from db_migrator.core.existing_table_policy import build_existing_table_execution_plan
from db_migrator.core.health import run_health_checks
from db_migrator.core.incremental import migrate_incremental_tables
from db_migrator.core.indexes import execute_index_ddls
from db_migrator.core.foreign_keys import execute_foreign_key_ddls, generate_foreign_key_ddls
from db_migrator.core.manual_migration import export_manual_migration_files
from db_migrator.core.validation import ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.core.validation_readers import SourceTargetValidationReader
from db_migrator.application.table_mapping import ColumnPlanTargetAdapter, TargetMappingAdapter
from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.execution_artifacts import load_execution_artifacts
from db_migrator.reports.final_report import write_validation_report
from db_migrator.reports.incremental_report import write_incremental_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.common_types import CommonType
from db_migrator.schema.models import SamplePosition, SchemaSnapshot, TableRef, TableSchema
from db_migrator.schema.schema_pair import SchemaPairPlan, SchemaPairResolver
from db_migrator.schema.snapshot_io import SchemaSnapshotLoadError, load_schema_snapshot_from_json
from db_migrator.schema.table_mapping import TableMappingResolver


@dataclass(frozen=True)
class CommandResult:
    command: str
    success: bool
    message: str
    job_id: str | None = None
    status: str | None = None
    output_dir: Path | None = None
    output_file: Path | None = None
    report_html: Path | None = None
    table_count: int | None = None
    warning_count: int | None = None
    rows_written: int | None = None
    rows_upserted: int | None = None
    events: tuple[MigrationEvent, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ColumnSelection:
    name: str
    source_type: str
    common_type: CommonType
    nullable: bool
    primary_key: bool


@dataclass(frozen=True)
class TableSelection:
    identifier: str
    schema: str
    table: str
    column_count: int
    estimated_rows: int | None
    has_primary_key: bool
    columns: tuple[ColumnSelection, ...] = ()


@dataclass(frozen=True)
class CommandRuntime:
    config: ResolvedAppConfig
    source: SourceAdapter | None = None
    target: TargetAdapter | None = None

    @property
    def original(self) -> AppConfig:
        return self.config.original

    @property
    def resolved(self) -> AppConfig:
        return self.config.resolved


class MigrationApplicationService:
    def __init__(self, registry: DbmsAdapterRegistry | None = None, tunnel_factory: TunnelFactory | None = None) -> None:
        self._registry = registry or default_adapter_registry()
        self._tunnel_manager = TunnelManager(tunnel_factory)

    def run_bootstrap(self, config: Path | None = None) -> CommandResult:
        try:
            app_config = load_config(config)
            event_queue: Queue[MigrationEvent] = Queue()
            result = MigrationEngine(QueueEventPublisher(event_queue)).run_dry_bootstrap(app_config)
            events = _drain_events(event_queue)
            return CommandResult(
                command="bootstrap",
                success=True,
                message=f"job_id={result.job_id} status={result.status}",
                job_id=result.job_id,
                status=result.status,
                events=events,
            )
        except _KNOWN_ERRORS as exc:
            return _failure("bootstrap", exc)

    def run_doctor(self, project_root: Path = Path(".")) -> CommandResult:
        report = run_health_checks(project_root.resolve())
        return CommandResult(
            command="doctor",
            success=report.ok,
            message="Health checks passed." if report.ok else "Health checks failed.",
            details={"checks": report.checks},
        )

    def run_test_source_connection(self, *, config: Path) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                connected = runtime.source.test_connection()
            return CommandResult(
                command="test-source",
                success=connected,
                message=_connection_test_message("Source", runtime, "source", connected),
            )
        except _KNOWN_ERRORS as exc:
            return _failure("test-source", exc)

    def run_test_target_connection(self, *, config: Path) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=False, include_target=True) as runtime:
                assert runtime.target is not None
                connected = runtime.target.test_connection()
            return CommandResult(
                command="test-target",
                success=connected,
                message=_connection_test_message("Target", runtime, "target", connected),
            )
        except _KNOWN_ERRORS as exc:
            return _failure("test-target", exc)

    def run_scan_tables(self, *, config: Path, schema_file: Path | None = None) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=False) as runtime:
                assert runtime.source is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_snapshot = self._load_target_snapshot(app_config, original_config)
            tables = tuple(_table_selection(table) for table in snapshot.tables)
            target_tables = tuple(_table_selection(table) for table in target_snapshot.tables)
            return CommandResult(
                command="scan-tables",
                success=True,
                message=f"tables={len(tables)}",
                table_count=len(tables),
                details={"tables": tables, "target_tables": target_tables},
            )
        except _KNOWN_ERRORS as exc:
            return _failure("scan-tables", exc)

    def run_table_preview(
        self,
        *,
        config: Path,
        table_identifier: str,
        schema_file: Path | None = None,
        target_schema: str | None = None,
        target_table: str | None = None,
        column_mappings: dict[str, str] | None = None,
        type_overrides: dict[str, str] | None = None,
        source_only_actions: dict[str, SourceOnlyColumnAction] | None = None,
        sample_size: int = 30,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                app_config = runtime.resolved
                original_config = runtime.original
                source_snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_snapshot = self._load_target_snapshot(app_config, original_config)
                preview_config = original_config.model_copy(deep=True)
                table_config = preview_config.tables.get(table_identifier, TableRunConfig())
                table_config.target_schema = target_schema or table_config.target_schema
                table_config.target_table = target_table or table_config.target_table
                _apply_preview_column_mappings(table_config, column_mappings or {}, type_overrides or {})
                table_config.source_only_columns = source_only_actions or table_config.source_only_columns
                preview_config.tables[table_identifier] = table_config
                selected_snapshot = _filter_snapshot(source_snapshot, {table_identifier})
                schema_plan = self._resolve_schema_plan(preview_config, source_snapshot=selected_snapshot, target_snapshot=target_snapshot)
                if not schema_plan.pairs:
                    return CommandResult(command="table-preview", success=False, message=f"table not found: {table_identifier}")
                plan = schema_plan.pairs[0].column_plan
                read_columns = plan.read_columns or tuple(column.name for column in plan.source_table.columns if not column.is_generated)
                order_by = stable_order_columns(plan.source_table, read_columns)
                rows = plan.transform_rows(
                    runtime.source.sample_rows(plan.source_table.ref, read_columns, sample_size, order_by, SamplePosition.FIRST)
                )
                columns = plan.write_columns or tuple(rows[0].keys() if rows else ())
            return CommandResult(
                command="table-preview",
                success=True,
                message=f"preview_rows={len(rows)}",
                details={
                    "columns": columns,
                    "rows": rows,
                    "schema_origin": schema_plan.pairs[0].schema_origin.value,
                    "target_table": schema_plan.pairs[0].target_table.ref,
                },
            )
        except _KNOWN_ERRORS as exc:
            return _failure("table-preview", exc)

    def run_dry_run(
        self,
        *,
        config: Path | None = None,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                app_config = runtime.resolved
                report_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_snapshot = self._load_target_snapshot(app_config, report_config)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            schema_plan = self._resolve_schema_plan(report_config, source_snapshot=snapshot, target_snapshot=target_snapshot)
            report = build_dry_run_report(
                schema_plan.target_snapshot,
                source_snapshot=schema_plan.source_snapshot,
                source_dbms=report_config.source.dbms,
                target_dbms=report_config.target.dbms,
                target_database=report_config.target.database,
                metadata=_dry_run_metadata(report_config),
                registry=self._registry,
                app_config=report_config,
                column_plans=_column_plan_by_source_key(schema_plan),
                schema_origins=_schema_origin_by_source_key(schema_plan),
            )
            resolved_output_dir = output_dir or Path(report_config.report.output_dir)
            write_dry_run_report(report, resolved_output_dir)
            return CommandResult(
                command="dry-run",
                success=True,
                message=f"dry_run_report={resolved_output_dir} tables={report.table_count} warnings={report.warning_count}",
                output_dir=resolved_output_dir,
                report_html=resolved_output_dir / "summary.html",
                table_count=report.table_count,
                warning_count=report.warning_count,
            )
        except _KNOWN_ERRORS as exc:
            return _failure("dry-run", exc)

    def run_apply_ddl(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_file: Path | None = None,
        dry_run_report_path: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                safety_config = runtime.original
                if dry_run_report_path is not None:
                    safety_config.migration.dry_run_report_path = str(dry_run_report_path)
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, safety_config)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                schema_plan = self._resolve_schema_plan(safety_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
                execution_plan = build_existing_table_execution_plan(
                    schema_plan,
                    safety_config.migration.existing_table_policy,
                    include_target_only_sync=selected_tables is None,
                )
                resolved_output_file = output_file or Path(safety_config.report.output_dir) / "ddl-execution.json"
                summary = execute_schema_ddl(
                    config=safety_config,
                    snapshot=execution_plan.ddl_snapshot,
                    executor=runtime.target,
                    report_output_path=resolved_output_file,
                    registry=self._registry,
                    column_plans=execution_plan.column_plans,
                    execution_plan=execution_plan,
                )
            failed_count = sum(1 for table in summary.tables if not table.success)
            return CommandResult(
                command="apply-ddl",
                success=failed_count == 0,
                message=f"ddl_execution_report={resolved_output_file} tables={len(summary.tables)} warnings={len(summary.warnings)} failed={failed_count}",
                output_file=resolved_output_file,
                table_count=len(summary.tables),
                warning_count=len(summary.warnings) + failed_count,
                details={"allowed": summary.allowed, "blocking_reasons": summary.blocking_reasons},
            )
        except _KNOWN_ERRORS as exc:
            return _failure("apply-ddl", exc)

    def run_apply_indexes(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_file: Path | None = None,
        phase: IndexApplyTiming = IndexApplyTiming.POST_DATA,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, original_config)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
                resolved_output_file = output_file or Path(original_config.report.output_dir) / f"index-execution-{phase.value}.json"
                summary = execute_index_ddls(
                    config=original_config,
                    snapshot=schema_plan.target_snapshot,
                    executor=runtime.target,
                    phase=phase,
                    report_output_path=resolved_output_file,
                )
            failed_count = sum(1 for result in summary.indexes if not result.success)
            return CommandResult(
                command="apply-indexes",
                success=failed_count == 0,
                message=(
                    f"index_execution_report={resolved_output_file} phase={phase.value} "
                    f"indexes={len(summary.indexes)} failed={failed_count}"
                ),
                output_file=resolved_output_file,
                table_count=len({(result.schema, result.table) for result in summary.indexes}),
                warning_count=failed_count,
                details={"phase": phase.value, "decisions": summary.decisions, "indexes": summary.indexes},
            )
        except _KNOWN_ERRORS as exc:
            return _failure("apply-indexes", exc)

    def run_apply_foreign_keys(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_file: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, original_config)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
                foreign_key_ddls = generate_foreign_key_ddls(schema_plan.target_snapshot, target_dbms=original_config.target.dbms)
                results = execute_foreign_key_ddls(ddls=foreign_key_ddls, executor=runtime.target)
                resolved_output_file = output_file or Path(original_config.report.output_dir) / "foreign-key-execution.json"
                _write_foreign_key_execution_report(foreign_key_ddls, results, resolved_output_file)
            failed_count = sum(1 for result in results if not result.success)
            return CommandResult(
                command="apply-foreign-keys",
                success=failed_count == 0,
                message=f"foreign_key_execution_report={resolved_output_file} foreign_keys={len(results)} failed={failed_count}",
                output_file=resolved_output_file,
                table_count=len({result.table for result in results}),
                warning_count=failed_count,
                details={"foreign_keys": results},
            )
        except _KNOWN_ERRORS + (ValueError, OSError) as exc:
            return _failure("apply-foreign-keys", exc)

    def run_generate_manual_ddl(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, original_config)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
            generator = self._registry.create_ddl_generator(original_config.target.dbms, target_database=original_config.target.database)
            statements = tuple(generator.generate_create_table(table).ddl for table in schema_plan.target_snapshot.tables)
            sql = "\n\n".join(statements)
            resolved_output_dir = output_dir or Path(original_config.report.output_dir)
            output_file = resolved_output_dir / "manual-ddl.sql"
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(sql, encoding="utf-8")
            return CommandResult(
                command="generate-manual-ddl",
                success=True,
                message=f"manual_ddl={output_file} tables={len(statements)}",
                output_file=output_file,
                table_count=len(statements),
                details={"sql": sql},
            )
        except _KNOWN_ERRORS + (OSError,) as exc:
            return _failure("generate-manual-ddl", exc)

    def run_generate_manual_migration(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, original_config)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
                generator = self._registry.create_ddl_generator(original_config.target.dbms, target_database=original_config.target.database)
                statements = tuple(generator.generate_create_table(table).ddl for table in schema_plan.target_snapshot.tables)
                ddl_sql = "\n\n".join(statements)
                resolved_output_dir = output_dir or Path(original_config.report.output_dir)
                export = export_manual_migration_files(
                    source=runtime.source,
                    source_tables=schema_plan.source_snapshot.tables,
                    target_tables=schema_plan.target_snapshot.tables,
                    target_dbms=original_config.target.dbms,
                    target_database=original_config.target.database,
                    migration_config=original_config.migration,
                    ddl_sql=ddl_sql,
                    output_dir=resolved_output_dir,
                    column_plans=schema_plan.column_plans,
                )
            return CommandResult(
                command="generate-manual-migration",
                success=True,
                message=(
                    f"manual_migration={export.ddl_file.parent} tables={len(export.tables)} "
                    f"rows_exported={export.rows_exported}"
                ),
                output_dir=export.ddl_file.parent,
                output_file=export.load_sql_file,
                table_count=len(export.tables),
                rows_written=export.rows_exported,
                details={"ddl_file": export.ddl_file, "load_sql_file": export.load_sql_file, "tables": export.tables},
            )
        except _KNOWN_ERRORS + (OSError, ValueError) as exc:
            return _failure("generate-manual-migration", exc)

    def run_migrate_data(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        checkpoint_db: Path = Path("checkpoints/migration.sqlite"),
        event_publisher: EventPublisher,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        return self._run_checkpointed_migration(
            command="migrate-data",
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=event_publisher,
            retry_failed_only=None,
            selected_tables=selected_tables,
        )

    def run_resume(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        checkpoint_db: Path = Path("checkpoints/migration.sqlite"),
        event_publisher: EventPublisher,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        return self._run_checkpointed_migration(
            command="resume",
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=event_publisher,
            retry_failed_only=False,
            selected_tables=selected_tables,
        )

    def run_retry_failed(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        checkpoint_db: Path = Path("checkpoints/migration.sqlite"),
        event_publisher: EventPublisher,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        return self._run_checkpointed_migration(
            command="retry-failed",
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=event_publisher,
            retry_failed_only=True,
            selected_tables=selected_tables,
        )

    def run_validate(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                original_config = runtime.original
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                actual_target_snapshot = self._load_target_snapshot(app_config, original_config)
                schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=actual_target_snapshot)
                resolved_output_dir = output_dir or Path(original_config.report.output_dir)
                report = validate_tables(
                    job_id=original_config.job.name,
                    tables=schema_plan.source_snapshot.tables,
                    reader=SourceTargetValidationReader(runtime.source, runtime.target, schema_plan.column_plans),
                    verification=original_config.verification,
                    metadata=_validation_metadata(original_config),
                    target_table_resolver=lambda table: schema_plan.column_plans[table].target_table.ref,
                    source_snapshot=schema_plan.target_snapshot,
                    target_snapshot=actual_target_snapshot,
                    execution_artifacts=load_execution_artifacts(resolved_output_dir),
                )
                write_validation_report(report, resolved_output_dir)
            return CommandResult(
                command="validate",
                success=True,
                message=f"validation_report={resolved_output_dir} status={report.status} tables={len(report.tables)}",
                job_id=app_config.job.name,
                status=report.status,
                output_dir=resolved_output_dir,
                report_html=resolved_output_dir / "summary.html",
                table_count=len(report.tables),
            )
        except _KNOWN_ERRORS as exc:
            return _failure("validate", exc)

    def run_incremental(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                original_config = runtime.original
                if not original_config.incremental.enabled:
                    return CommandResult(
                        command="migrate-incremental",
                        success=False,
                        message="incremental.enabled must be true to run migrate-incremental.",
                    )
                resolver = TableMappingResolver(original_config)
                original_config.incremental.watermarks = resolver.incremental_watermarks()
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                report = migrate_incremental_tables(
                    job_id=original_config.job.name,
                    tables=snapshot.tables,
                    source=runtime.source,
                    target=TargetMappingAdapter(runtime.target, resolver),
                    migration_config=original_config.migration,
                    incremental_config=original_config.incremental,
                    target_table_resolver=resolver.target_ref_for,
                )
                resolved_output_dir = output_dir or Path(original_config.report.output_dir)
                write_incremental_report(report, resolved_output_dir)
            return CommandResult(
                command="migrate-incremental",
                success=True,
                message=f"incremental_report={resolved_output_dir} tables={len(report.tables)} rows_upserted={report.rows_upserted}",
                job_id=original_config.job.name,
                output_dir=resolved_output_dir,
                report_html=resolved_output_dir / "summary.html",
                table_count=len(report.tables),
                rows_upserted=report.rows_upserted,
            )
        except _KNOWN_ERRORS as exc:
            return _failure("migrate-incremental", exc)

    def _run_checkpointed_migration(
        self,
        *,
        command: str,
        config: Path,
        schema_file: Path | None,
        checkpoint_db: Path,
        event_publisher: EventPublisher,
        retry_failed_only: bool | None,
        selected_tables: set[str] | None,
    ) -> CommandResult:
        try:
            with self._command_runtime(config, include_source=True, include_target=True) as runtime:
                assert runtime.source is not None
                assert runtime.target is not None
                app_config = runtime.resolved
                original_config = runtime.original
                checkpoint_store = CheckpointStore(checkpoint_db)
                snapshot = self._load_snapshot(app_config.source.schema_name, runtime.source, schema_file, source_dbms=app_config.source.dbms)
                target_scan_snapshot = self._load_target_snapshot(app_config, original_config)
                snapshot = _filter_snapshot(snapshot, selected_tables)
                schema_plan = self._resolve_schema_plan(original_config, source_snapshot=snapshot, target_snapshot=target_scan_snapshot)
                execution_plan = build_existing_table_execution_plan(
                    schema_plan,
                    original_config.migration.existing_table_policy,
                    include_target_only_sync=selected_tables is None,
                )
                target = ColumnPlanTargetAdapter(runtime.target, execution_plan.column_plans)
                resume_plan = None
                if retry_failed_only is not None:
                    resume_plan = (
                        build_retry_failed_plan(original_config.job.name, checkpoint_store)
                        if retry_failed_only
                        else build_resume_plan(original_config.job.name, execution_plan.dml_source_tables, checkpoint_store)
                    )
                result = migrate_tables(
                    job_id=original_config.job.name,
                    tables=execution_plan.dml_source_tables,
                    source=runtime.source,
                    target=target,
                    checkpoint_store=checkpoint_store,
                    event_publisher=event_publisher,
                    migration_config=original_config.migration,
                    resume_plan=resume_plan,
                    column_plans=execution_plan.column_plans,
                )
            return CommandResult(
                command=command,
                success=True,
                message=f"job_id={result.job_id} tables={len(result.tables)} rows_written={result.rows_written}",
                job_id=result.job_id,
                table_count=len(result.tables),
                rows_written=result.rows_written,
                output_file=checkpoint_db,
            )
        except _KNOWN_ERRORS as exc:
            return _failure(command, exc)

    def _load_snapshot(
        self,
        schema_name: str,
        source: SourceAdapter,
        schema_file: Path | None,
        *,
        source_dbms: Dbms,
    ) -> SchemaSnapshot:
        if schema_file is not None:
            return load_schema_snapshot_from_json(schema_file, source_dbms=source_dbms)
        return source.scan_schema(schema_name)

    def _load_target_snapshot(self, app_config: AppConfig, original_config: AppConfig) -> SchemaSnapshot:
        target_schema_reader = self._registry.create_source(_target_schema_scan_config(app_config, original_config))
        return target_schema_reader.scan_schema(_target_schema_name(original_config))

    def _resolve_schema_plan(
        self,
        config: AppConfig,
        *,
        source_snapshot: SchemaSnapshot,
        target_snapshot: SchemaSnapshot | None,
    ) -> SchemaPairPlan:
        return SchemaPairResolver(config, target_schema_name=_target_schema_name(config)).resolve(
            source_snapshot=source_snapshot,
            target_snapshot=target_snapshot,
        )

    @contextmanager
    def _command_runtime(
        self,
        config_path: Path | None,
        *,
        include_source: bool,
        include_target: bool,
    ) -> Iterator[CommandRuntime]:
        app_config = load_config(config_path)
        source: SourceAdapter | None = None
        target: TargetAdapter | None = None
        with self._tunnel_manager.open(app_config, include_source=include_source, include_target=include_target) as resolved_config:
            try:
                if include_source:
                    source = self._registry.create_source(resolved_config.resolved.source)
                if include_target:
                    target = self._registry.create_target(resolved_config.resolved.target)
                yield CommandRuntime(config=resolved_config, source=source, target=target)
            finally:
                _close_adapter(target)


_KNOWN_ERRORS = (
    ConfigLoadError,
    SchemaSnapshotLoadError,
    PostgresAdapterError,
    MySqlAdapterError,
    AdapterRegistryError,
    DdlExecutionBlocked,
    TunnelError,
)


def _failure(command: str, exc: Exception) -> CommandResult:
    return CommandResult(command=command, success=False, message=str(exc))


def _connection_test_message(label: str, runtime: CommandRuntime, side: str, connected: bool) -> str:
    config = runtime.resolved.source if side == "source" else runtime.resolved.target
    original = runtime.original.source if side == "source" else runtime.original.target
    status = "succeeded" if connected else "failed"
    if original.tunnel.enabled:
        remote_host = original.tunnel.remote_host or original.host
        remote_port = original.tunnel.remote_port or original.port
        return (
            f"{label} connection {status}. "
            f"db_endpoint={remote_host}:{remote_port} "
            f"tunnel_local_endpoint={config.host}:{config.port}."
        )
    return f"{label} connection {status}. db_endpoint={config.host}:{config.port}."


def _close_adapter(adapter: object | None) -> None:
    close = getattr(adapter, "close", None)
    if callable(close):
        close()


def _filter_snapshot(snapshot: SchemaSnapshot, selected_tables: set[str] | None) -> SchemaSnapshot:
    if selected_tables is None:
        return snapshot
    return SchemaSnapshot(tables=tuple(table for table in snapshot.tables if _table_identifier(table) in selected_tables))


def _column_plan_by_source_key(schema_plan: SchemaPairPlan):
    return {_table_identifier(pair.source_table): pair.column_plan for pair in schema_plan.pairs}


def _apply_preview_column_mappings(table_config: TableRunConfig, column_mappings: dict[str, str], type_overrides: dict[str, str]) -> None:
    for source_column, target_column in {**{column: column for column in type_overrides}, **column_mappings}.items():
        if not source_column or not target_column:
            continue
        existing = table_config.columns.get(target_column)
        target_type = type_overrides.get(source_column)
        table_config.columns[target_column] = (
            existing.model_copy(update={"source": source_column, "target_type": target_type or existing.target_type})
            if existing is not None
            else ColumnTransformConfig(source=source_column, target_type=target_type)
        )


def _schema_origin_by_source_key(schema_plan: SchemaPairPlan):
    return {_table_identifier(pair.source_table): pair.schema_origin for pair in schema_plan.pairs}


def _write_foreign_key_execution_report(foreign_key_ddls, results, output_file: Path) -> None:
    ddl_by_key = {(ddl.table, ddl.constraint_name): ddl.ddl for ddl in foreign_key_ddls}
    payload = {
        "foreign_keys": [
            {
                **asdict(result),
                "ddl": ddl_by_key.get((result.table, result.constraint_name)),
            }
            for result in results
        ]
    }
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _table_selection(table: TableSchema) -> TableSelection:
    primary_key_columns = set(table.primary_key.columns if table.primary_key is not None else ())
    return TableSelection(
        identifier=_table_identifier(table),
        schema=table.ref.schema,
        table=table.ref.name,
        column_count=len(table.columns),
        estimated_rows=table.estimated_rows,
        has_primary_key=table.primary_key is not None and bool(table.primary_key.columns),
        columns=tuple(
            ColumnSelection(
                name=column.name,
                source_type=column.source_type,
                common_type=column.common_type,
                nullable=column.nullable,
                primary_key=column.name in primary_key_columns,
            )
            for column in table.columns
        ),
    )


def _table_identifier(table: TableSchema) -> str:
    return f"{table.ref.schema}.{table.ref.name}"


def _drain_events(event_queue: Queue[MigrationEvent]) -> tuple[MigrationEvent, ...]:
    events: list[MigrationEvent] = []
    while not event_queue.empty():
        events.append(event_queue.get())
    return tuple(events)


def _dry_run_metadata(app_config: AppConfig) -> DryRunMetadata:
    return DryRunMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source=ReportEndpoint(
            dbms=app_config.source.dbms.value,
            host=app_config.source.host,
            port=app_config.source.port,
            database=app_config.source.database,
            schema=app_config.source.schema_name,
        ),
        target=ReportEndpoint(
            dbms=app_config.target.dbms.value,
            host=app_config.target.host,
            port=app_config.target.port,
            database=app_config.target.database,
            schema=app_config.target.schema_name,
        ),
        migration_mode=app_config.migration.mode.value,
        existing_table_policy=app_config.migration.existing_table_policy.value,
    )


def _validation_metadata(app_config: AppConfig) -> ValidationMetadata:
    return ValidationMetadata(
        generated_at=datetime.now(timezone.utc).isoformat(),
        source=ValidationEndpoint(
            dbms=app_config.source.dbms.value,
            host=app_config.source.host,
            port=app_config.source.port,
            database=app_config.source.database,
            schema=app_config.source.schema_name,
        ),
        target=ValidationEndpoint(
            dbms=app_config.target.dbms.value,
            host=app_config.target.host,
            port=app_config.target.port,
            database=app_config.target.database,
            schema=app_config.target.schema_name,
        ),
        migration_mode=app_config.migration.mode.value,
        existing_table_policy=app_config.migration.existing_table_policy.value,
        checksum_sample_size=app_config.verification.checksum_sample_size,
        checksum_timezone=app_config.verification.checksum_timezone,
        checksum_datetime_precision=app_config.verification.checksum_datetime_precision,
    )


def _target_schema_scan_config(app_config: AppConfig, original_config: AppConfig | None = None) -> SourceConfig:
    metadata_config = original_config or app_config
    return SourceConfig(
        dbms=app_config.target.dbms,
        host=app_config.target.host,
        port=app_config.target.port,
        database=metadata_config.target.database,
        schema=_target_schema_name(metadata_config),
        user=metadata_config.target.user,
        password=metadata_config.target.password,
    )


def _target_schema_name(app_config: AppConfig) -> str:
    if app_config.target.schema_name:
        return app_config.target.schema_name
    if app_config.target.dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return app_config.target.database
    return app_config.source.schema_name
