from unittest.mock import patch

import pytest

from db_migrator.selftest.package_check import check_pyinstaller_available
from db_migrator.selftest.runner import (
    DOCKER_MISSING_MESSAGE,
    SelfTestError,
    SelfTestOptions,
    check_docker_available,
    _load_scenario,
)


def test_check_docker_available_reports_clear_message_when_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = check_docker_available()

    assert result.success is False
    assert result.message == DOCKER_MISSING_MESSAGE


def test_package_check_reports_pyinstaller_missing() -> None:
    with patch("shutil.which", return_value=None):
        result = check_pyinstaller_available()

    assert result.success is False
    assert "PyInstaller is not installed" in result.message


def test_default_selftest_scenario_files_exist(tmp_path) -> None:
    scenario = _load_scenario(SelfTestOptions(large_rows=10, work_dir=tmp_path))

    assert scenario.config_file.exists()
    assert scenario.config_file.as_posix().endswith("/generated-configs/pg_to_mariadb/config.yml")
    assert scenario.compose_file.exists()
    assert scenario.compose_file.name == "docker-compose.yml"
    assert scenario.compose_env["SELFTEST_SOURCE_IMAGE"] == "postgres:16"
    assert scenario.compose_env["SELFTEST_TARGET_IMAGE"] == "mariadb:11"
    assert scenario.compose_env["SELFTEST_SOURCE_ENV_FILE"].endswith("source.env")
    assert scenario.compose_env["SELFTEST_TARGET_ENV_FILE"].endswith("target.env")
    assert scenario.source_schema_file.exists()
    assert scenario.source_seed_file.exists()
    assert scenario.source_service == "source"
    assert scenario.source_schema_command[0] == "psql"
    assert scenario.source_seed_command[0] == "psql"


def test_mariadb_to_postgres_selftest_scenario_files_exist(tmp_path) -> None:
    scenario = _load_scenario(SelfTestOptions(scenario="mariadb_to_pg", large_rows=10, work_dir=tmp_path))

    assert scenario.config_file.exists()
    assert scenario.config_file.as_posix().endswith("/generated-configs/mariadb_to_pg/config.yml")
    assert scenario.compose_file.exists()
    assert scenario.compose_file.name == "docker-compose.yml"
    assert scenario.compose_env["SELFTEST_SOURCE_IMAGE"] == "mariadb:11"
    assert scenario.compose_env["SELFTEST_TARGET_IMAGE"] == "postgres:16"
    assert scenario.compose_env["SELFTEST_SOURCE_ENV_FILE"].endswith("source.env")
    assert scenario.compose_env["SELFTEST_TARGET_ENV_FILE"].endswith("target.env")
    assert scenario.source_service == "source"
    assert scenario.source_schema_file.as_posix().endswith("/source/mariadb/schema.sql")
    assert scenario.source_seed_file.as_posix().endswith("/source/mariadb/seed.sql")
    assert scenario.source_schema_command == (
        "sh",
        "-c",
        "mariadb --protocol=TCP -h 127.0.0.1 -u source_user -psource_pass source < {script_path}",
    )


def test_selftest_rejects_negative_large_rows() -> None:
    with pytest.raises(SelfTestError, match="large_rows"):
        _load_scenario(SelfTestOptions(large_rows=-1))
