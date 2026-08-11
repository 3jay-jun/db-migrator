from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

from db_migrator.adapters.base import SourceAdapter
from db_migrator.adapters.mysql import MySqlAdapterError
from db_migrator.adapters.postgres import PostgresAdapterError
from db_migrator.adapters.registry import AdapterRegistryError, DbmsAdapterRegistry, default_adapter_registry
from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import AppConfig, Dbms, IndexApplyTiming, SourceConfig
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.core.dml_migration import build_resume_plan, build_retry_failed_plan, migrate_tables
from db_migrator.core.engine import MigrationEngine
from db_migrator.core.events import EventPublisher, MigrationEvent, QueueEventPublisher
from db_migrator.core.health import run_health_checks
from db_migrator.core.incremental import migrate_incremental_tables
from db_migrator.core.indexes import execute_index_ddls
from db_migrator.core.manual_migration import export_manual_migration_files
from db_migrator.core.validation import ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.core.validation_readers import SourceTargetValidationReader
from db_migrator.application.table_mapping import TargetMappingAdapter
from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.final_report import write_validation_report
from db_migrator.reports.incremental_report import write_incremental_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.models import ForeignKeySchema, SchemaObjectSummary, SchemaSnapshot, TableRef, TableSchema
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
class TableSelection:
    identifier: str
    schema: str
    table: str
    column_count: int
    estimated_rows: int | None
    has_primary_key: bool


