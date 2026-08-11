from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from db_migrator.application import CommandResult, MigrationApplicationService
from db_migrator.cli.console_events import ConsoleEventPublisher
from db_migrator.selftest.package_check import check_pyinstaller_available
from db_migrator.selftest.runner import run_self_test

app = typer.Typer(help="Safe DB migration helper.")
_self_test_app = typer.Typer(help="Optional Docker-based self-test.")
app.add_typer(_self_test_app, name="self-test")
console = Console()
_service = MigrationApplicationService()


@app.callback()
def main() -> None:
    """DB migration CLI."""


@app.command()
def bootstrap(config: Path | None = typer.Option(None, "--config", "-c")) -> None:
    result = _service.run_bootstrap(config)
    for event in result.events:
        console.print(f"[{event.level}] {event.message}")
    _print_or_fail(result)


@app.command("dry-run")
def dry_run(
    config: Path | None = typer.Option(None, "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    _print_or_fail(_service.run_dry_run(config=config, schema_file=schema_file, output_dir=output_dir))


@app.command("apply-ddl")
def apply_ddl(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_file: Path | None = typer.Option(None, "--output-file"),
) -> None:
    _print_or_fail(_service.run_apply_ddl(config=config, schema_file=schema_file, output_file=output_file))


@app.command("migrate-data")
def migrate_data(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    _print_or_fail(
        _service.run_migrate_data(
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=ConsoleEventPublisher(console),
        )
    )


@app.command("resume")
def resume(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    _print_or_fail(
        _service.run_resume(
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=ConsoleEventPublisher(console),
        )
    )


@app.command("retry-failed")
def retry_failed(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    checkpoint_db: Path = typer.Option(Path("checkpoints/migration.sqlite"), "--checkpoint-db"),
) -> None:
    _print_or_fail(
        _service.run_retry_failed(
            config=config,
            schema_file=schema_file,
            checkpoint_db=checkpoint_db,
            event_publisher=ConsoleEventPublisher(console),
        )
    )


@app.command("validate")
def validate(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    _print_or_fail(_service.run_validate(config=config, schema_file=schema_file, output_dir=output_dir))


@app.command("migrate-incremental")
def migrate_incremental(
    config: Path = typer.Option(..., "--config", "-c"),
    schema_file: Path | None = typer.Option(None, "--schema-file"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
) -> None:
    _print_or_fail(_service.run_incremental(config=config, schema_file=schema_file, output_dir=output_dir))


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
    result = _service.run_doctor(project_root)
    for check in result.details.get("checks", ()):
        console.print(f"{check.status.upper()} {check.name}: {check.message}")
    if not result.success:
        raise typer.Exit(code=1)


def _print_or_fail(result: CommandResult) -> None:
    if not result.success:
        raise typer.BadParameter(result.message)
    console.print(result.message)
