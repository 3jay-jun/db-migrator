from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest

from db_migrator.adapters.postgres import PostgresAdapterError, PostgresSourceAdapter, PostgresTargetAdapter
from db_migrator.config.models import SourceConfig, TargetConfig
from db_migrator.schema.models import ColumnSchema, SamplePosition, TableRef, TableSchema
from db_migrator.schema.type_mapping import postgres_type_to_common


class TupleCursor:
    def __init__(self, rows: tuple[tuple, ...]) -> None:
        self._rows = rows

    def __enter__(self) -> "TupleCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, _sql: str, _params: tuple | None = None) -> None:
        return None

    def fetchall(self) -> tuple[tuple, ...]:
        return self._rows


class TupleConnection:
    def __init__(self, rows: tuple[tuple, ...]) -> None:
        self._rows = rows

    def __enter__(self) -> "TupleConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> TupleCursor:
        return TupleCursor(self._rows)


class CountCursor:
    def __init__(self, count: int) -> None:
        self._count = count

    def __enter__(self) -> "CountCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, _sql: str, _params: tuple | None = None) -> None:
        return None

    def fetchone(self) -> tuple[int]:
        return (self._count,)


class CountConnection:
    def __init__(self, count: int) -> None:
        self._count = count
        self.closed = False

    def __enter__(self) -> "CountConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> CountCursor:
        return CountCursor(self._count)

    def close(self) -> None:
        self.closed = True


class FailingCursor:
    def __init__(self, message: str) -> None:
        self._message = message

    def __enter__(self) -> "FailingCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, _sql: str, _params: tuple | None = None) -> None:
        raise RuntimeError(self._message)


class FailingConnection:
    def __init__(self, message: str) -> None:
        self._message = message

    def __enter__(self) -> "FailingConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FailingCursor:
        return FailingCursor(self._message)


def _account_table() -> TableSchema:
    return TableSchema(
        ref=TableRef(schema="public", name="account"),
        columns=(
            ColumnSchema(
                name="id",
                source_type="bigint",
                common_type=postgres_type_to_common("bigint"),
                nullable=False,
                default=None,
                is_generated=False,
                generation_expression=None,
                ordinal_position=1,
            ),
        ),
    )


def test_source_connection_test_reports_driver_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(
        SourceConfig(host="127.0.0.1", port=15432, database="source_db", user="readonly", password="secret")
    )

    def fail_connect() -> object:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(adapter, "_connect", fail_connect)

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.test_connection()

    message = str(exc_info.value)
    assert "PostgreSQL connection test failed" in message
    assert "host=127.0.0.1" in message
    assert "port=15432" in message
    assert "database=source_db" in message
    assert "user=readonly" in message
    assert "detail=connection refused" in message
    assert "secret" not in message


def test_target_connection_test_reports_driver_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresTargetAdapter(
        TargetConfig(host="127.0.0.1", port=15433, database="target_db", user="migration", password="secret")
    )

    def fail_connect() -> object:
        raise RuntimeError("password authentication failed")

    monkeypatch.setattr(adapter, "_connect", fail_connect)

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.test_connection()

    message = str(exc_info.value)
    assert "PostgreSQL connection test failed" in message
    assert "host=127.0.0.1" in message
    assert "port=15433" in message
    assert "database=target_db" in message
    assert "user=migration" in message
    assert "detail=password authentication failed" in message
    assert "secret" not in message


def test_postgres_fetch_rows_by_keys_accepts_tuple_cursor_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresTargetAdapter(TargetConfig(database="target_db", password="secret"))
    monkeypatch.setattr(adapter, "_connect", lambda: TupleConnection(((1,),)))

    rows = adapter.fetch_rows_by_keys(_account_table(), ("id",), ({"id": 1},))

    assert rows == {(1,): {"id": 1}}


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


def test_source_count_rows_reports_validation_query_context(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(
        SourceConfig(host="10.0.0.10", port=15432, database="legacy", user="readonly", password="secret")
    )
    monkeypatch.setattr(adapter, "_connect", lambda: FailingConnection("server closed the connection unexpectedly"))

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.count_rows(TableRef(schema="public", name="account"))

    message = str(exc_info.value)
    assert "PostgreSQL row count failed for table: public.account." in message
    assert "host=10.0.0.10" in message
    assert "port=15432" in message
    assert "database=legacy" in message
    assert "user=readonly" in message
    assert 'sql=select count(*) from "public"."account"' in message
    assert "detail=server closed the connection unexpectedly" in message
    assert "secret" not in message


def test_source_count_rows_retries_transient_connection_abort(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(SourceConfig(database="legacy", user="readonly", password="secret"))
    attempts = {"count": 0}

    def connect() -> object:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("connection failed: could not receive data from server: Software caused connection abort")
        return CountConnection(7)

    monkeypatch.setattr("db_migrator.adapters.postgres.sleep", lambda _seconds: None)
    monkeypatch.setattr(adapter, "_connect", connect)

    assert adapter.count_rows(TableRef(schema="public", name="account")) == 7
    assert attempts["count"] == 3


def test_source_validation_queries_reuse_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(SourceConfig(database="legacy", user="readonly", password="secret"))
    attempts = {"count": 0}

    def connect() -> CountConnection:
        attempts["count"] += 1
        return CountConnection(7)

    monkeypatch.setattr(adapter, "_connect", connect)

    table = TableRef(schema="public", name="account")
    assert adapter.count_rows(table) == 7
    assert adapter.count_rows(table) == 7
    assert attempts["count"] == 1


def test_source_count_rows_does_not_retry_non_transient_query_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresSourceAdapter(SourceConfig(database="legacy", user="readonly", password="secret"))
    attempts = {"count": 0}

    def connect() -> object:
        attempts["count"] += 1
        return FailingConnection("permission denied for table account")

    monkeypatch.setattr("db_migrator.adapters.postgres.sleep", lambda _seconds: None)
    monkeypatch.setattr(adapter, "_connect", connect)

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.count_rows(TableRef(schema="public", name="account"))

    message = str(exc_info.value)
    assert attempts["count"] == 1
    assert "attempts=1" in message
    assert "permission denied for table account" in message


def test_target_sample_rows_reports_validation_query_context(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PostgresTargetAdapter(
        TargetConfig(host="10.0.0.11", port=15433, database="target", user="migration", password="secret")
    )
    monkeypatch.setattr(adapter, "_connect", lambda: FailingConnection("permission denied for table account"))

    with pytest.raises(PostgresAdapterError) as exc_info:
        adapter.sample_rows(TableRef(schema="public", name="account"), ("id", "email"), 20, ("id",), SamplePosition.LAST)

    message = str(exc_info.value)
    assert "PostgreSQL sample rows failed for table: public.account." in message
    assert "host=10.0.0.11" in message
    assert "port=15433" in message
    assert "database=target" in message
    assert "user=migration" in message
    assert 'sql=select "id", "email" from "public"."account" order by "id" desc limit %s' in message
    assert "columns=id,email" in message
    assert "order_by=id" in message
    assert "sample_size=20" in message
    assert "position=last" in message
    assert "detail=permission denied for table account" in message
    assert "secret" not in message