class MigrationApplicationService:
    def __init__(self, registry: DbmsAdapterRegistry | None = None) -> None:
        self._registry = registry or default_adapter_registry()

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
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            connected = source.test_connection()
            return CommandResult(
                command="test-source",
                success=connected,
                message="Source connection succeeded." if connected else "Source connection failed.",
            )
        except _KNOWN_ERRORS as exc:
            return _failure("test-source", exc)

    def run_test_target_connection(self, *, config: Path) -> CommandResult:
        try:
            app_config = load_config(config)
            target = self._registry.create_target(app_config.target)
            connected = target.test_connection()
            return CommandResult(
                command="test-target",
                success=connected,
                message="Target connection succeeded." if connected else "Target connection failed.",
            )
        except _KNOWN_ERRORS as exc:
            return _failure("test-target", exc)

    def run_scan_tables(self, *, config: Path, schema_file: Path | None = None) -> CommandResult:
        try:
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            tables = tuple(_table_selection(table) for table in snapshot.tables)
            return CommandResult(
                command="scan-tables",
                success=True,
                message=f"tables={len(tables)}",
                table_count=len(tables),
                details={"tables": tables},
            )
        except _KNOWN_ERRORS as exc:
            return _failure("scan-tables", exc)

    def run_dry_run(
        self,
        *,
        config: Path | None = None,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
            report = build_dry_run_report(
                target_snapshot,
                source_snapshot=snapshot,
                source_dbms=app_config.source.dbms,
                target_dbms=app_config.target.dbms,
                target_database=app_config.target.database,
                metadata=_dry_run_metadata(app_config),
                registry=self._registry,
                app_config=app_config,
            )
            resolved_output_dir = output_dir or Path(app_config.report.output_dir)
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
            app_config = load_config(config)
            if dry_run_report_path is not None:
                app_config.migration.dry_run_report_path = str(dry_run_report_path)
            source = self._registry.create_source(app_config.source)
            target = self._registry.create_target(app_config.target)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
            resolved_output_file = output_file or Path(app_config.report.output_dir) / "ddl-execution.json"
            summary = execute_schema_ddl(
                config=app_config,
                snapshot=target_snapshot,
                executor=target,
                report_output_path=resolved_output_file,
                registry=self._registry,
            )
            return CommandResult(
                command="apply-ddl",
                success=True,
                message=f"ddl_execution_report={resolved_output_file} tables={len(summary.tables)} warnings={len(summary.warnings)}",
                output_file=resolved_output_file,
                table_count=len(summary.tables),
                warning_count=len(summary.warnings),
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
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            target = self._registry.create_target(app_config.target)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
            resolved_output_file = output_file or Path(app_config.report.output_dir) / f"index-execution-{phase.value}.json"
            summary = execute_index_ddls(
                config=app_config,
                snapshot=target_snapshot,
                executor=target,
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

    def run_generate_manual_ddl(
        self,
        *,
        config: Path,
        schema_file: Path | None = None,
        output_dir: Path | None = None,
        selected_tables: set[str] | None = None,
    ) -> CommandResult:
        try:
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
            generator = self._registry.create_ddl_generator(app_config.target.dbms, target_database=app_config.target.database)
            statements = tuple(generator.generate_create_table(table).ddl for table in target_snapshot.tables)
            sql = "\n\n".join(statements)
            resolved_output_dir = output_dir or Path(app_config.report.output_dir)
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
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
            generator = self._registry.create_ddl_generator(app_config.target.dbms, target_database=app_config.target.database)
            statements = tuple(generator.generate_create_table(table).ddl for table in target_snapshot.tables)
            ddl_sql = "\n\n".join(statements)
            resolved_output_dir = output_dir or Path(app_config.report.output_dir)
            export = export_manual_migration_files(
                source=source,
                source_tables=snapshot.tables,
                target_tables=target_snapshot.tables,
                target_dbms=app_config.target.dbms,
                target_database=app_config.target.database,
                migration_config=app_config.migration,
                ddl_sql=ddl_sql,
                output_dir=resolved_output_dir,
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
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            target = self._registry.create_target(app_config.target)
            target_schema_reader = self._registry.create_source(_target_schema_scan_config(app_config))
            resolver = TableMappingResolver(app_config)
            target = TargetMappingAdapter(target, resolver)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            expected_target_snapshot = _schema_scan_expected_snapshot(resolver.target_snapshot_for(snapshot), app_config)
            actual_target_snapshot = target_schema_reader.scan_schema(_target_schema_name(app_config))
            report = validate_tables(
                job_id=app_config.job.name,
                tables=snapshot.tables,
                reader=SourceTargetValidationReader(source, target),
                verification=app_config.verification,
                metadata=_validation_metadata(app_config),
                target_table_resolver=resolver.target_ref_for,
                source_snapshot=expected_target_snapshot,
                target_snapshot=actual_target_snapshot,
            )
            resolved_output_dir = output_dir or Path(app_config.report.output_dir)
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
            app_config = load_config(config)
            if not app_config.incremental.enabled:
                return CommandResult(
                    command="migrate-incremental",
                    success=False,
                    message="incremental.enabled must be true to run migrate-incremental.",
                )
            source = self._registry.create_source(app_config.source)
            target = self._registry.create_target(app_config.target)
            resolver = TableMappingResolver(app_config)
            app_config.incremental.watermarks = resolver.incremental_watermarks()
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            report = migrate_incremental_tables(
                job_id=app_config.job.name,
                tables=snapshot.tables,
                source=source,
                target=TargetMappingAdapter(target, resolver),
                migration_config=app_config.migration,
                incremental_config=app_config.incremental,
                target_table_resolver=resolver.target_ref_for,
            )
            resolved_output_dir = output_dir or Path(app_config.report.output_dir)
            write_incremental_report(report, resolved_output_dir)
            return CommandResult(
                command="migrate-incremental",
                success=True,
                message=f"incremental_report={resolved_output_dir} tables={len(report.tables)} rows_upserted={report.rows_upserted}",
                job_id=app_config.job.name,
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
            app_config = load_config(config)
            source = self._registry.create_source(app_config.source)
            target = self._registry.create_target(app_config.target)
            target = TargetMappingAdapter(target, TableMappingResolver(app_config))
            checkpoint_store = CheckpointStore(checkpoint_db)
            snapshot = self._load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
            snapshot = _filter_snapshot(snapshot, selected_tables)
            resume_plan = None
            if retry_failed_only is not None:
                resume_plan = (
                    build_retry_failed_plan(app_config.job.name, checkpoint_store)
                    if retry_failed_only
                    else build_resume_plan(app_config.job.name, snapshot.tables, checkpoint_store)
                )
            result = migrate_tables(
                job_id=app_config.job.name,
                tables=snapshot.tables,
                source=source,
                target=target,
                checkpoint_store=checkpoint_store,
                event_publisher=event_publisher,
                migration_config=app_config.migration,
                resume_plan=resume_plan,
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


_KNOWN_ERRORS = (
    ConfigLoadError,
    SchemaSnapshotLoadError,
    PostgresAdapterError,
    MySqlAdapterError,
    AdapterRegistryError,
    DdlExecutionBlocked,
)


def _failure(command: str, exc: Exception) -> CommandResult:
    return CommandResult(command=command, success=False, message=str(exc))


def _filter_snapshot(snapshot: SchemaSnapshot, selected_tables: set[str] | None) -> SchemaSnapshot:
    if selected_tables is None:
        return snapshot
    return SchemaSnapshot(tables=tuple(table for table in snapshot.tables if _table_identifier(table) in selected_tables))


def _table_selection(table: TableSchema) -> TableSelection:
    return TableSelection(
        identifier=_table_identifier(table),
        schema=table.ref.schema,
        table=table.ref.name,
        column_count=len(table.columns),
        estimated_rows=table.estimated_rows,
        has_primary_key=table.primary_key is not None and bool(table.primary_key.columns),
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


def _target_schema_scan_config(app_config: AppConfig) -> SourceConfig:
    return SourceConfig(
        dbms=app_config.target.dbms,
        host=app_config.target.host,
        port=app_config.target.port,
        database=app_config.target.database,
        schema=_target_schema_name(app_config),
        user=app_config.target.user,
        password=app_config.target.password,
    )


def _target_schema_name(app_config: AppConfig) -> str:
    if app_config.target.schema_name:
        return app_config.target.schema_name
    if app_config.target.dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return app_config.target.database
    return app_config.source.schema_name


def _schema_scan_expected_snapshot(snapshot: SchemaSnapshot, app_config: AppConfig) -> SchemaSnapshot:
    schema_name = _target_schema_name(app_config)
    return SchemaSnapshot(
        tables=tuple(_schema_scan_expected_table(table, schema_name) for table in snapshot.tables),
        non_table_objects=tuple(_schema_scan_expected_object(schema_object, schema_name) for schema_object in snapshot.non_table_objects),
    )


def _schema_scan_expected_table(table: TableSchema, schema_name: str) -> TableSchema:
    ref = TableRef(schema=schema_name, name=table.ref.name)
    return replace(
        table,
        ref=ref,
        foreign_keys=tuple(_schema_scan_expected_foreign_key(foreign_key, schema_name) for foreign_key in table.foreign_keys),
    )


def _schema_scan_expected_foreign_key(foreign_key: ForeignKeySchema, schema_name: str) -> ForeignKeySchema:
    return replace(foreign_key, referenced_table=TableRef(schema=schema_name, name=foreign_key.referenced_table.name))


def _schema_scan_expected_object(schema_object: SchemaObjectSummary, schema_name: str) -> SchemaObjectSummary:
    parent_table = (
        TableRef(schema=schema_name, name=schema_object.parent_table.name)
        if schema_object.parent_table is not None
        else None
    )
    return replace(schema_object, schema=schema_name, parent_table=parent_table)
