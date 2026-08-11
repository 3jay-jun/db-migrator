from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from db_migrator.config.models import AppConfig
from db_migrator.schema.models import TableSchema


class OverwriteAuditStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def start_run(self, *, config: AppConfig, table_count: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                insert into overwrite_runs (
                    job_id, target_dbms, target_host, target_database, table_count, status, started_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.job.name,
                    config.target.dbms.value,
                    config.target.host,
                    config.target.database,
                    table_count,
                    "running",
                    _now(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, message: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                "update overwrite_runs set status = ?, message = ?, finished_at = ? where id = ?",
                (status, message, _now(), run_id),
            )

    def record_table_action(
        self,
        run_id: int,
        *,
        table: TableSchema,
        action: str,
        status: str,
        message: str,
        ddl: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                insert into overwrite_table_actions (
                    run_id, source_schema, source_table, action, status, message, ddl, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, table.ref.schema, table.ref.name, action, status, message, ddl, _now()),
            )

    @property
    def path(self) -> Path:
        return self._db_path

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists overwrite_runs (
                    id integer primary key autoincrement,
                    job_id text not null,
                    target_dbms text not null,
                    target_host text not null,
                    target_database text not null,
                    table_count integer not null,
                    status text not null,
                    message text,
                    started_at text not null,
                    finished_at text
                )
                """
            )
            connection.execute(
                """
                create table if not exists overwrite_table_actions (
                    id integer primary key autoincrement,
                    run_id integer not null,
                    source_schema text not null,
                    source_table text not null,
                    action text not null,
                    status text not null,
                    message text not null,
                    ddl text,
                    created_at text not null,
                    foreign key(run_id) references overwrite_runs(id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

