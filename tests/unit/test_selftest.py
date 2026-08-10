from unittest.mock import patch

from db_migrator.selftest.package_check import check_pyinstaller_available
from db_migrator.selftest.runner import DOCKER_MISSING_MESSAGE, check_docker_available


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
