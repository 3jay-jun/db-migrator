from unittest.mock import patch

from db_migrator.core.health import run_health_checks


def test_run_health_checks_reports_writable_dirs(tmp_path) -> None:
    report = run_health_checks(tmp_path)

    writable_checks = [check for check in report.checks if check.name.startswith("writable:")]
    assert {check.name for check in writable_checks} == {
        "writable:reports",
        "writable:checkpoints",
        "writable:logs",
    }
    assert all(check.status == "ok" for check in writable_checks)


def test_run_health_checks_treats_missing_optional_tools_as_warning(tmp_path) -> None:
    with patch("shutil.which", return_value=None):
        report = run_health_checks(tmp_path)

    tool_checks = [check for check in report.checks if check.name.startswith("tool:")]
    assert all(check.status == "warning" for check in tool_checks)
    assert report.ok is True
