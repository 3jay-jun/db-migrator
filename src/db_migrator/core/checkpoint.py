from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from typing import Any

from db_migrator.schema.models import CursorStrategy, ReadCursor, RowBatch, TableRef


@dataclass(frozen=True)
class BatchCheckpoint:
    job_id: str
    table: TableRef
    batch_number: int
    committed_rows: int
    next_offset: int | None
    status: str
    cursor_strategy: str = CursorStrategy.OFFSET.value
    key_columns: tuple[str, ...] = ()
    last_key_values: tuple[Any, ...] = ()
    message: str | None = None

    @property
    def next_cursor(self) -> ReadCursor:
        if self.cursor_strategy == CursorStrategy.KEYSET.value:
            return ReadCursor.keyset_cursor(
                key_columns=self.key_columns,
                last_key_values=self.last_key_values,
                offset=self.next_offset or 0,
            )
        return ReadCursor.offset_cursor(self.next_offset or 0)


class CheckpointStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_batch_success(self, job_id: str, batch: RowBatch) -> BatchCheckpoint:
        next_cursor = batch.next_cursor or ReadCursor.offset_cursor()
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=batch.table,
            batch_number=batch.batch_number,
            committed_rows=batch.row_count,
            next_offset=next_cursor.offset,
            status="completed",
            cursor_strategy=next_cursor.strategy.value,
            key_columns=next_cursor.key_columns,
            last_key_values=next_cursor.last_key_values,
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def save_batch_failure(self, job_id: str, batch: RowBatch, message: str) -> BatchCheckpoint:
        failure_cursor = batch.failure_cursor
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=batch.table,
            batch_number=batch.batch_number,
            committed_rows=0,
            next_offset=failure_cursor.offset,
            status="failed",
            cursor_strategy=failure_cursor.strategy.value,
            key_columns=failure_cursor.key_columns,
            last_key_values=failure_cursor.last_key_values,
            message=message,
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def save_checkpoint_failure_after_commit(self, job_id: str, batch: RowBatch, message: str) -> BatchCheckpoint:
        next_cursor = batch.next_cursor or ReadCursor.offset_cursor(batch.start_offset + batch.row_count)
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=batch.table,
            batch_number=batch.batch_number,
            committed_rows=batch.row_count,
            next_offset=next_cursor.offset,
            status="checkpoint_failed_after_commit",
            cursor_strategy=next_cursor.strategy.value,
            key_columns=next_cursor.key_columns,
            last_key_values=next_cursor.last_key_values,
            message=message,
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def save_table_cancelled(
        self,
        job_id: str,
        table: TableRef,
        batch_number: int,
        next_offset: int | None,
        message: str,
        cursor: ReadCursor | None = None,
    ) -> BatchCheckpoint:
        saved_cursor = cursor or ReadCursor.offset_cursor(next_offset or 0)
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=table,
            batch_number=batch_number,
            committed_rows=0,
            next_offset=saved_cursor.offset,
            status="cancelled",
            cursor_strategy=saved_cursor.strategy.value,
            key_columns=saved_cursor.key_columns,
            last_key_values=saved_cursor.last_key_values,
            message=message,
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def save_table_completed(
        self,
        job_id: str,
        table: TableRef,
        batch_number: int,
        next_offset: int | None,
        committed_rows: int,
        cursor: ReadCursor | None = None,
    ) -> BatchCheckpoint:
        saved_cursor = cursor or ReadCursor.offset_cursor(next_offset or 0)
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=table,
            batch_number=batch_number,
            committed_rows=committed_rows,
            next_offset=saved_cursor.offset,
            status="table_completed",
            cursor_strategy=saved_cursor.strategy.value,
            key_columns=saved_cursor.key_columns,
            last_key_values=saved_cursor.last_key_values,
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def list_checkpoints(self, job_id: str) -> list[BatchCheckpoint]:
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute(
                """
                select job_id, schema_name, table_name, batch_number, committed_rows, next_offset, status,
                       cursor_strategy, key_columns_json, last_key_values_json, message
                from batch_checkpoints
                where job_id = ?
                order by schema_name, table_name, batch_number
                """,
                (job_id,),
            ).fetchall()
        return [
            BatchCheckpoint(
                job_id=row[0],
                table=TableRef(schema=row[1], name=row[2]),
                batch_number=row[3],
                committed_rows=row[4],
                next_offset=row[5],
                status=row[6],
                cursor_strategy=row[7],
                key_columns=_loads_tuple(row[8]),
                last_key_values=_loads_tuple(row[9]),
                message=row[10],
            )
            for row in rows
        ]

    def latest_checkpoint_for_table(self, job_id: str, table: TableRef) -> BatchCheckpoint | None:
        with sqlite3.connect(self._db_path) as connection:
            row = connection.execute(
                """
                select job_id, schema_name, table_name, batch_number, committed_rows, next_offset, status,
                       cursor_strategy, key_columns_json, last_key_values_json, message
                from batch_checkpoints
                where job_id = ?
                  and schema_name = ?
                  and table_name = ?
                order by id desc
                limit 1
                """,
                (job_id, table.schema, table.name),
            ).fetchone()
        if row is None:
            return None
        return BatchCheckpoint(
            job_id=row[0],
            table=TableRef(schema=row[1], name=row[2]),
            batch_number=row[3],
            committed_rows=row[4],
            next_offset=row[5],
            status=row[6],
            cursor_strategy=row[7],
            key_columns=_loads_tuple(row[8]),
            last_key_values=_loads_tuple(row[9]),
            message=row[10],
        )

    def failed_tables(self, job_id: str) -> tuple[TableRef, ...]:
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute(
                """
                select c.schema_name, c.table_name
                from batch_checkpoints c
                join (
                    select schema_name, table_name, max(id) as latest_id
                    from batch_checkpoints
                    where job_id = ?
                    group by schema_name, table_name
                ) latest
                  on latest.latest_id = c.id
                where c.job_id = ?
                  and c.status = 'failed'
                order by c.schema_name, c.table_name
                """,
                (job_id, job_id),
            ).fetchall()
        return tuple(TableRef(schema=row[0], name=row[1]) for row in rows)

    def _initialize(self) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                create table if not exists batch_checkpoints (
                    id integer primary key autoincrement,
                    job_id text not null,
                    schema_name text not null,
                    table_name text not null,
                    batch_number integer not null,
                    committed_rows integer not null,
                    next_offset integer,
                    status text not null,
                    cursor_strategy text not null default 'offset',
                    key_columns_json text not null default '[]',
                    last_key_values_json text not null default '[]',
                    message text,
                    saved_at text not null
                )
                """
            )
            _ensure_column(connection, "batch_checkpoints", "cursor_strategy", "text not null default 'offset'")
            _ensure_column(connection, "batch_checkpoints", "key_columns_json", "text not null default '[]'")
            _ensure_column(connection, "batch_checkpoints", "last_key_values_json", "text not null default '[]'")
            connection.execute(
                """
                create index if not exists idx_batch_checkpoints_job_table
                on batch_checkpoints (job_id, schema_name, table_name, batch_number)
                """
            )

    def _insert_checkpoint(self, checkpoint: BatchCheckpoint) -> None:
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                insert into batch_checkpoints (
                    job_id,
                    schema_name,
                    table_name,
                    batch_number,
                    committed_rows,
                    next_offset,
                    status,
                    cursor_strategy,
                    key_columns_json,
                    last_key_values_json,
                    message,
                    saved_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.job_id,
                    checkpoint.table.schema,
                    checkpoint.table.name,
                    checkpoint.batch_number,
                    checkpoint.committed_rows,
                    checkpoint.next_offset,
                    checkpoint.status,
                    checkpoint.cursor_strategy,
                    json.dumps(checkpoint.key_columns, ensure_ascii=False),
                    json.dumps(checkpoint.last_key_values, ensure_ascii=False, default=str),
                    checkpoint.message,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


def _ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
    columns = {row[1] for row in connection.execute(f"pragma table_info({table_name})").fetchall()}
    if column_name not in columns:
        connection.execute(f"alter table {table_name} add column {column_name} {definition}")


def _loads_tuple(raw_value: str | None) -> tuple[Any, ...]:
    if not raw_value:
        return ()
    value = json.loads(raw_value)
    if not isinstance(value, list):
        return ()
    return tuple(value)
