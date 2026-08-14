import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QAbstractItemView

from db_migrator.application import TableSelection
from db_migrator.config.models import AppConfig
from db_migrator.gui import main as gui_main


@pytest.fixture()
def window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> gui_main.MainWindow:
    monkeypatch.setenv("JIGRATION_GUI_STATE_DB", str(tmp_path / "gui-state.sqlite"))
    app = QApplication.instance() or QApplication([])
    gui_main.jigration_stylesheet()
    created = gui_main.MainWindow()
    yield created
    created.close()
    app.processEvents()


def test_table_grid_populates_roles_and_selection(window: gui_main.MainWindow) -> None:
    window._populate_tables((_table("public.account", 26), _table("public.audit_event", None), _table("public.unknown_count", -1)))

    assert window.table_list.rowCount() == 3
    assert window._selected_tables() == {"public.account", "public.audit_event", "public.unknown_count"}
    assert window.table_selected_count.text() == "3 / 3 선택"
    assert window.table_selected_count_value.text() == "3"
    assert window.table_selected_count_suffix.text() == "/ 3 선택"
    assert window.table_summary_count.text() == "● 3"
    assert window.table_summary.text() == "개 테이블을 불러왔습니다"
    assert window.table_list.item(0, 1).text() == ""
    assert window.table_list.item(0, 3).text() == ""
    assert window.table_list.item(0, 4).text() == "26"
    assert window.table_list.item(1, 4).text() == "-"
    assert window.table_list.item(2, 4).text() == "-"
    assert _table_name_labels(window, 0, 1, "tableNamePrimary") == ["public.account"]
    assert _table_name_labels(window, 0, 1, "tableNameSecondary") == ["postgresql"]
    assert _table_name_labels(window, 0, 3, "tableNamePrimary") == ["target.account"]
    assert _table_name_labels(window, 0, 3, "tableNameSecondary") == ["mysql · 신규"]


def test_table_grid_select_all_syncs_role_and_checkbox(window: gui_main.MainWindow) -> None:
    window._populate_tables((_table("public.account", 26), _table("public.audit_event", 1_204)))

    window._set_all_tables(False)

    assert window._selected_tables() == set()
    assert window.table_selected_count.text() == "0 / 2 선택"
    assert window.table_selected_count_value.text() == "0"
    assert window.table_selected_count_suffix.text() == "/ 2 선택"
    assert all(not window._row_checkbox(item).isChecked() for item in window._iter_table_items())

    window._set_all_tables(True)

    assert window._selected_tables() == {"public.account", "public.audit_event"}
    assert window.table_selected_count.text() == "2 / 2 선택"
    assert window.table_selected_count_value.text() == "2"
    assert window.table_selected_count_suffix.text() == "/ 2 선택"
    assert all(window._row_checkbox(item).isChecked() for item in window._iter_table_items())


def test_table_grid_focus_indicator_stays_on_left_selector_cell(window: gui_main.MainWindow) -> None:
    window._populate_tables((_table("public.account", 26), _table("public.audit_event", 1_204)))

    window.table_list.selectRow(1)
    window._refresh_table_focus_indicators()

    assert window.table_list.selectionMode() == QAbstractItemView.SelectionMode.SingleSelection
    assert window.table_list.cellWidget(0, 0).property("focused") is False
    assert window.table_list.cellWidget(1, 0).property("focused") is True


def test_table_grid_search_hides_rows_without_rebuilding(window: gui_main.MainWindow) -> None:
    window._populate_tables((_table("public.account", 26), _table("public.audit_event", 1_204)))
    first_item = window._table_row_item(0)

    window._filter_tables("audit")

    assert window.table_list.isRowHidden(0)
    assert not window.table_list.isRowHidden(1)
    assert window._table_row_item(0) is first_item

    window._filter_tables("")

    assert not window.table_list.isRowHidden(0)
    assert not window.table_list.isRowHidden(1)


def test_table_grid_saves_custom_target_table_config(window: gui_main.MainWindow) -> None:
    window._populate_tables((_table("public.account", 26),))
    item = window._table_row_item(0)
    item.setData(gui_main.TARGET_SCHEMA_ROLE, "archive")
    item.setData(gui_main.TARGET_TABLE_ROLE, "account_archive")
    item.setData(gui_main.WATERMARK_COLUMN_ROLE, "updated_at")
    item.setData(gui_main.WATERMARK_START_ROLE, "2026-08-01 00:00:00")
    config = AppConfig()

    window._save_table_run_configs(config)

    saved = config.tables["public.account"]
    assert saved.target_schema == "archive"
    assert saved.target_table == "account_archive"
    assert saved.incremental.watermark_column == "updated_at"
    assert saved.incremental.start_value == "2026-08-01 00:00:00"


def test_clear_screen_log_only_clears_plain_text(window: gui_main.MainWindow) -> None:
    window.log.setPlainText("INFO something happened")

    window._clear_screen_log()

    assert window.log.toPlainText() == ""


def _table(identifier: str, rows: int | None) -> TableSelection:
    schema, table = identifier.rsplit(".", 1)
    return TableSelection(
        identifier=identifier,
        schema=schema,
        table=table,
        column_count=3,
        estimated_rows=rows,
        has_primary_key=True,
    )


def _table_name_labels(window: gui_main.MainWindow, row: int, column: int, object_name: str) -> list[str]:
    widget = window.table_list.cellWidget(row, column)
    assert widget is not None
    return [label.text() for label in widget.findChildren(QLabel, object_name)]
