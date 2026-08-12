from pathlib import Path

import pytest

from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import Dbms, SshAuthenticationType, TargetEnvironment


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


def test_load_config_reads_ssh_tunnel_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
source:
  host: 10.0.1.10
  port: 5432
  tunnel:
    enabled: true
    ssh_host: ec2.example.com
    ssh_user: ec2-user
    private_key_path: C:/keys/service.pem
    private_key_passphrase_env: SSH_KEY_PASSPHRASE
    known_hosts_path: C:/Users/me/.ssh/known_hosts
    remote_host: pg.internal
    remote_port: 5432
    local_port: 0
target:
  tunnel:
    enabled: true
    ssh_host: target.example.com
    ssh_user: ubuntu
    auth_type: password
    ssh_password: ssh-secret
    known_hosts_path: C:/Users/me/.ssh/known_hosts
    local_host: 127.0.0.1
    local_port: 13306
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    dumped = config.model_dump(by_alias=True, mode="json")

    assert config.source.tunnel.enabled is True
    assert config.source.tunnel.ssh_host == "ec2.example.com"
    assert config.source.tunnel.private_key_passphrase_env == "SSH_KEY_PASSPHRASE"
    assert config.source.tunnel.remote_host == "pg.internal"
    assert config.source.tunnel.remote_port == 5432
    assert config.target.tunnel.auth_type is SshAuthenticationType.PASSWORD
    assert config.target.tunnel.ssh_password == "ssh-secret"
    assert config.target.tunnel.local_port == 13306
    assert dumped["source"]["tunnel"]["private_key_path"] == "C:/keys/service.pem"
    assert dumped["target"]["tunnel"]["auth_type"] == "password"


def test_load_config_reports_missing_file() -> None:
    with pytest.raises(ConfigLoadError, match="Config file does not exist"):
        load_config(Path("missing.yml"))
