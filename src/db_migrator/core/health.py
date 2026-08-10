from __future__ import annotations

import importlib.util
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: str
    message: str


@dataclass(frozen=True)
class HealthReport:
    checks: tuple[HealthCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.status in {"ok", "warning"} for check in self.checks)


def run_health_checks(project_root: Path) -> HealthReport:
    return HealthReport(
        checks=(
            _check_python_version(),
            *_check_required_imports(("typer", "pydantic", "yaml", "rich", "psycopg", "pymysql")),
            *_check_writable_dirs(project_root, ("reports", "checkpoints", "logs")),
            *_check_optional_tools(("uv", "docker", "pyinstaller")),
        )
    )


def _check_python_version() -> HealthCheck:
    version = platform.python_version_tuple()
    major = int(version[0])
    minor = int(version[1])
    if (major, minor) >= (3, 11):
        return HealthCheck(name="python", status="ok", message=platform.python_version())
    return HealthCheck(name="python", status="error", message="Python 3.11+ is required.")


def _check_required_imports(module_names: tuple[str, ...]) -> tuple[HealthCheck, ...]:
    checks = []
    for module_name in module_names:
        if importlib.util.find_spec(module_name) is None:
            checks.append(HealthCheck(name=f"import:{module_name}", status="error", message="Required package is missing."))
        else:
            checks.append(HealthCheck(name=f"import:{module_name}", status="ok", message="available"))
    return tuple(checks)


def _check_writable_dirs(project_root: Path, dir_names: tuple[str, ...]) -> tuple[HealthCheck, ...]:
    checks = []
    for dir_name in dir_names:
        target_dir = project_root / dir_name
        target_dir.mkdir(parents=True, exist_ok=True)
        probe_path = target_dir / ".db_migrator_write_probe"
        try:
            probe_path.write_text("ok", encoding="utf-8")
            probe_path.unlink()
            checks.append(HealthCheck(name=f"writable:{dir_name}", status="ok", message=str(target_dir)))
        except OSError as exc:
            checks.append(HealthCheck(name=f"writable:{dir_name}", status="error", message=str(exc)))
    return tuple(checks)


def _check_optional_tools(tool_names: tuple[str, ...]) -> tuple[HealthCheck, ...]:
    checks = []
    for tool_name in tool_names:
        tool_path = shutil.which(tool_name)
        if tool_path is None:
            checks.append(HealthCheck(name=f"tool:{tool_name}", status="warning", message="not found"))
        else:
            checks.append(HealthCheck(name=f"tool:{tool_name}", status="ok", message=tool_path))
    return tuple(checks)
