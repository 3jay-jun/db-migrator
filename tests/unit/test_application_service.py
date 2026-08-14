from __future__ import annotations

import json
from pathlib import Path
from queue import Queue

from db_migrator.application.events import event_to_view
from db_migrator.application.safety import evaluate_dry_run_gate
from db_migrator.application.service import MigrationApplicationService
from db_migrator.config.loader import load_config
from db_migrator.config.models import Dbms
from db_migrator.core.events import EventLevel, EventType, MigrationEvent, ProgressSnapshot, QueueEventPublisher
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy
from db_migrator.schema.models import ColumnSchema, ForeignKeySchema, IndexSchema, PrimaryKey, ReadCursor, RowBatch, SchemaSnapshot, TableRef, TableSchema, WriteResult


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
    registry = FakeRegistry()
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(
            _table_with_columns("target_db", "users", ("id", "email")),
            _table_with_columns("target_db", "orders", ("id",)),
        )
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)

    result = service.run_scan_tables(config=config_path)

    assert result.success is True
    assert result.table_count == 2
    assert [table.identifier for table in result.details["tables"]] == ["public.users", "public.orders"]
    assert [table.identifier for table in result.details["target_tables"]] == ["target_db.users", "target_db.orders"]
    assert result.details["tables"][0].columns[0].name == "id"


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


def test_service_closes_source_adapter_after_command(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)

    result = service.run_scan_tables(config=config_path)

    assert result.success is True
    assert registry.source.closed is True


def test_service_closes_target_schema_scan_adapter_after_command(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)

    result = service.run_scan_tables(config=config_path)

    assert result.success is True
    assert registry.target_schema_source.closed is True


def test_service_resolves_tunnel_endpoint_for_source_and_target(tmp_path: Path) -> None:
    registry = FakeRegistry()
    tunnel_factory = FakeTunnelFactory()
    service = MigrationApplicationService(registry, tunnel_factory=tunnel_factory)
    config_path = _write_tunnel_config(tmp_path)

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json")

    assert result.success is True
    assert registry.source_configs[0].host == "127.0.0.1"
    assert registry.source_configs[0].port == 15000
    assert registry.source_configs[-1].host == "127.0.0.1"
    assert registry.source_configs[-1].port == 15001
    assert registry.target_configs[-1].host == "127.0.0.1"
    assert registry.target_configs[-1].port == 15001
    assert [tunnel.stopped for tunnel in tunnel_factory.tunnels] == [True, True]


def test_service_connection_test_reports_db_and_tunnel_endpoints(tmp_path: Path) -> None:
    service = MigrationApplicationService(FakeRegistry(), tunnel_factory=FakeTunnelFactory())
    config_path = _write_tunnel_config(tmp_path)

    result = service.run_test_source_connection(config=config_path)

    assert result.success is True
    assert "db_endpoint=10.0.1.10:5432" in result.message
    assert "tunnel_local_endpoint=127.0.0.1:15000" in result.message


def test_service_validate_resolves_target_schema_scan_tunnel(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry, tunnel_factory=FakeTunnelFactory())
    config_path = _write_tunnel_config(tmp_path)

    result = service.run_validate(config=config_path, output_dir=tmp_path / "reports")

    assert result.success is True
    assert [config.port for config in registry.source_configs] == [15000, 15001]
    assert registry.source_configs[-1].database == "target_db"


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


def test_service_append_policy_apply_ddl_creates_only_missing_target_tables(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "users", ("id",)),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: append
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json")

    assert result.success is True
    assert registry.target.executed_ddls == ["CREATE TABLE `target_db`.`orders` (`id` int)"]


