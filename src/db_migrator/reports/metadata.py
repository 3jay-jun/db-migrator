from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportEndpoint:
    dbms: str
    host: str
    port: int
    database: str
    schema: str | None = None
