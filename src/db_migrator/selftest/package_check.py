from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class PackageCheckResult:
    success: bool
    message: str


def check_pyinstaller_available() -> PackageCheckResult:
    if shutil.which("pyinstaller") is None:
        return PackageCheckResult(success=False, message="PyInstaller is not installed. Run `uv sync --extra test` first.")
    return PackageCheckResult(success=True, message="PyInstaller is available.")
