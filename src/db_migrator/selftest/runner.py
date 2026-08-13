from __future__ import annotations

import re
import shutil
import subprocess
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue
from typing import Any

import yaml

from db_migrator.adapters.mysql import MySqlAdapterError
from db_migrator.adapters.registry import AdapterRegistryError, default_adapter_registry
from db_migrator.adapters.postgres import PostgresAdapterError
from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import IndexApplyTiming
from db_migrator.core.checkpoint import CheckpointStore
from db_migrator.core.ddl_execution import DdlExecutionBlocked, execute_schema_ddl
from db_migrator.core.dml_migration import migrate_tables
from db_migrator.core.events import EventLevel, EventPublisher, EventType, MigrationEvent, QueueEventPublisher
from db_migrator.core.foreign_keys import execute_foreign_key_ddls, generate_foreign_key_ddls
from db_migrator.core.indexes import execute_index_ddls
from db_migrator.core.validation import ValidationEndpoint, ValidationMetadata, validate_tables
from db_migrator.core.validation import ValidationStatus
from db_migrator.core.validation_readers import SourceTargetValidationReader
from db_migrator.reports.dry_run import DryRunMetadata, build_dry_run_report, write_dry_run_report
from db_migrator.reports.final_report import write_validation_report
from db_migrator.reports.metadata import ReportEndpoint
from db_migrator.schema.dependency import plan_table_creation_order
from db_migrator.schema.models import SchemaSnapshot, TableSchema
from db_migrator.schema.table_mapping import TableMappingResolver


DOCKER_MISSING_MESSAGE = "Docker is not installed or not running. Self-test requires Docker Desktop."
DEFAULT_SCENARIO = "pg_to_mariadb"
DEFAULT_LARGE_ROWS = 100_000


@dataclass(frozen=True)
class SelfTestResult:
    success: bool
    message: str


@dataclass(frozen=True)
class SelfTestOptions:
    compose_file: Path | None = None
    scenario: str = DEFAULT_SCENARIO
    large_rows: int = DEFAULT_LARGE_ROWS
    keep_containers: bool = False
    project_name: str = "db-migrator-selftest"
    work_dir: Path = Path(".tmp/selftest")


@dataclass(frozen=True)
class SelfTestScenario:
    root: Path
    config_file: Path
    compose_file: Path
    compose_env: Mapping[str, str]
    source_schema_file: Path
    source_seed_file: Path
    source_service: str
    source_schema_command: tuple[str, ...]
    source_seed_command: tuple[str, ...]


