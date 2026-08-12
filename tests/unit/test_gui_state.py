from pathlib import Path

from db_migrator.gui.state import GuiPathState, GuiStateStore


def test_gui_state_store_persists_paths(tmp_path: Path) -> None:
    db_path = tmp_path / "gui-state.sqlite"
    defaults = GuiPathState(
        config_path="config.yml",
        schema_path="",
        output_dir="reports/live",
        checkpoint_path="checkpoints/migration.sqlite",
    )

    GuiStateStore(db_path).save_paths(
        GuiPathState(
            config_path="D:/project/migration.yml",
            schema_path="D:/project/schema.json",
            output_dir="D:/project/reports",
            checkpoint_path="D:/project/checkpoint.sqlite",
        )
    )

    loaded = GuiStateStore(db_path).load_paths(defaults)

    assert loaded.config_path == "D:/project/migration.yml"
    assert loaded.schema_path == "D:/project/schema.json"
    assert loaded.output_dir == "D:/project/reports"
    assert loaded.checkpoint_path == "D:/project/checkpoint.sqlite"


def test_gui_state_store_uses_defaults_for_missing_values(tmp_path: Path) -> None:
    defaults = GuiPathState(
        config_path="config.yml",
        schema_path="",
        output_dir="reports/live",
        checkpoint_path="checkpoints/migration.sqlite",
    )

    loaded = GuiStateStore(tmp_path / "gui-state.sqlite").load_paths(defaults)

    assert loaded == defaults
