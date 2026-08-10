from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db_migrator.schema.models import RowBatch, TableRef


@dataclass(frozen=True)
class BatchCheckpoint:
    job_id: str
    table: TableRef
    batch_number: int
    committed_rows: int
    next_offset: int | None
    status: str
    message: str | None = None


class CheckpointStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save_batch_success(self, job_id: str, batch: RowBatch) -> BatchCheckpoint:
        next_offset = batch.next_cursor.offset if batch.next_cursor is not None else None
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=batch.table,
            batch_number=batch.batch_number,
            committed_rows=batch.row_count,
            next_offset=next_offset,
            status="completed",
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def save_batch_failure(self, job_id: str, batch: RowBatch, message: str) -> BatchCheckpoint:
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=batch.table,
            batch_number=batch.batch_number,
            committed_rows=0,
            next_offset=batch.start_offset,
            status="failed",
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
    ) -> BatchCheckpoint:
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=table,
            batch_number=batch_number,
            committed_rows=0,
            next_offset=next_offset,
            status="cancelled",
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
    ) -> BatchCheckpoint:
        checkpoint = BatchCheckpoint(
            job_id=job_id,
            table=table,
            batch_number=batch_number,
            committed_rows=committed_rows,
            next_offset=next_offset,
            status="table_completed",
        )
        self._insert_checkpoint(checkpoint)
        return checkpoint

    def list_checkpoints(self, job_id: str) -> list[BatchCheckpoint]:
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute(
                """
                select job_id, schema_name, table_name, batch_number, committed_rows, next_offset, status, message
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
                message=row[7],
            )
            for row in rows
        ]

    def latest_checkpoint_for_table(self, job_id: str, table: TableRef) -> BatchCheckpoint | None:
        with sqlite3.connect(self._db_path) as connection:
            row = connection.execute(
                """
                select job_id, schema_name, table_name, batch_number, committed_rows, next_offset, status, message
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
            message=row[7],
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
                    message text,
                    saved_at text not null
                )
                """
            )
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
                    message,
                    saved_at
                )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.job_id,
                    checkpoint.table.schema,
                    checkpoint.table.name,
                    checkpoint.batch_number,
                    checkpoint.committed_rows,
                    checkpoint.next_offset,
                    checkpoint.status,
                    checkpoint.message,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