def check_docker_available() -> SelfTestResult:
    docker_path = shutil.which("docker")
    if docker_path is None:
        return SelfTestResult(success=False, message=DOCKER_MISSING_MESSAGE)

    completed = subprocess.run(
        [docker_path, "info"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return SelfTestResult(success=False, message=DOCKER_MISSING_MESSAGE)

    return SelfTestResult(success=True, message="Docker is available.")


def run_self_test(
    compose_file: Path | None = None,
    *,
    scenario: str = DEFAULT_SCENARIO,
    large_rows: int = DEFAULT_LARGE_ROWS,
    keep_containers: bool = False,
    project_name: str = "db-migrator-selftest",
    work_dir: Path = Path(".tmp/selftest"),
    event_publisher: EventPublisher | None = None,
) -> SelfTestResult:
    options = SelfTestOptions(
        compose_file=compose_file,
        scenario=scenario,
        large_rows=large_rows,
        keep_containers=keep_containers,
        project_name=project_name,
        work_dir=work_dir,
    )
    docker_check = check_docker_available()
    if not docker_check.success:
        return docker_check

    scenario_paths: SelfTestScenario | None = None
    try:
        _publish_selftest_event(event_publisher, scenario, EventType.JOB_STARTED, "Self-test started.")
        scenario_paths = _load_scenario(options)
        _run_docker_scenario(options, scenario_paths, event_publisher=event_publisher)
        summary = _run_migration_flow(options, scenario_paths, event_publisher=event_publisher)
    except SelfTestError as exc:
        return SelfTestResult(success=False, message=str(exc))
    except (ConfigLoadError, DdlExecutionBlocked, PostgresAdapterError, MySqlAdapterError, AdapterRegistryError) as exc:
        return SelfTestResult(success=False, message=str(exc))
    finally:
        if scenario_paths is not None and not options.keep_containers:
            _docker_compose(options, scenario_paths, ("down", "-v"), check=False)

    return SelfTestResult(success=True, message=summary)


class SelfTestError(RuntimeError):
    pass


def _load_scenario(options: SelfTestOptions) -> SelfTestScenario:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", options.scenario):
        raise SelfTestError("scenario may contain only letters, numbers, underscores, and hyphens.")
    scenario_root = _scenario_root(options)
    manifest = _load_manifest(scenario_root)
    docker_manifest = _docker_manifest(manifest)
    config_file = _write_generated_config(options, manifest, docker_manifest)
    source_manifest = _source_manifest(manifest)
    compose_file = options.compose_file or scenario_root / str(manifest.get("compose_file", "../../docker-compose.yml"))
    scenario_paths = SelfTestScenario(
        root=scenario_root,
        config_file=config_file,
        compose_file=compose_file.resolve(),
        compose_env=_compose_env(options, docker_manifest),
        source_schema_file=scenario_root / str(source_manifest.get("schema_file", "schema.sql")),
        source_seed_file=scenario_root / str(source_manifest.get("seed_file", "seed.sql")),
        source_service=_required_str(source_manifest, "service"),
        source_schema_command=_required_command(source_manifest, "schema_command"),
        source_seed_command=_required_command(source_manifest, "seed_command"),
    )
    missing_paths = [
        path
        for path in (
            scenario_paths.config_file,
            scenario_paths.compose_file,
            scenario_paths.source_schema_file,
            scenario_paths.source_seed_file,
        )
        if not path.exists()
    ]
    if missing_paths:
        joined_paths = ", ".join(str(path) for path in missing_paths)
        raise SelfTestError(f"Self-test scenario is incomplete: {joined_paths}")
    if options.large_rows < 0:
        raise SelfTestError("large_rows must be greater than or equal to 0.")
    return scenario_paths


def _scenario_root(options: SelfTestOptions) -> Path:
    if options.compose_file is not None:
        scenario_parent = options.compose_file.parent
        if scenario_parent.name == options.scenario:
            return scenario_parent
        return scenario_parent / "scenarios" / options.scenario
    return Path("src/db_migrator/selftest/scenarios") / options.scenario


def _load_manifest(scenario_root: Path) -> dict[str, Any]:
    manifest_path = scenario_root / "selftest.yml"
    if not manifest_path.exists():
        raise SelfTestError(f"Self-test scenario manifest does not exist: {manifest_path}")
    raw_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw_manifest, dict):
        raise SelfTestError(f"Self-test scenario manifest must be a mapping: {manifest_path}")
    return raw_manifest


def _source_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    source_manifest = manifest.get("source_seed")
    if not isinstance(source_manifest, Mapping):
        raise SelfTestError("Self-test scenario manifest must define source_seed mapping.")
    return source_manifest


def _required_str(manifest: Mapping[str, Any], key: str) -> str:
    value = manifest.get(key)
    if not isinstance(value, str) or not value:
        raise SelfTestError(f"Self-test scenario manifest must define source.{key}.")
    return value


def _required_command(manifest: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = manifest.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SelfTestError(f"Self-test scenario manifest must define source_seed.{key} as a string list.")
    return tuple(value)


def _write_generated_config(
    options: SelfTestOptions,
    manifest: Mapping[str, Any],
    docker_manifest: Mapping[str, Any],
) -> Path:
    config_manifest = manifest.get("migration_config")
    if not isinstance(config_manifest, Mapping):
        raise SelfTestError("Self-test scenario manifest must define migration_config mapping.")
    source_docker = _required_mapping(docker_manifest, "source")
    target_docker = _required_mapping(docker_manifest, "target")
    generated_config = dict(config_manifest)
    generated_config["source"] = {
        "dbms": _required_str(source_docker, "dbms"),
        "host": "localhost",
        "port": _required_int(source_docker, "host_port"),
        "database": _required_str(source_docker, "database"),
        "schema": _required_str(source_docker, "schema"),
        "user": _required_str(source_docker, "user"),
        "password": _required_str(source_docker, "password"),
    }
    generated_config["target"] = {
        "dbms": _required_str(target_docker, "dbms"),
        "host": "localhost",
        "port": _required_int(target_docker, "host_port"),
        "database": _required_str(target_docker, "database"),
        "user": _required_str(target_docker, "user"),
        "password": _required_str(target_docker, "password"),
        "environment": _required_str(target_docker, "environment"),
    }
    config_file = options.work_dir / "generated-configs" / options.scenario / "config.yml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(yaml.safe_dump(generated_config, sort_keys=False), encoding="utf-8")
    return config_file


def _compose_env(
    options: SelfTestOptions,
    docker_manifest: Mapping[str, Any],
) -> Mapping[str, str]:
    source_env_file = _write_container_env_file(
        options,
        "source",
        _required_mapping(docker_manifest, "source"),
    )
    target_env_file = _write_container_env_file(
        options,
        "target",
        _required_mapping(docker_manifest, "target"),
    )
    return {
        **_compose_role_env("SOURCE", _required_mapping(docker_manifest, "source"), source_env_file),
        **_compose_role_env("TARGET", _required_mapping(docker_manifest, "target"), target_env_file),
    }


def _docker_manifest(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    docker_manifest = manifest.get("docker")
    if not isinstance(docker_manifest, Mapping):
        raise SelfTestError("Self-test scenario manifest must define docker mapping.")
    return docker_manifest


def _compose_role_env(
    role: str,
    manifest: Mapping[str, Any],
    env_file: Path,
) -> Mapping[str, str]:
    return {
        f"SELFTEST_{role}_IMAGE": _required_str(manifest, "image"),
        f"SELFTEST_{role}_HOST_PORT": str(_required_int(manifest, "host_port")),
        f"SELFTEST_{role}_CONTAINER_PORT": str(_required_int(manifest, "container_port")),
        f"SELFTEST_{role}_HEALTHCHECK": _required_str(manifest, "healthcheck"),
        f"SELFTEST_{role}_ENV_FILE": str(env_file.resolve()),
    }


def _write_container_env_file(
    options: SelfTestOptions,
    role: str,
    manifest: Mapping[str, Any],
) -> Path:
    environment = manifest.get("container_environment", manifest.get("environment"))
    if not isinstance(environment, Mapping):
        raise SelfTestError("Self-test docker container_environment must be a mapping.")
    env_file = options.work_dir / "generated-compose-envs" / options.scenario / f"{role}.env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for key, value in environment.items():
        if not isinstance(key, str) or not key:
            raise SelfTestError("Self-test docker environment must contain non-empty string keys.")
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return env_file


def _required_mapping(manifest: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = manifest.get(key)
    if not isinstance(value, Mapping):
        raise SelfTestError(f"Self-test scenario manifest must define {key} mapping.")
    return value


def _required_int(manifest: Mapping[str, Any], key: str) -> int:
    value = manifest.get(key)
    if not isinstance(value, int):
        raise SelfTestError(f"Self-test scenario manifest must define {key} as an integer.")
    return value


def _run_docker_scenario(
    options: SelfTestOptions,
    scenario: SelfTestScenario,
    *,
    event_publisher: EventPublisher | None = None,
) -> None:
    _publish_selftest_event(event_publisher, options.scenario, EventType.PLAN_CREATED, "Checking Docker compose config.")
    _docker_compose(options, scenario, ("config",))
    _docker_compose(options, scenario, ("down", "-v"), check=False)
    _publish_selftest_event(event_publisher, options.scenario, EventType.JOB_STARTED, "Starting Docker containers.")
    _docker_compose(options, scenario, ("up", "-d", "--wait"))
    _publish_selftest_event(event_publisher, options.scenario, EventType.PLAN_CREATED, "Loading source schema seed.")
    _run_source_sql(
        options,
        scenario,
        scenario.source_schema_file,
        command_template=scenario.source_schema_command,
    )
    _publish_selftest_event(event_publisher, options.scenario, EventType.PLAN_CREATED, f"Loading source data seed. large_rows={options.large_rows}")
    _run_source_sql(
        options,
        scenario,
        scenario.source_seed_file,
        variables={"large_rows": str(options.large_rows)},
        command_template=scenario.source_seed_command,
    )


def _run_migration_flow(
    options: SelfTestOptions,
    scenario: SelfTestScenario,
    event_publisher: EventPublisher | None = None,
) -> str:
    registry = default_adapter_registry()
    app_config = load_config(scenario.config_file)
    source = registry.create_source(app_config.source)
    target = registry.create_target(app_config.target)
    _publish_selftest_event(event_publisher, app_config.job.name, EventType.CONNECTION_TESTED, "Scanning source schema.")
    snapshot = source.scan_schema(app_config.source.schema_name)
    target_snapshot = TableMappingResolver(app_config).target_snapshot_for(snapshot)
    ordered_tables = _dependency_ordered_tables(snapshot.tables)
    _publish_selftest_event(
        event_publisher,
        app_config.job.name,
        EventType.SCHEMA_SCANNED,
        f"Source schema scanned. tables={len(ordered_tables)}",
    )

    report_root = options.work_dir / "reports"
    checkpoint_db = options.work_dir / "checkpoints" / "selftest.sqlite"
    options.work_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_db.unlink(missing_ok=True)

    dry_run_report = build_dry_run_report(
        snapshot,
        source_snapshot=snapshot,
        source_dbms=app_config.source.dbms,
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
        registry=registry,
    )
    write_dry_run_report(dry_run_report, report_root / "dry-run")
    _publish_selftest_event(event_publisher, app_config.job.name, EventType.PLAN_CREATED, f"Dry-run report written: {report_root / 'dry-run'}")

    execute_schema_ddl(
        config=app_config,
        snapshot=snapshot,
        executor=target,
        report_output_path=report_root / "ddl-execution.json",
        registry=registry,
    )
    _publish_selftest_event(event_publisher, app_config.job.name, EventType.PLAN_CREATED, "Target DDL executed.")

    event_queue: Queue[MigrationEvent] = Queue()
    dml_event_publisher = event_publisher or QueueEventPublisher(event_queue)
    dml_result = migrate_tables(
        job_id=app_config.job.name,
        tables=ordered_tables,
        source=source,
        target=target,
        checkpoint_store=CheckpointStore(checkpoint_db),
        event_publisher=dml_event_publisher,
        migration_config=app_config.migration,
    )

    if app_config.migration.apply_foreign_keys:
        foreign_key_ddls = generate_foreign_key_ddls(target_snapshot, target_dbms=app_config.target.dbms)
        foreign_key_results = execute_foreign_key_ddls(ddls=foreign_key_ddls, executor=target)
        if any(not result.success for result in foreign_key_results):
            failed = next(result for result in foreign_key_results if not result.success)
            raise RuntimeError(f"Self-test foreign key execution failed: {failed.table}.{failed.constraint_name} {failed.message}")
        _publish_selftest_event(event_publisher, app_config.job.name, EventType.PLAN_CREATED, "Target foreign keys executed.")

    execute_index_ddls(
        config=app_config,
        snapshot=target_snapshot,
        executor=target,
        phase=IndexApplyTiming.POST_DATA,
        report_output_path=report_root / "index-execution-post-data.json",
    )
    _publish_selftest_event(event_publisher, app_config.job.name, EventType.PLAN_CREATED, "Target indexes executed.")

    validation_report = validate_tables(
        job_id=app_config.job.name,
        tables=ordered_tables,
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
            migration_mode=app_config.migration.mode.value,
            existing_table_policy=app_config.migration.existing_table_policy.value,
            checksum_sample_size=app_config.verification.checksum_sample_size,
            checksum_timezone=app_config.verification.checksum_timezone,
            checksum_datetime_precision=app_config.verification.checksum_datetime_precision,
        ),
    )
    write_validation_report(validation_report, report_root / "validation")
    _publish_selftest_event(
        event_publisher,
        app_config.job.name,
        EventType.JOB_COMPLETED,
        f"Validation completed. status={validation_report.status}",
    )

    failed_tables = [table for table in dml_result.tables if table.status != "completed"]
    if failed_tables:
        table_messages = ", ".join(
            f"{table.table.name}:{table.status}:{table.message or 'no detail'}" for table in failed_tables
        )
        raise SelfTestError(f"Self-test DML failed: {table_messages}")
    if validation_report.status != ValidationStatus.MATCHED:
        raise SelfTestError(f"Self-test validation failed: status={validation_report.status}")

    return (
        "Self-test completed. "
        f"scenario={options.scenario} tables={len(ordered_tables)} rows_written={dml_result.rows_written} "
        f"validation={validation_report.status} reports={report_root}"
    )


def _publish_selftest_event(
    event_publisher: EventPublisher | None,
    job_id: str,
    event_type: EventType,
    message: str,
) -> None:
    if event_publisher is None:
        return
    event_publisher.publish(
        MigrationEvent(
            job_id=job_id,
            level=EventLevel.INFO,
            type=event_type,
            message=message,
        )
    )


def _dependency_ordered_tables(tables: tuple[TableSchema, ...]) -> tuple[TableSchema, ...]:
    table_by_ref = {table.ref: table for table in tables}
    plan = plan_table_creation_order(SchemaSnapshot(tables=tables))
    return tuple(table_by_ref[table_ref] for table_ref in plan.creation_order)


def _run_source_sql(
    options: SelfTestOptions,
    scenario: SelfTestScenario,
    sql_file: Path,
    *,
    variables: Mapping[str, str] | None = None,
    command_template: tuple[str, ...],
) -> None:
    render_context = {
        "large_rows": str((variables or {}).get("large_rows", DEFAULT_LARGE_ROWS)),
        "script_path": _container_script_path(scenario, sql_file),
    }
    command = tuple(_render_command_part(part, render_context) for part in command_template)
    _docker_compose(options, scenario, ("exec", "-T", scenario.source_service, *command))


def _container_script_path(scenario: SelfTestScenario, sql_file: Path) -> str:
    relative_path = sql_file.resolve().relative_to(scenario.compose_file.parent).as_posix()
    return f"/selftest/{relative_path}"


def _render_command_part(command_part: str, context: Mapping[str, str]) -> str:
    rendered = command_part
    for key, value in context.items():
        rendered = rendered.replace(f"{{{key}}}", value)
    return rendered


def _docker_compose(
    options: SelfTestOptions,
    scenario: SelfTestScenario,
    args: Sequence[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command_env = dict(os.environ)
    command_env.update(scenario.compose_env)
    return _run_command(
        ("docker", "compose", "-p", options.project_name, "-f", str(scenario.compose_file), *args),
        env=command_env,
        check=check,
        timeout=600,
    )


def _run_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=dict(env) if env is not None else None,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise SelfTestError(f"{' '.join(command)} failed: {detail}")
    return completed
