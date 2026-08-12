from __future__ import annotations

import pytest

from db_migrator.adapters.mysql import MySqlAdapterError, MySqlTargetAdapter
from db_migrator.config.models import TargetConfig
from db_migrator.schema.models import ColumnSchema, TableRef, TableSchema
from db_migrator.schema.type_mapping import postgres_type_to_common


class FailingCursor:
    def __init__(self, *, execute_error: Exception | None = None, executemany_error: Exception | None = None) -> None:
        self._execute_error = execute_error
        self._executemany_error = executemany_error

    def __enter__(self) -> "FailingCursor":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, _sql: str, _params: tuple | None = None) -> None:
        if self._execute_error is not None:
            raise self._execute_error

    def executemany(self, _sql: str, _values: list[tuple]) -> None:
        if self._executemany_error is not None:
            raise self._executemany_error


class FakeConnection:
    def __init__(self, cursor: FailingCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self) -> FailingCursor:
        return self._cursor


def _account_table() -> TableSchema:
    return TableSchema(
        ref=TableRef(schema="hd_bb", name="account2"),
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


def test_mysql_write_batch_reports_driver_detail() -> None:
    adapter = MySqlTargetAdapter(TargetConfig(database="hd_bb", password="secret"))
    adapter._connect = lambda: FakeConnection(FailingCursor(executemany_error=RuntimeError("data too long for column")))

    with pytest.raises(MySqlAdapterError) as exc_info:
        adapter.write_batch(_account_table(), ({"id": 1},))

    message = str(exc_info.value)
    assert "Failed to write target batch for table: account2" in message
    assert "detail=data too long for column" in message
    assert "secret" not in message


def test_mysql_sample_rows_reports_driver_detail() -> None:
    adapter = MySqlTargetAdapter(TargetConfig(database="hd_bb", password="secret"))
    adapter._connect = lambda: FakeConnection(FailingCursor(execute_error=RuntimeError("unknown column id_")))

    with pytest.raises(MySqlAdapterError) as exc_info:
        adapter.sample_rows(TableRef(schema="hd_bb", name="account2"), ("id_",), 20, ("id_",))

    message = str(exc_info.value)
    assert "Failed to sample target rows for table: account2" in message
    assert "detail=unknown column id_" in message
    assert "secret" not in message
