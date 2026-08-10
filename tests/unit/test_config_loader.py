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
  environment: dev
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.job.name == "legacy-postgres-to-mysql"
    assert config.source.database == "legacy"
    assert config.source.schema_name == "public"
    assert config.target.dbms is Dbms.MARIADB
    assert config.target.environment is TargetEnvironment.DEV


def test_load_config_reports_missing_file() -> None:
    with pytest.raises(ConfigLoadError, match="Config file does not exist"):
        load_config(Path("missing.yml"))
