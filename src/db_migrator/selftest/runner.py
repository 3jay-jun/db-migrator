from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


DOCKER_MISSING_MESSAGE = "Docker is not installed or not running. Self-test requires Docker Desktop."


@dataclass(frozen=True)
class SelfTestResult:
    success: bool
    message: str


def check_docker_available() -> SelfTestResult:
    docker_path = shutil.which("docker")
    if docker_path is None:
        return SelfTestResult(success=False, message=DOCKER_MISSING_MESSAGE)

    completed = subprocess.run(
        [docker_path, "info"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        return SelfTestResult(success=False, message=DOCKER_MISSING_MESSAGE)

    return SelfTestResult(success=True, message="Docker is available.")


def run_self_test(compose_file: Path) -> SelfTestResult:
    docker_check = check_docker_available()
    if not docker_check.success:
        return docker_check

    completed = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return SelfTestResult(success=False, message=completed.stderr.strip() or "Docker compose config failed.")

    return SelfTestResult(success=True, message="Self-test prerequisites passed.")
