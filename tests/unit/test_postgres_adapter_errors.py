from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

from db_migrator.adapters.postgres import PostgresAdapterError, PostgresSourceAdapter
from db_migrator.config.models import SourceConfig


def test_scan_schema_reports_connection_context(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(SourceConfig(database="wrong_db", user="readonly"))

    def fail_connect() -> object:
        raise RuntimeError("database does not exist")

    monkeypatch.setattr(adapter, "_connect", fail_connect)

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.scan_schema("public")

    message = str(exc_info.value)
    assert "connection failed before schema scan" in message
    assert "database=wrong_db" in message
    assert "schema=public" in message
    assert "database does not exist" in message


def test_scan_schema_reports_metadata_query_context(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(SourceConfig(database="legacy"))

    @contextmanager
    def connect() -> Iterator[object]:
        yield object()

    def fail_fetch_columns(connection: object, schema: str) -> list[dict[str, object]]:
        raise RuntimeError("permission denied for information_schema")

    monkeypatch.setattr(adapter, "_connect", connect)
    monkeypatch.setattr(adapter, "_fetch_columns", fail_fetch_columns)

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.scan_schema("private")

    message = str(exc_info.value)
    assert "schema metadata query failed" in message
    assert "schema 'private'" in message
    assert "permission denied for information_schema" in message
