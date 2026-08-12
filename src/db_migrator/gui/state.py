from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuiPathState:
    config_path: str
    schema_path: str
    output_dir: str
    checkpoint_path: str


class GuiStateStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or default_gui_state_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def load_paths(self, defaults: GuiPathState) -> GuiPathState:
        values = self._load_values()
        return GuiPathState(
            config_path=values.get("config_path") or defaults.config_path,
            schema_path=values.get("schema_path") or defaults.schema_path,
            output_dir=values.get("output_dir") or defaults.output_dir,
            checkpoint_path=values.get("checkpoint_path") or defaults.checkpoint_path,
        )

    def save_paths(self, state: GuiPathState) -> None:
        with self._connect() as connection:
            connection.executemany(
                """
                insert into gui_state (key, value) values (?, ?)
                on conflict(key) do update set value = excluded.value
                """,
                (
                    ("config_path", state.config_path),
                    ("schema_path", state.schema_path),
                    ("output_dir", state.output_dir),
                    ("checkpoint_path", state.checkpoint_path),
                ),
            )

    @property
    def path(self) -> Path:
        return self._db_path

    def _load_values(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("select key, value from gui_state").fetchall()
        return {str(key): str(value) for key, value in rows}

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists gui_state (
                    key text primary key,
                    value text not null
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)


def default_gui_state_db_path() -> Path:
    override = os.environ.get("JIGRATION_GUI_STATE_DB")
    if override:
        return Path(override).expanduser()
    app_data = os.environ.get("APPDATA")
    if app_data:
        return Path(app_data) / "Jigration" / "gui-state.sqlite"
    return Path.home() / ".jigration" / "gui-state.sqlite"
