from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

import typer
from rich.console import Console

from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.adapters.postgres import PostgresAdapterError
from db_migrator.adapters.mysql import MySqlAdapterError
from db_migrator.adapters.base import SourceAdapter
from db_migrator.adapters.registry import AdapterRegistryError, default_adapter_registry
from db_migrator.cli.console_events import ConsoleEventPublisher
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.core.dml_migration import build_resume_plan, build_retry_failed_plan, migrate_tables
from db_migrator.core.engine import MigrationEngine
from db_migrator.core.events import MigrationEvent, QueueEventPublisher
from db_migrator.core.health import run_health_checks
from db_migrator.core.incremental import migrate_incremental_tables
from db_migrator.core.validation import ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.core.validation_readers import SourceTargetValidationReader
from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.final_report import write_validation_report
from db_migrator.reports.incremental_report import write_incremental_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.config.models import Dbms
from db_migrator.schema.models import SchemaSnapshot
from db_migrator.schema.snapshot_io import SchemaSnapshotLoadError, load_schema_snapshot_from_json
from db_migrator.selftest.package_check import check_pyinstaller_available
from db_migrator.selftest.runner import run_self_test

app = typer.Typer(help="Safe DB migration helper.")
_self_test_app = typer.Typer(help="Optional Docker-based self-test.")
app.add_typer(_self_test_app, name="self-test")
console = Console()
_registry = default_adapter_registry()


@app.callback()
def main() -> None:
    """DB migration CLI."""


