from __future__ import annotations

from typing import Protocol


class ConnectionConfig(Protocol):
    host: str
    port: int
    database: str
    user: str


def safe_error_detail(exc: Exception) -> str:
    detail = str(exc).strip()
    return detail or exc.__class__.__name__


def connection_test_failure_message(dbms_label: str, config: ConnectionConfig, exc: Exception) -> str:
    return (
        f"{dbms_label} connection test failed. "
        f"host={config.host} port={config.port} database={config.database} user={config.user} "
        f"detail={safe_error_detail(exc)}"
    )
