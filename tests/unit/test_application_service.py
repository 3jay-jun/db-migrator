from __future__ import annotations

from pathlib import Path
from queue import Queue

from db_migrator.application.events import event_to_view
from db_migrator.application.safety import evaluate_dry_run_gate
from db_migrator.application.service import MigrationApplicationService
from db_migrator.config.loader import load_config
from db_migrator.core.events import EventLevel, EventType, MigrationEvent, ProgressSnapshot, QueueEventPublisher
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, IndexSchema, PrimaryKey, ReadCursor, RowBatch, SchemaSnapshot, TableRef, TableSchema, WriteResult


def test_service_dry_run_writes_report_with_shared_orchestration(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(tmp_path)

    result = service.run_dry_run(config=config_path, output_dir=tmp_path / "reports")

    assert result.success is True
    assert result.table_count == 2
    assert result.warning_count == 0
    assert result.report_html == tmp_path / "reports" / "summary.html"
    assert (tmp_path / "reports" / "summary.json").exists()


def test_service_scans_tables_for_gui_selection(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(tmp_path)

    result = service.run_scan_tables(config=config_path)

    assert result.success is True
    assert result.table_count == 2
    assert [table.identifier for table in result.details["tables"]] == ["public.users", "public.orders"]


def test_service_dry_run_filters_selected_tables(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(tmp_path)

    result = service.run_dry_run(
        config=config_path,
        output_dir=tmp_path / "reports",
        selected_tables={"public.orders"},
    )

    assert result.success is True
    assert result.table_count == 1
    summary = (tmp_path / "reports" / "summary.json").read_text(encoding="utf-8")
    assert "orders" in summary
    assert "users" not in summary


def test_service_connection_tests_use_configured_adapters(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(tmp_path)

    source_result = service.run_test_source_connection(config=config_path)
    target_result = service.run_test_target_connection(config=config_path)

    assert source_result.success is True
    assert target_result.success is True


def test_service_apply_ddl_accepts_gui_dry_run_report_override(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(
        tmp_path,
        """
target:
  environment: production
migration:
  existing_table_policy: truncate_reload
safety:
  allow_destructive_on_production: true
""",
    )
    dry_run_report = tmp_path / "dry-run" / "summary.html"
    dry_run_report.parent.mkdir()
    dry_run_report.write_text("<html></html>", encoding="utf-8")

    result = service.run_apply_ddl(
        config=config_path,
        output_file=tmp_path / "ddl-execution.json",
        dry_run_report_path=dry_run_report,
    )

    assert result.success is True
    assert result.output_file == tmp_path / "ddl-execution.json"
    assert result.table_count == 2


def test_service_apply_indexes_executes_post_data_auto_candidates(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)

    result = service.run_apply_indexes(config=config_path, output_file=tmp_path / "indexes.json")

    assert result.success is True
    assert "CREATE INDEX" in registry.target.executed_ddls[-1]
    assert "`target_db`.`users`" in registry.target.executed_ddls[-1]
    assert "`idx_users_email`" in registry.target.executed_ddls[-1]
    assert (tmp_path / "indexes.json").exists()


def test_service_migrate_data_publishes_events_and_returns_rows(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry())
    config_path = _write_config(tmp_path)
    event_queue: Queue[MigrationEvent] = Queue()

    result = service.run_migrate_data(
        config=config_path,
        checkpoint_db=tmp_path / "checkpoint.sqlite",
        event_publisher=QueueEventPublisher(event_queue),
    )

    assert result.success is True
    assert result.rows_written == 2
    assert event_queue.qsize() > 0


def test_service_manual_ddl_uses_target_table_mapping_without_executing_target(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_generate_manual_ddl(
        config=config_path,
        output_dir=tmp_path / "reports",
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert "`app_users`" in result.details["sql"]
    assert "`users`" not in result.details["sql"]
    assert registry.target.executed_ddls == []


def test_service_manual_migration_exports_ddl_csv_and_load_script(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_generate_manual_migration(
        config=config_path,
        output_dir=tmp_path / "reports",
        selected_tables={"public.users"},
    )

    manual_dir = tmp_path / "reports" / "manual-migration"
    assert result.success is True
    assert result.output_dir == manual_dir
    assert (manual_dir / "ddl.sql").exists()
    assert (manual_dir / "load-data.sql").exists()
    assert (manual_dir / "data" / "public.app_users.csv").exists()
    assert "`app_users`" in (manual_dir / "ddl.sql").read_text(encoding="utf-8")
    assert "LOAD DATA LOCAL INFILE" in (manual_dir / "load-data.sql").read_text(encoding="utf-8")
    assert "id" in (manual_dir / "data" / "public.app_users.csv").read_text(encoding="utf-8")
    assert registry.target.executed_ddls == []


def test_service_migrate_data_reads_source_table_and_writes_mapped_target_table(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_migrate_data(
        config=config_path,
        checkpoint_db=tmp_path / "checkpoint.sqlite",
        event_publisher=QueueEventPublisher(Queue()),
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert registry.source.read_tables == [TableRef(schema="public", name="users")]
    assert registry.target.written_tables == [TableRef(schema="public", name="app_users")]


def test_service_validate_counts_mapped_target_table(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_validate(
        config=config_path,
        output_dir=tmp_path / "reports",
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert registry.source.counted_tables == [TableRef(schema="public", name="users")]
    assert registry.target.counted_tables == [TableRef(schema="public", name="app_users")]
    summary = (tmp_path / "reports" / "summary.json").read_text(encoding="utf-8")
    assert "public.users -> public.app_users" in summary


def test_dry_run_gate_blocks_destructive_policy_without_report(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
""",
    )
    config = load_config(config_path)

    decision = evaluate_dry_run_gate(config, None)

    assert decision.allowed is False
    assert decision.required is True
    assert "Dry-run report is required" in decision.message


def test_event_to_view_caps_progress_at_one_hundred_percent() -> None:
    event = MigrationEvent(
        job_id="job-1",
        level=EventLevel.INFO,
        type=EventType.BATCH_COMMITTED,
        message="Batch committed.",
        table="users",
        progress=ProgressSnapshot(completed_units=73, total_units=55, current_unit="users"),
    )

    view = event_to_view(event)

    assert view.level == "info"
    assert view.event_type == "batch_committed"
    assert view.table == "users"
    assert view.progress_label == "73/55 (100.0%)"


class FakeRegistry:
    def __init__(self) -> None:
        self.source = FakeAdapter()
        self.target = FakeAdapter()

    def create_source(self, _config):
        return self.source

    def create_target(self, _config):
        return self.target

    def create_ddl_generator(self, _dbms, *, target_database: str | None = None):
        return FakeDdlGenerator(target_database)


class FakeDdlGenerator:
    def __init__(self, target_database: str | None) -> None:
        self._target_database = target_database

    def generate_create_table(self, table_schema: TableSchema):
        from db_migrator.adapters.base import DdlResult

        database = f"`{self._target_database}`." if self._target_database else ""
        return DdlResult(table_name=table_schema.ref.name, ddl=f"CREATE TABLE {database}`{table_schema.ref.name}` (`id` int)", warnings=())


class FakeAdapter:
    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.executed_ddls: list[str] = []
        self.read_tables: list[TableRef] = []
        self.written_tables: list[TableRef] = []
        self.counted_tables: list[TableRef] = []

    def test_connection(self) -> bool:
        return True

    def scan_schema(self, _schema: str) -> SchemaSnapshot:
        return self.snapshot

    def table_exists(self, _table_schema: TableSchema) -> bool:
        return False

    def execute_ddl(self, _ddl: str):
        from db_migrator.adapters.base import ExecutionResult

        self.executed_ddls.append(_ddl)
        return ExecutionResult(success=True, message="ok")

    def truncate_table(self, _table_schema: TableSchema):
        from db_migrator.adapters.base import ExecutionResult

        return ExecutionResult(success=True, message="ok")

    def drop_table(self, _table_schema: TableSchema):
        from db_migrator.adapters.base import ExecutionResult

        return ExecutionResult(success=True, message="ok")

    def read_rows(self, table: TableRef, _columns, _cursor, _batch_size, _order_by):
        self.read_tables.append(table)
        yield RowBatch(
            table=table,
            rows=({"id": 1},),
            batch_number=1,
            start_offset=0,
            next_cursor=ReadCursor.offset_cursor(1),
        )

    def write_batch(self, table_schema: TableSchema, rows: tuple[dict, ...]) -> WriteResult:
        self.written_tables.append(table_schema.ref)
        return WriteResult(success=True, rows_written=len(rows), message="ok")

    def upsert_batch(self, _table_schema: TableSchema, rows: tuple[dict, ...], _keys) -> WriteResult:
        return WriteResult(success=True, rows_written=len(rows), message="ok")

    def commit(self) -> None:
        return None

    def count_rows(self, table: TableRef) -> int:
        self.counted_tables.append(table)
        return 1

    def sample_rows(self, table: TableRef, _columns, _sample_size, _order_by, _position):
        return ({"id": 1},)


def _snapshot() -> SchemaSnapshot:
    return SchemaSnapshot(
        tables=(
            TableSchema(
                ref=TableRef(schema="public", name="users"),
                primary_key=PrimaryKey(columns=("id",)),
                indexes=(IndexSchema(name="idx_users_email", columns=("id",)),),
                columns=(
                    ColumnSchema(
                        name="id",
                        source_type="integer",
                        common_type=CommonType(kind=CommonTypeKind.INTEGER, policy=TypePolicy.AUTO_CONVERT),
                        nullable=False,
                        default=None,
                        is_generated=False,
                        generation_expression=None,
                        ordinal_position=1,
                    ),
                ),
            ),
            TableSchema(
                ref=TableRef(schema="public", name="orders"),
                primary_key=PrimaryKey(columns=("id",)),
                columns=(
                    ColumnSchema(
                        name="id",
                        source_type="integer",
                        common_type=CommonType(kind=CommonTypeKind.INTEGER, policy=TypePolicy.AUTO_CONVERT),
                        nullable=False,
                        default=None,
                        is_generated=False,
                        generation_expression=None,
                        ordinal_position=1,
                    ),
                ),
            ),
        )
    )


def _write_config(tmp_path: Path, overrides: str = "") -> Path:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"""
job:
  name: gui-test
source:
  dbms: postgresql
  database: source_db
  schema: public
target:
  dbms: mysql
  database: target_db
{overrides}
""",
        encoding="utf-8",
    )
    return config_path