@app.command()
def bootstrap(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    event_queue: Queue[MigrationEvent] = Queue()

    try:
        app_config = load_config(config)
    except ConfigLoadError as exc:
        raise typer.BadParameter(str(exc)) from exc

    result = MigrationEngine(QueueEventPublisher(event_queue)).run_dry_bootstrap(app_config)

    while not event_queue.empty():
        event = event_queue.get()
        console.print(f"[{event.level}] {event.message}")

    console.print(f"job_id={result.job_id} status={result.status}")


@app.command("dry-run")
def dry_run(
    config: Path | None = typer.Option(None, "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    try:
        app_config = load_config(config)
        source = _registry.create_source(app_config.source)
        if schema_file is not None:
            snapshot = load_schema_snapshot_from_json(schema_file, source_dbms=app_config.source.dbms)
        else:
            snapshot = source.scan_schema(app_config.source.schema_name)
        report = build_dry_run_report(
            snapshot,
            target_dbms=app_config.target.dbms,
            target_database=app_config.target.database,
            metadata=DryRunMetadata(
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
                ),
                migration_mode=app_config.migration.mode.value,
                existing_table_policy=app_config.migration.existing_table_policy.value,
            ),
            registry=_registry,
        )
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    resolved_output_dir = output_dir or Path(app_config.report.output_dir)
    write_dry_run_report(report, resolved_output_dir)
    console.print(
        f"dry_run_report={resolved_output_dir} tables={report.table_count} warnings={report.warning_count}"
    )


@app.command("apply-ddl")
def apply_ddl(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_file: Path | None = typer.Option(None, "--output-file"),
) -> None:
    try:
        app_config = load_config(config)
        source = _registry.create_source(app_config.source)
        target = _registry.create_target(app_config.target)
        snapshot = _load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
        resolved_output_file = output_file or Path(app_config.report.output_dir) / "ddl-execution.json"
        summary = execute_schema_ddl(
            config=app_config,
            snapshot=snapshot,
            executor=target,
            report_output_path=resolved_output_file,
            registry=_registry,
        )
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    except DdlExecutionBlocked as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(
        f"ddl_execution_report={resolved_output_file} tables={len(summary.tables)} warnings={len(summary.warnings)}"
    )


@app.command("migrate-data")
def migrate_data(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    try:
        app_config = load_config(config)
        source = _registry.create_source(app_config.source)
        target = _registry.create_target(app_config.target)
        snapshot = _load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
        result = migrate_tables(
            job_id=app_config.job.name,
            tables=snapshot.tables,
            source=source,
            target=target,
            checkpoint_store=CheckpointStore(checkpoint_db),
            event_publisher=ConsoleEventPublisher(console),
            migration_config=app_config.migration,
        )
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"job_id={result.job_id} tables={len(result.tables)} rows_written={result.rows_written}")


@app.command("resume")
def resume(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    _run_checkpointed_migration(
        config=config,
        schema_file=schema_file,
        checkpoint_db=checkpoint_db,
        retry_failed_only=False,
    )


@app.command("retry-failed")
def retry_failed(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    _run_checkpointed_migration(
        config=config,
        schema_file=schema_file,
        checkpoint_db=checkpoint_db,
        retry_failed_only=True,
    )


@app.command("validate")
def validate(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    try:
        app_config = load_config(config)
        source = _registry.create_source(app_config.source)
        target = _registry.create_target(app_config.target)
        snapshot = _load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
        report = validate_tables(
            job_id=app_config.job.name,
            tables=snapshot.tables,
            reader=SourceTargetValidationReader(source, target),
            verification=app_config.verification,
            metadata=ValidationMetadata(
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
                ),
                checksum_sample_size=app_config.verification.checksum_sample_size,
                checksum_timezone=app_config.verification.checksum_timezone,
                checksum_datetime_precision=app_config.verification.checksum_datetime_precision,
            ),
        )
        resolved_output_dir = output_dir or Path(app_config.report.output_dir)
        write_validation_report(report, resolved_output_dir)
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"validation_report={resolved_output_dir} status={report.status} tables={len(report.tables)}")


@app.command("migrate-incremental")
def migrate_incremental(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    try:
        app_config = load_config(config)
        if not app_config.incremental.enabled:
            raise typer.BadParameter("incremental.enabled must be true to run migrate-incremental.")
        source = _registry.create_source(app_config.source)
        target = _registry.create_target(app_config.target)
        snapshot = _load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
        report = migrate_incremental_tables(
            job_id=app_config.job.name,
            tables=snapshot.tables,
            source=source,
            target=target,
            migration_config=app_config.migration,
            incremental_config=app_config.incremental,
        )
        resolved_output_dir = output_dir or Path(app_config.report.output_dir)
        write_incremental_report(report, resolved_output_dir)
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"incremental_report={resolved_output_dir} tables={len(report.tables)} rows_upserted={report.rows_upserted}")


@_self_test_app.command("run")
def self_test_run(
    compose_file: Path | None = typer.Option(None, "--compose-file"),
    scenario: str = typer.Option("pg_to_mariadb", "--scenario"),
    large_rows: int = typer.Option(100_000, "--large-rows", min=0),
    keep_containers: bool = typer.Option(False, "--keep-containers"),
    work_dir: Path = typer.Option(Path(".tmp/selftest"), "--work-dir"),
) -> None:
    result = run_self_test(
        compose_file,
        scenario=scenario,
        large_rows=large_rows,
        keep_containers=keep_containers,
        work_dir=work_dir,
        event_publisher=ConsoleEventPublisher(console),
    )
    if not result.success:
        raise typer.BadParameter(result.message)
    console.print(result.message)


@app.command("package-check")
def package_check() -> None:
    result = check_pyinstaller_available()
    if not result.success:
        raise typer.BadParameter(result.message)
    console.print(result.message)


@app.command("doctor")
def doctor(project_root: Path = typer.Option(Path("."), "--project-root")) -> None:
    report = run_health_checks(project_root.resolve())
    for check in report.checks:
        console.print(f"{check.status.upper()} {check.name}: {check.message}")
    if not report.ok:
        raise typer.Exit(code=1)


def _run_checkpointed_migration(
    *,
    config: Path,
    schema_file: Path | None,
    checkpoint_db: Path,
    retry_failed_only: bool,
) -> None:
    try:
        app_config = load_config(config)
        source = _registry.create_source(app_config.source)
        target = _registry.create_target(app_config.target)
        checkpoint_store = CheckpointStore(checkpoint_db)
        snapshot = _load_snapshot(app_config.source.schema_name, source, schema_file, source_dbms=app_config.source.dbms)
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
            event_publisher=ConsoleEventPublisher(console),
            migration_config=app_config.migration,
            resume_plan=resume_plan,
        )
    except (ConfigLoadError, SchemaSnapshotLoadError, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        raise typer.BadParameter(str(exc)) from exc

    console.print(f"job_id={result.job_id} tables={len(result.tables)} rows_written={result.rows_written}")


def _load_snapshot(schema_name: str, source: SourceAdapter, schema_file: Path | None, *, source_dbms: Dbms) -> SchemaSnapshot:
    if schema_file is not None:
        return load_schema_snapshot_from_json(schema_file, source_dbms=source_dbms)
    return source.scan_schema(schema_name)