def test_service_append_policy_migrate_data_writes_only_missing_target_tables(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "users", ("id",)),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: append
""",
    )

    result = service.run_migrate_data(
        config=config_path,
        checkpoint_db=tmp_path / "checkpoint.sqlite",
        event_publisher=QueueEventPublisher(Queue()),
    )

    assert result.success is True
    assert registry.source.read_tables == [TableRef(schema="public", name="orders")]
    assert registry.target.written_tables == [TableRef(schema="target_db", name="orders")]


def test_service_sync_policy_apply_ddl_drops_target_only_tables_for_full_scope(tmp_path: Path) -> None:
    registry = FakeRegistry()
    child = _table_with_columns("target_db", "zzz_child", ("id", "parent_id"))
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(
            _table_with_columns("target_db", "users", ("id",)),
            _table_with_columns("target_db", "aaa_parent", ("id",)),
            TableSchema(
                ref=child.ref,
                primary_key=child.primary_key,
                columns=child.columns,
                foreign_keys=(
                    ForeignKeySchema(
                        name="zzz_child_parent_id_fkey",
                        columns=("parent_id",),
                        referenced_table=TableRef(schema="target_db", name="aaa_parent"),
                        referenced_columns=("id",),
                    ),
                ),
            ),
        )
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json")

    assert result.success is True
    assert registry.target.dropped_tables == [
        TableRef(schema="target_db", name="zzz_child"),
        TableRef(schema="target_db", name="aaa_parent"),
    ]
    assert registry.target.executed_ddls == ["CREATE TABLE `target_db`.`orders` (`id` int)"]


def test_service_sync_policy_selected_tables_do_not_drop_target_tables_outside_scope(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(
            _table_with_columns("target_db", "users", ("id",)),
            _table_with_columns("target_db", "legacy_logs", ("id",)),
        )
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
""",
    )

    result = service.run_apply_ddl(
        config=config_path,
        output_file=tmp_path / "ddl-execution.json",
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert registry.target.dropped_tables == []
    assert registry.target.executed_ddls == []


def test_service_sync_policy_blocks_target_only_drop_referenced_by_kept_table(tmp_path: Path) -> None:
    registry = FakeRegistry()
    users = _table_with_columns("target_db", "users", ("id", "legacy_id"))
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(
            TableSchema(
                ref=users.ref,
                primary_key=users.primary_key,
                columns=users.columns,
                foreign_keys=(
                    ForeignKeySchema(
                        name="users_legacy_id_fkey",
                        columns=("legacy_id",),
                        referenced_table=TableRef(schema="target_db", name="legacy_lookup"),
                        referenced_columns=("id",),
                    ),
                ),
            ),
            _table_with_columns("target_db", "legacy_lookup", ("id",)),
        )
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json")

    assert result.success is False
    assert registry.target.dropped_tables == []
    payload = json.loads((tmp_path / "ddl-execution.json").read_text(encoding="utf-8"))
    blocked = [table for table in payload["tables"] if table["action"] == "blocked"]
    assert blocked[0]["table"] == "legacy_lookup"
    assert "users_legacy_id_fkey" in blocked[0]["message"]


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


def test_service_apply_foreign_keys_executes_mapped_target_constraints(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(
        tables=(
            _table_with_columns("public", "users", ("id",)),
            TableSchema(
                ref=TableRef(schema="public", name="orders"),
                primary_key=PrimaryKey(columns=("id",)),
                columns=_table_with_columns("public", "orders", ("id", "user_id")).columns,
                foreign_keys=(
                    ForeignKeySchema(
                        name="orders_user_id_fkey",
                        columns=("user_id",),
                        referenced_table=TableRef(schema="public", name="users"),
                        referenced_columns=("id",),
                    ),
                ),
            ),
        )
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)

    result = service.run_apply_foreign_keys(config=config_path, output_file=tmp_path / "foreign-keys.json")

    assert result.success is True
    assert registry.target.executed_ddls == [
        "ALTER TABLE `target_db`.`orders` ADD CONSTRAINT `orders_user_id_fkey` FOREIGN KEY (`user_id`) REFERENCES `target_db`.`users` (`id`);"
    ]
    assert (tmp_path / "foreign-keys.json").exists()


def test_service_apply_ddl_executes_configured_sync_alter_candidate(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    registry.target.existing_tables = {"app_users"}
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
    source_only_columns:
      legacy_code: add_to_target
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json", selected_tables={"public.users"})

    assert result.success is True
    assert registry.target.executed_ddls == ["ALTER TABLE `target_db`.`app_users` ADD COLUMN `legacy_code` longtext NOT NULL;"]


def test_service_apply_ddl_executes_configured_new_target_column_name(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id",)),))
    registry.target.existing_tables = {"app_users"}
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
    columns:
      legacy_id:
        source: legacy_code
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json", selected_tables={"public.users"})

    assert result.success is True
    assert registry.target.executed_ddls == ["ALTER TABLE `target_db`.`app_users` ADD COLUMN `legacy_id` longtext NOT NULL;"]


def test_service_apply_ddl_executes_configured_target_column_rename(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    registry.target.existing_tables = {"app_users"}
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
    columns:
      id_:
        source: id
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json", selected_tables={"public.users"})

    assert result.success is True
    assert registry.target.executed_ddls == ["ALTER TABLE `target_db`.`app_users` RENAME COLUMN `id` TO `id_`;"]


def test_service_apply_ddl_executes_existing_target_column_type_change(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(
        tables=(_typed_table("public", "users", {"email": ("character varying(320)", CommonTypeKind.STRING, 320)}),)
    )
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(_typed_table("target_db", "app_users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)}),)
    )
    registry.target.existing_tables = {"app_users"}
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json", selected_tables={"public.users"})

    assert result.success is True
    assert registry.target.executed_ddls == ["ALTER TABLE `target_db`.`app_users` MODIFY COLUMN `email` varchar(320) NOT NULL;"]


def test_service_apply_ddl_executes_configured_target_type_override(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(
        tables=(_typed_table("public", "users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)}),)
    )
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(_typed_table("target_db", "app_users", {"email": ("varchar(255)", CommonTypeKind.STRING, 255)}),)
    )
    registry.target.existing_tables = {"app_users"}
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
    columns:
      email:
        target_type: varchar(500)
""",
    )

    result = service.run_apply_ddl(config=config_path, output_file=tmp_path / "ddl-execution.json", selected_tables={"public.users"})

    assert result.success is True
    assert registry.target.executed_ddls == ["ALTER TABLE `target_db`.`app_users` MODIFY COLUMN `email` varchar(500) NOT NULL;"]


def test_service_migrate_data_writes_configured_new_target_column_name(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id",)),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
    columns:
      legacy_id:
        source: legacy_code
""",
    )

    result = service.run_migrate_data(
        config=config_path,
        checkpoint_db=tmp_path / "checkpoint.sqlite",
        event_publisher=QueueEventPublisher(Queue()),
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert registry.source.read_columns == [("id", "legacy_code")]
    assert registry.target.written_rows[0] == ({"id": 1, "legacy_id": 1},)


def test_service_table_preview_uses_configured_target_column_rename(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
    columns:
      id_:
        source: id
""",
    )

    result = service.run_table_preview(
        config=config_path,
        table_identifier="public.users",
        target_schema="target_db",
        target_table="app_users",
    )

    assert result.success is True
    assert result.details["columns"] == ("id_", "email")
    assert result.details["rows"] == ({"id_": 1, "email": 1},)
    assert registry.source.sample_columns[-1] == ("id", "email")


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
    assert (manual_dir / "data" / "target_db.app_users.csv").exists()
    assert "`app_users`" in (manual_dir / "ddl.sql").read_text(encoding="utf-8")
    assert "LOAD DATA LOCAL INFILE" in (manual_dir / "load-data.sql").read_text(encoding="utf-8")
    assert "id" in (manual_dir / "data" / "target_db.app_users.csv").read_text(encoding="utf-8")
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
    assert registry.target.written_tables == [TableRef(schema="target_db", name="app_users")]


def test_service_dry_run_reports_source_only_columns_for_existing_target_table(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
    source_only_columns:
      legacy_code: add_to_target
""",
    )

    result = service.run_dry_run(config=config_path, output_dir=tmp_path / "reports", selected_tables={"public.users"})

    summary = (tmp_path / "reports" / "summary.json").read_text(encoding="utf-8")
    assert result.success is True
    assert '"schema_origin": "target_existing"' in summary
    assert "legacy_code" in summary
    assert "ALTER TABLE `target_db`.`app_users` ADD COLUMN `legacy_code` longtext NOT NULL;" in summary
    assert "source-only column ignored by default: legacy_code" not in summary


def test_service_migrate_data_uses_existing_target_schema_and_migrates_source_only_columns_by_default(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
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
    assert registry.source.read_columns == [("id", "email", "legacy_code")]
    assert registry.target.written_tables == [TableRef(schema="target_db", name="app_users")]
    assert registry.target.written_rows[0][0]["legacy_code"] == 1


def test_service_sync_maps_source_key_to_target_key_column(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(
        tables=(_table_with_columns("target_db", "app_users", ("user_id", "email"), primary_key=("user_id",)),)
    )
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  existing_table_policy: sync
tables:
  public.users:
    target_table: app_users
    columns:
      user_id:
        source: id
""",
    )

    result = service.run_migrate_data(
        config=config_path,
        checkpoint_db=tmp_path / "checkpoint.sqlite",
        event_publisher=QueueEventPublisher(Queue()),
        selected_tables={"public.users"},
    )

    assert result.success is True
    assert registry.target.upserted_batches == [("app_users", ("user_id",), ({"user_id": 1, "email": 1},))]
    assert registry.target.sync_keys == [
        ("app_users", ("begin", ("user_id",))),
        ("app_users", (1,)),
        ("app_users", ("delete", ("user_id",))),
        ("app_users", ("end",)),
    ]


def test_service_migrate_data_strict_source_only_columns_allows_default_source_only_migration(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
migration:
  strict_source_only_columns: true
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
    assert result.rows_written == 1
    assert registry.source.read_columns == [("id", "email", "legacy_code")]
    assert registry.target.written_tables == [TableRef(schema="target_db", name="app_users")]


def test_service_table_preview_returns_transformed_sample_rows(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "email", "legacy_code")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_table_preview(
        config=config_path,
        table_identifier="public.users",
        target_schema="target_db",
        target_table="app_users",
        sample_size=30,
    )

    assert result.success is True
    assert result.details["columns"] == ("id", "email", "legacy_code")
    assert result.details["rows"] == ({"id": 1, "email": 1, "legacy_code": 1},)
    assert registry.source.read_columns == []


def test_service_table_preview_applies_unsaved_column_mapping(tmp_path: Path) -> None:
    registry = FakeRegistry()
    registry.source.snapshot = SchemaSnapshot(tables=(_table_with_columns("public", "users", ("id", "old_email")),))
    registry.target_schema_source.snapshot = SchemaSnapshot(tables=(_table_with_columns("target_db", "app_users", ("id", "email")),))
    service = MigrationApplicationService(registry)
    config_path = _write_config(
        tmp_path,
        """
tables:
  public.users:
    target_table: app_users
""",
    )

    result = service.run_table_preview(
        config=config_path,
        table_identifier="public.users",
        target_schema="target_db",
        target_table="app_users",
        column_mappings={"old_email": "email"},
    )

    assert result.success is True
    assert result.details["columns"] == ("id", "email")
    assert result.details["rows"] == ({"id": 1, "email": 1},)
    assert registry.source.sample_columns[-1] == ("id", "old_email")


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
    assert registry.target.counted_tables == [TableRef(schema="target_db", name="app_users")]
    summary = (tmp_path / "reports" / "summary.json").read_text(encoding="utf-8")
    assert "public.users -> target_db.app_users" in summary


def test_service_validate_loads_prior_execution_artifacts(tmp_path: Path) -> None:
    registry = FakeRegistry()
    service = MigrationApplicationService(registry)
    config_path = _write_config(tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "ddl-execution.json").write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "schema": "target_db",
                        "table": "users",
                        "action": "create",
                        "success": True,
                        "message": "ok",
                        "ddl": "CREATE TABLE `target_db`.`users` (`id` int);",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = service.run_validate(config=config_path, output_dir=reports_dir)

    assert result.success is True
    summary = json.loads((reports_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["summary"]["execution_artifact_count"] == 1
    assert summary["execution_artifacts"][0]["object_name"] == "target_db.users"


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
        self.target_schema_source = FakeAdapter()
        self.source_configs = []
        self.target_configs = []

    def create_source(self, config):
        self.source_configs.append(config)
        if config.dbms in {Dbms.MYSQL, Dbms.MARIADB}:
            return self.target_schema_source
        return self.source

    def create_target(self, _config):
        self.target_configs.append(_config)
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
        self.existing_tables: set[str] = set()
        self.executed_ddls: list[str] = []
        self.dropped_tables: list[TableRef] = []
        self.read_tables: list[TableRef] = []
        self.read_columns: list[tuple[str, ...]] = []
        self.sample_columns: list[tuple[str, ...]] = []
        self.written_tables: list[TableRef] = []
        self.written_rows: list[tuple[dict, ...]] = []
        self.upserted_batches: list[tuple[str, tuple[str, ...], tuple[dict, ...]]] = []
        self.sync_keys: list[tuple[str, tuple]] = []
        self.counted_tables: list[TableRef] = []
        self.closed = False

    def test_connection(self) -> bool:
        return True

    def scan_schema(self, _schema: str) -> SchemaSnapshot:
        return self.snapshot

    def table_exists(self, _table_schema: TableSchema) -> bool:
        return _table_schema.ref.name in self.existing_tables

    def execute_ddl(self, _ddl: str):
        from db_migrator.adapters.base import ExecutionResult

        self.executed_ddls.append(_ddl)
        return ExecutionResult(success=True, message="ok")

    def truncate_table(self, _table_schema: TableSchema):
        from db_migrator.adapters.base import ExecutionResult

        return ExecutionResult(success=True, message="ok")

    def drop_table(self, _table_schema: TableSchema):
        from db_migrator.adapters.base import ExecutionResult

        self.dropped_tables.append(_table_schema.ref)
        return ExecutionResult(success=True, message="ok")

    def read_rows(self, table: TableRef, _columns, _cursor, _batch_size, _order_by):
        self.read_tables.append(table)
        self.read_columns.append(tuple(_columns))
        yield RowBatch(
            table=table,
            rows=(dict.fromkeys(_columns, 1),),
            batch_number=1,
            start_offset=0,
            next_cursor=ReadCursor.offset_cursor(1),
        )

    def write_batch(self, table_schema: TableSchema, rows: tuple[dict, ...]) -> WriteResult:
        self.written_tables.append(table_schema.ref)
        self.written_rows.append(rows)
        return WriteResult(success=True, rows_written=len(rows), message="ok")

    def upsert_batch(self, table_schema: TableSchema, rows: tuple[dict, ...], keys) -> WriteResult:
        self.upserted_batches.append((table_schema.ref.name, tuple(keys), rows))
        return WriteResult(success=True, rows_written=len(rows), message="ok")

    def fetch_rows_by_keys(self, table_schema: TableSchema, keys: tuple[str, ...], rows: tuple[dict, ...]) -> dict[tuple[object, ...], dict]:
        return {}

    def begin_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> None:
        self.sync_keys.append((table_schema.ref.name, ("begin", keys)))

    def record_sync_keys(self, table_schema: TableSchema, rows: tuple[dict, ...], keys: tuple[str, ...]) -> None:
        self.sync_keys.extend((table_schema.ref.name, tuple(row.get(key) for key in keys)) for row in rows)

    def delete_rows_not_in_sync_keys(self, table_schema: TableSchema, keys: tuple[str, ...]) -> int:
        self.sync_keys.append((table_schema.ref.name, ("delete", keys)))
        return 0

    def end_sync_keys(self, table_schema: TableSchema) -> None:
        self.sync_keys.append((table_schema.ref.name, ("end",)))

    def commit(self) -> None:
        return None

    def count_rows(self, table: TableRef) -> int:
        self.counted_tables.append(table)
        return 1

    def sample_rows(self, table: TableRef, _columns, _sample_size, _order_by, _position):
        self.sample_columns.append(tuple(_columns))
        return (dict.fromkeys(_columns, 1),)

    def close(self) -> None:
        self.closed = True


class FakeTunnelFactory:
    def __init__(self) -> None:
        self.tunnels: list[FakeTunnel] = []

    def create(self, *, label: str, endpoint_host: str, endpoint_port: int, config):
        tunnel = FakeTunnel(label=label, local_port=15000 + len(self.tunnels))
        self.tunnels.append(tunnel)
        return tunnel


class FakeTunnel:
    def __init__(self, *, label: str, local_port: int) -> None:
        self.label = label
        self.local_bind_host = "127.0.0.1"
        self.local_bind_port = local_port
        self.stopped = False

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True


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


def _table_with_columns(schema: str, name: str, columns: tuple[str, ...], *, primary_key: tuple[str, ...] | None = None) -> TableSchema:
    resolved_primary_key = primary_key if primary_key is not None else (("id",) if "id" in columns else None)
    return TableSchema(
        ref=TableRef(schema=schema, name=name),
        primary_key=PrimaryKey(columns=resolved_primary_key) if resolved_primary_key is not None else None,
        columns=tuple(
            ColumnSchema(
                name=column,
                source_type="integer" if column in {"id", "user_id"} else "text",
                common_type=CommonType(
                    kind=CommonTypeKind.INTEGER if column in {"id", "user_id"} else CommonTypeKind.TEXT,
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


def _write_tunnel_config(tmp_path: Path) -> Path:
    key_file = tmp_path / "service.pem"
    key_file.write_text("fake key", encoding="utf-8")
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fake known hosts", encoding="utf-8")
    return _write_config(
        tmp_path,
        f"""
source:
  dbms: postgresql
  host: 10.0.1.10
  port: 5432
  database: source_db
  schema: public
  tunnel:
    enabled: true
    ssh_host: source-ec2.example.com
    ssh_user: ec2-user
    private_key_path: {key_file}
    known_hosts_path: {known_hosts}
target:
  dbms: mysql
  host: 10.0.2.20
  port: 3306
  database: target_db
  environment: staging
  tunnel:
    enabled: true
    ssh_host: target-ec2.example.com
    ssh_user: ec2-user
    private_key_path: {key_file}
    known_hosts_path: {known_hosts}
""",
    )
