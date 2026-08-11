from db_migrator.adapters.mysql import MySqlSourceAdapter
from db_migrator.config.models import SourceConfig
from db_migrator.schema.models import TableRef


class FakeServerSideCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed_sql: str | None = None
        self.executed_params: tuple | None = None
        self.fetch_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.executed_sql = sql
        self.executed_params = params

    def fetchmany(self, size: int) -> tuple[dict, ...]:
        self.fetch_sizes.append(size)
        chunk = tuple(self._rows[:size])
        del self._rows[:size]
        return chunk


class FakeConnection:
    def __init__(self, cursor: FakeServerSideCursor) -> None:
        self.cursor_instance = cursor
        self.cursor_class_name: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def cursor(self, cursor_class):
        self.cursor_class_name = cursor_class.__name__
        return self.cursor_instance


def test_mysql_source_read_rows_uses_server_side_dict_cursor() -> None:
    cursor = FakeServerSideCursor(rows=[{"id": 1}, {"id": 2}, {"id": 3}])
    connection = FakeConnection(cursor)
    adapter = MySqlSourceAdapter(SourceConfig(database="legacy_db", schema="legacy_db"))
    adapter._connect = lambda: connection

    batches = tuple(
        adapter.read_rows(
            TableRef(schema="legacy_db", name="accounts"),
            columns=("id",),
            cursor=None,
            batch_size=2,
            order_by=("id",),
        )
    )

    assert connection.cursor_class_name == "SSDictCursor"
    assert cursor.executed_params == (0,)
    assert "LIMIT %s, 18446744073709551615" in (cursor.executed_sql or "")
    assert [batch.row_count for batch in batches] == [2, 1]
    assert batches[-1].next_cursor is not None
    assert batches[-1].next_cursor.offset == 3
