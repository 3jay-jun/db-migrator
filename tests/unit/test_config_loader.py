from pathlib import Path

import pytest

from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import Dbms, TargetEnvironment


def test_load_config_uses_safe_defaults_when_path_is_missing() -> None:
    config = load_config(None)

    assert config.source.dbms is Dbms.POSTGRESQL
    assert config.target.environment is TargetEnvironment.STAGING
    assert config.safety.is_production_protection is True


def test_load_config_reads_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
job:
  name: legacy-postgres-to-mysql
source:
  database: legacy
  schema: public
target:
  dbms: mariadb
  database: migrated
  schema: app
  environment: dev
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.job.name == "legacy-postgres-to-mysql"
    assert config.source.database == "legacy"
    assert config.source.schema_name == "public"
    assert config.target.dbms is Dbms.MARIADB
    assert config.target.schema_name == "app"
    assert config.target.environment is TargetEnvironment.DEV


def test_load_config_reads_table_run_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
tables:
  public.users:
    target_schema: app
    target_table: app_users
    incremental:
      watermark_column: updated_at
      start_value: "2026-01-01T00:00:00"
      end_value: "2026-02-01T00:00:00"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.tables["public.users"].target_table == "app_users"
    assert config.tables["public.users"].target_schema == "app"
    assert config.tables["public.users"].incremental.watermark_column == "updated_at"
    assert config.tables["public.users"].incremental.start_value == "2026-01-01T00:00:00"


def test_load_config_reports_missing_file() -> None:
    with pytest.raises(ConfigLoadError, match="Config file does not exist"):
        load_config(Path("missing.yml"))
