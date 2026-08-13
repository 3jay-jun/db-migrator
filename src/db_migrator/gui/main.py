from __future__ import annotations

import json
import sqlite3
import sys
from importlib.resources import as_file, files
from pathlib import Path
from typing import Callable

import yaml

from db_migrator.application import ColumnSelection, CommandResult, MigrationApplicationService, TableSelection
from db_migrator.application.events import event_to_view
from db_migrator.application.safety import evaluate_dry_run_gate
from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import (
    AppConfig,
    ColumnTransformConfig,
    Dbms,
    ExistingTablePolicy,
    IndexApplyTiming,
    MigrationMode,
    SourceOnlyColumnAction,
    SshAuthenticationType,
    SshTunnelConfig,
    TableRunConfig,
)
from db_migrator.connection import SshConnectionTester, TunnelError
from db_migrator.core.events import EventPublisher, FileEventPublisher, MigrationEvent
from db_migrator.gui.state import GuiPathState, GuiStateStore
from db_migrator.schema.common_types import CommonTypeKind
from db_migrator.schema.type_mapping import common_type_to_mysql, common_type_to_postgres, mysql_type_to_common, postgres_type_to_common


def main() -> None:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        raise SystemExit("PySide6 is not installed. Install the GUI extra before running jigration-gui.") from exc

    app = QApplication(sys.argv)
    app_icon = _app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = MainWindow()
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
    window.show()
    raise SystemExit(app.exec())


try:
    from PySide6.QtCore import QObject, QThread, QUrl, Qt, Signal, Slot
    from PySide6.QtGui import QBrush, QColor, QDesktopServices, QIcon
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QApplication,
        QDialog,
        QDialogButtonBox,
        QFileDialog,
        QFormLayout,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QSpinBox,
        QPlainTextEdit,
        QTabWidget,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    QObject = object  # type: ignore[assignment,misc]
    Signal = None  # type: ignore[assignment]
    Slot = lambda *args, **kwargs: (lambda fn: fn)  # type: ignore[assignment]


if Signal is not None:
    TABLE_ID_ROLE = int(Qt.ItemDataRole.UserRole)
    TARGET_TABLE_ROLE = TABLE_ID_ROLE + 1
    TARGET_SCHEMA_ROLE = TABLE_ID_ROLE + 2
    WATERMARK_COLUMN_ROLE = TABLE_ID_ROLE + 3
    WATERMARK_START_ROLE = TABLE_ID_ROLE + 4
    WATERMARK_END_ROLE = TABLE_ID_ROLE + 5
    COLUMN_COUNT_ROLE = TABLE_ID_ROLE + 6
    ESTIMATED_ROWS_ROLE = TABLE_ID_ROLE + 7
    HAS_PRIMARY_KEY_ROLE = TABLE_ID_ROLE + 8
    TABLE_SELECTED_ROLE = TABLE_ID_ROLE + 9
    SOURCE_ONLY_COLUMNS_ROLE = TABLE_ID_ROLE + 10
    TABLE_COLUMNS_ROLE = TABLE_ID_ROLE + 11
    TABLE_COMMENT_ROLE = TABLE_ID_ROLE + 12
    COLUMN_MAPPINGS_ROLE = TABLE_ID_ROLE + 13
    TYPE_OVERRIDES_ROLE = TABLE_ID_ROLE + 14
    GUI_EXISTING_TABLE_POLICIES = (
        ExistingTablePolicy.SKIP,
        ExistingTablePolicy.APPEND,
        ExistingTablePolicy.SYNC,
        ExistingTablePolicy.OVERWRITE,
    )
    EXISTING_TABLE_POLICY_LABELS = {
        ExistingTablePolicy.SKIP: "건너뛰기",
        ExistingTablePolicy.APPEND: "추가 적재",
        ExistingTablePolicy.SYNC: "동기화",
        ExistingTablePolicy.OVERWRITE: "덮어쓰기",
    }
    GUI_MIGRATION_MODE_LABELS = {
        MigrationMode.DDL_AND_DML: "기본 이관",
        MigrationMode.DDL_ONLY: "테이블 이관",
        MigrationMode.MANUAL_DDL: "수동 이관(DDL)",
        MigrationMode.MANUAL: "수동 이관(DDL + DML)",
    }
    GUI_MIGRATION_MODES_BY_LABEL = {label: mode for mode, label in GUI_MIGRATION_MODE_LABELS.items()}

    class WorkerEventPublisher:
        def __init__(self, emit_event: Callable[[MigrationEvent], None]) -> None:
            self._emit_event = emit_event
            self._file_events = FileEventPublisher()
            self.cancel_requested = False

        def publish(self, event: MigrationEvent) -> None:
            if self.cancel_requested:
                raise KeyboardInterrupt("Operation cancelled by user.")
            self._file_events.publish(event)
            self._emit_event(event)


    class CommandWorker(QObject):
        event_published = Signal(object)
        completed = Signal(object)

        def __init__(self, label: str, command: Callable[[EventPublisher], CommandResult]) -> None:
            super().__init__()
            self._label = label
            self._command = command
            self._publisher: WorkerEventPublisher | None = None

        def cancel(self) -> None:
            if self._publisher is not None:
                self._publisher.cancel_requested = True

        @Slot()
        def run(self) -> None:
            try:
                self._publisher = WorkerEventPublisher(self.event_published.emit)
                result = self._command(self._publisher)
            except KeyboardInterrupt:
                result = CommandResult(command=self._label, success=False, message="작업이 취소되었습니다.")
            except Exception as exc:
                result = CommandResult(command=self._label, success=False, message=f"작업 실행 중 오류가 발생했습니다: {exc}")
            self.completed.emit(result)


    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self._service = MigrationApplicationService()
            self._thread: QThread | None = None
            self._worker: CommandWorker | None = None
            self._running_label: str | None = None
            self._cancel_requested = False
            self._manual_apply_foreign_keys = False
            self._syncing_foreign_key_option = False
            self._source_tunnel_config = SshTunnelConfig()
            self._target_tunnel_config = SshTunnelConfig()
            self._state_store: GuiStateStore | None = self._create_state_store()
            self._last_dry_run_report: Path | None = None
            self._last_report_html: Path | None = None
            self._last_dry_run_tables: dict[str, dict] = {}
            self._target_table_options: tuple[TableSelection, ...] = ()
            self._buttons: list[QPushButton] = []
            self._table_actions: list[QPushButton] = []
            self.setWindowTitle("Jigration")
            app_icon = _app_icon()
            if not app_icon.isNull():
                self.setWindowIcon(app_icon)
            self.resize(1180, 820)
            self._build_ui()
            self._load_config_into_form(show_errors=False)
            self._set_table_actions_enabled(False)

        def _build_ui(self) -> None:
            root = QWidget()
            layout = QVBoxLayout(root)

            path_state = self._load_gui_path_state()
            self.config_path = QLineEdit(path_state.config_path)
            self.schema_path = QLineEdit(path_state.schema_path)
            self.output_dir = QLineEdit(path_state.output_dir)
            self.checkpoint_path = QLineEdit(path_state.checkpoint_path)
            top_bar = QHBoxLayout()
            self.config_label = QLabel(f"설정 파일: {self.config_path.text()}")
            top_bar.addWidget(self.config_label, stretch=1)
            settings_button = QPushButton("설정")
            settings_button.clicked.connect(self._open_settings)
            top_bar.addWidget(settings_button)
            layout.addLayout(top_bar)

            db_grid = QGridLayout()
            db_grid.addWidget(self._source_group(), 0, 0)
            db_grid.addWidget(self._target_group(), 0, 1)
            layout.addLayout(db_grid)

            body = QGridLayout()
            body.addWidget(self._table_group(), 0, 0)
            body.addWidget(self._options_group(), 0, 1)
            layout.addLayout(body, stretch=1)

            actions = QHBoxLayout()
            scan_button = QPushButton("테이블 불러오기")
            scan_button.clicked.connect(self._scan_tables)
            self._buttons.append(scan_button)
            actions.addWidget(scan_button)
            self.review_button = QPushButton("검토 실행")
            self.review_button.clicked.connect(self._run_dry_run)
            self._buttons.append(self.review_button)
            self._table_actions.append(self.review_button)
            actions.addWidget(self.review_button)
            self.migrate_button = QPushButton("실행")
            self.migrate_button.clicked.connect(self._run_migration_from_mode)
            self._buttons.append(self.migrate_button)
            self._table_actions.append(self.migrate_button)
            actions.addWidget(self.migrate_button)
            self.cancel_button = QPushButton("취소")
            self.cancel_button.clicked.connect(self._cancel_running_command)
            self.cancel_button.setEnabled(False)
            actions.addWidget(self.cancel_button)
            layout.addLayout(actions)

            self.recovery_group = QGroupBox("복구 작업")
            recovery_actions = QHBoxLayout(self.recovery_group)
            resume_button = QPushButton("이어서 실행")
            resume_button.clicked.connect(self._run_resume)
            retry_button = QPushButton("실패 테이블 재시도")
            retry_button.clicked.connect(self._run_retry_failed)
            recovery_actions.addWidget(resume_button)
            recovery_actions.addWidget(retry_button)
            self._buttons.extend([resume_button, retry_button])
            self._table_actions.extend([resume_button, retry_button])
            self.recovery_group.setVisible(False)
            layout.addWidget(self.recovery_group)

            self.status = QLabel("준비됨")
            layout.addWidget(self.status)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log, stretch=1)
            self.setCentralWidget(root)

        def _source_group(self) -> QGroupBox:
            group = QGroupBox("원본 DB")
            form = QFormLayout(group)
            self.source_dbms = _combo([dbms.value for dbms in Dbms])
            self.source_host = QLineEdit()
            self.source_port = _spin(1, 65535)
            self.source_database = QLineEdit()
            self.source_schema = QLineEdit()
            self.source_user = QLineEdit()
            self.source_password = QLineEdit()
            self.source_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.source_tunnel_enabled = QCheckBox("Use SSH Tunnel")
            source_tunnel_button = QPushButton("SSH 터널 설정")
            source_tunnel_button.clicked.connect(lambda: self._open_tunnel_settings("source"))
            test_button = QPushButton("원본 연결 테스트")
            test_button.clicked.connect(self._test_source)
            self._buttons.extend([source_tunnel_button, test_button])
            form.addRow("DBMS", self.source_dbms)
            form.addRow("호스트", self.source_host)
            form.addRow("포트", self.source_port)
            form.addRow("데이터베이스", self.source_database)
            form.addRow("스키마", self.source_schema)
            form.addRow("사용자", self.source_user)
            form.addRow("비밀번호", self.source_password)
            form.addRow("SSH 터널", _inline_row(self.source_tunnel_enabled, source_tunnel_button))
            form.addRow("", test_button)
            return group

        def _target_group(self) -> QGroupBox:
            group = QGroupBox("대상 DB")
            form = QFormLayout(group)
            self.target_dbms = _combo([dbms.value for dbms in Dbms])
            self.target_host = QLineEdit()
            self.target_port = _spin(1, 65535)
            self.target_database = QLineEdit()
            self.target_schema = QLineEdit()
            self.target_schema.setPlaceholderText("PostgreSQL 예: public / MySQL·MariaDB는 보통 비워둠")
            self.target_user = QLineEdit()
            self.target_password = QLineEdit()
            self.target_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.target_tunnel_enabled = QCheckBox("Use SSH Tunnel")
            target_tunnel_button = QPushButton("SSH 터널 설정")
            target_tunnel_button.clicked.connect(lambda: self._open_tunnel_settings("target"))
            test_button = QPushButton("대상 연결 테스트")
            test_button.clicked.connect(self._test_target)
            self._buttons.extend([target_tunnel_button, test_button])
            form.addRow("DBMS", self.target_dbms)
            form.addRow("호스트", self.target_host)
            form.addRow("포트", self.target_port)
            form.addRow("데이터베이스", self.target_database)
            form.addRow("기본 대상 스키마", self.target_schema)
            form.addRow("사용자", self.target_user)
            form.addRow("비밀번호", self.target_password)
            form.addRow("SSH 터널", _inline_row(self.target_tunnel_enabled, target_tunnel_button))
            form.addRow("", test_button)
            return group

        def _table_group(self) -> QGroupBox:
            group = QGroupBox("이관 대상 테이블")
            layout = QVBoxLayout(group)
            controls = QHBoxLayout()
            select_all = QPushButton("전체 선택")
            select_none = QPushButton("전체 해제")
            select_all.clicked.connect(lambda: self._set_all_tables(True))
            select_none.clicked.connect(lambda: self._set_all_tables(False))
            controls.addWidget(select_all)
            controls.addWidget(select_none)
            layout.addLayout(controls)
            self.table_list = QListWidget()
            self.table_list.itemDoubleClicked.connect(self._edit_table_settings)
            layout.addWidget(self.table_list, stretch=1)
            self.table_summary = QLabel("원본 DB 연결 후 테이블을 불러오면 이관 대상을 선택할 수 있습니다.")
            self.table_summary.setWordWrap(True)
            layout.addWidget(self.table_summary)
            return group

        def _options_group(self) -> QGroupBox:
            group = QGroupBox("이관 옵션")
            form = QFormLayout(group)
            self.migration_mode = _combo(list(GUI_MIGRATION_MODES_BY_LABEL))
            self.existing_table_policy = _policy_combo()
            self.apply_foreign_keys = QCheckBox("데이터 이관 후 외래키 적용")
            self.migration_mode.currentTextChanged.connect(self._sync_foreign_key_option)
            self.apply_foreign_keys.toggled.connect(self._remember_manual_foreign_key_option)
            self.batch_size = _spin(1, 10_000_000)
            self.commit_interval = _spin(1, 10_000_000)
            self.parallel_table_count = _spin(1, 64)
            self.throttle_sleep_ms = _spin(0, 60_000)
            self.generate_report = QCheckBox("리포트 생성 후 열기")
            self.generate_report.setChecked(True)
            self.auto_validate = QCheckBox("자동 검증")
            self.auto_validate.setChecked(True)
            self.incremental_enabled = QCheckBox("증분 이관 사용")
            form.addRow("실행 방식", self.migration_mode)
            form.addRow("기존 테이블 처리", self.existing_table_policy)
            form.addRow("", self.apply_foreign_keys)
            form.addRow("", self.generate_report)
            form.addRow("", self.auto_validate)
            form.addRow("", self.incremental_enabled)
            note = QLabel("배치 크기, checkpoint 경로, schema snapshot 등은 설정 화면에서 조정합니다.")
            note.setWordWrap(True)
            form.addRow("안내", note)
            return group

        def _add_button(self, layout: QGridLayout, label: str, row: int, column: int, handler: Callable[[], None]) -> None:
            button = QPushButton(label)
            button.clicked.connect(handler)
            layout.addWidget(button, row, column)
            self._buttons.append(button)

        def _choose_config(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Choose config", str(Path.cwd()), "YAML files (*.yml *.yaml);;All files (*)")
            if path:
                self.config_path.setText(path)
                self.config_label.setText(f"설정 파일: {self.config_path.text()}")
                self._save_gui_path_state()
                self._load_config_into_form(show_errors=True)

        def _choose_file(self, line_edit: QLineEdit, title: str) -> None:
            path, _ = QFileDialog.getOpenFileName(self, title, str(Path.cwd()), "All files (*)")
            if path:
                line_edit.setText(path)

        def _choose_schema(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Choose schema snapshot", str(Path.cwd()), "JSON files (*.json);;All files (*)")
            if path:
                self.schema_path.setText(path)
                self._save_gui_path_state()

        def _choose_output_dir(self) -> None:
            path = QFileDialog.getExistingDirectory(self, "Choose output directory", str(Path.cwd()))
            if path:
                self.output_dir.setText(path)
                self._save_gui_path_state()

        def _choose_checkpoint(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Choose checkpoint DB", str(Path.cwd()), "SQLite files (*.sqlite *.db);;All files (*)")
            if path:
                self.checkpoint_path.setText(path)
                self._save_gui_path_state()

        def _test_source(self) -> None:
            if self._save_form_to_config():
                config = self._config()
                self._run("원본 연결 테스트", lambda _publisher: self._service.run_test_source_connection(config=config))

        def _test_target(self) -> None:
            if self._save_form_to_config():
                config = self._config()
                self._run("대상 연결 테스트", lambda _publisher: self._service.run_test_target_connection(config=config))

        def _run_doctor(self) -> None:
            self._run("환경 점검", lambda _publisher: self._service.run_doctor(Path.cwd()))

        def _scan_tables(self) -> None:
            if not self._save_form_to_config():
                return
            config, schema_file, _, _, _ = self._command_context()
            self._run("테이블 불러오기", lambda _publisher: self._run_preflight_then(lambda: self._service.run_scan_tables(config=config, schema_file=schema_file)))

        def _run_dry_run(self) -> None:
            if not self._save_form_to_config() or not self._ensure_tables_selected():
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            self._run(
                "검토 실행",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._service.run_dry_run(
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _show_manual_ddl(self) -> None:
            if not self._save_form_to_config() or not self._ensure_tables_selected():
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            self._run(
                "수동 DDL 생성",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._service.run_generate_manual_ddl(
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_migration_from_mode(self) -> None:
            mode = self.migration_mode.currentText()
            if mode == "기본 이관":
                self._run_full_selected_migration()
            elif mode == "테이블 이관":
                self._run_apply_ddl()
            elif mode == "수동 이관(DDL)":
                self._show_manual_ddl()
            elif mode == "수동 이관(DDL + DML)":
                self._run_manual_migration()

        def _run_apply_ddl(self) -> None:
            if not self._save_form_to_config() or not self._confirm_write_operation("테이블 이관"):
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            dry_run_report = self._last_dry_run_report
            self._run(
                "테이블 이관",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._service.run_apply_ddl(
                        config=config,
                        schema_file=schema_file,
                        output_file=output_dir / "ddl-execution.json",
                        dry_run_report_path=dry_run_report,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_manual_migration(self) -> None:
            if not self._save_form_to_config() or not self._ensure_tables_selected():
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            self._run(
                "수동 이관(DDL + DML)",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._service.run_generate_manual_migration(
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_migrate_data(self) -> None:
            if not self._save_form_to_config() or not self._confirm_write_operation("데이터 이관"):
                return
            config, schema_file, output_dir, checkpoint_db, selected_tables = self._command_context()
            auto_validate = self.auto_validate.isChecked()
            self._run(
                "데이터 이관",
                lambda publisher: self._run_preflight_then(
                    lambda: self._run_data_sequence(
                        publisher,
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        checkpoint_db=checkpoint_db,
                        selected_tables=selected_tables,
                        auto_validate=auto_validate,
                    )
                ),
            )

        def _run_full_selected_migration(self) -> None:
            if not self._save_form_to_config() or not self._confirm_write_operation("기본 이관"):
                return
            config, schema_file, output_dir, checkpoint_db, selected_tables = self._command_context()
            dry_run_report = self._last_dry_run_report
            auto_validate = self.auto_validate.isChecked()
            self._run(
                "기본 이관",
                lambda publisher: self._run_preflight_then(
                    lambda: self._run_full_sequence(
                        publisher,
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        checkpoint_db=checkpoint_db,
                        dry_run_report=dry_run_report,
                        selected_tables=selected_tables,
                        auto_validate=auto_validate,
                    )
                ),
            )

        def _run_resume(self) -> None:
            if not self._save_form_to_config() or not self._confirm_write_operation("이어서 실행"):
                return
            config, schema_file, _, checkpoint_db, selected_tables = self._command_context()
            self._run(
                "이어서 실행",
                lambda publisher: self._run_preflight_then(
                    lambda: self._service.run_resume(
                        config=config,
                        schema_file=schema_file,
                        checkpoint_db=checkpoint_db,
                        event_publisher=publisher,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_retry_failed(self) -> None:
            if not self._save_form_to_config() or not self._confirm_write_operation("실패 테이블 재시도"):
                return
            config, schema_file, _, checkpoint_db, selected_tables = self._command_context()
            self._run(
                "실패 테이블 재시도",
                lambda publisher: self._run_preflight_then(
                    lambda: self._service.run_retry_failed(
                        config=config,
                        schema_file=schema_file,
                        checkpoint_db=checkpoint_db,
                        event_publisher=publisher,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_validate(self) -> None:
            if not self._save_form_to_config() or not self._ensure_tables_selected():
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            self._run(
                "검증 실행",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._service.run_validate(
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        selected_tables=selected_tables,
                    )
                ),
            )

        def _run_incremental(self) -> None:
            if not self._save_form_to_config() or not self._warn_incremental_skips() or not self._confirm_write_operation("증분 이관"):
                return
            config, schema_file, output_dir, _, selected_tables = self._command_context()
            auto_validate = self.auto_validate.isChecked()
            self._run(
                "증분 이관",
                lambda _publisher: self._run_preflight_then(
                    lambda: self._run_incremental_sequence(
                        config=config,
                        schema_file=schema_file,
                        output_dir=output_dir,
                        selected_tables=selected_tables,
                        auto_validate=auto_validate,
                    )
                ),
            )

        def _run_preflight_then(self, command: Callable[[], CommandResult]) -> CommandResult:
            doctor_result = self._service.run_doctor(Path.cwd())
            if not doctor_result.success:
                return doctor_result
            return command()

        def _run_full_sequence(
            self,
            publisher: EventPublisher,
            *,
            config: Path,
            schema_file: Path | None,
            output_dir: Path,
            checkpoint_db: Path,
            dry_run_report: Path | None,
            selected_tables: set[str] | None,
            auto_validate: bool,
        ) -> CommandResult:
            ddl_result = self._service.run_apply_ddl(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "ddl-execution.json",
                dry_run_report_path=dry_run_report,
                selected_tables=selected_tables,
            )
            if not ddl_result.success:
                return ddl_result
            pre_index_result = self._service.run_apply_indexes(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "index-execution-pre-data.json",
                phase=IndexApplyTiming.PRE_DATA,
                selected_tables=selected_tables,
            )
            if not pre_index_result.success:
                return pre_index_result
            data_result = self._service.run_migrate_data(
                config=config,
                schema_file=schema_file,
                checkpoint_db=checkpoint_db,
                event_publisher=publisher,
                selected_tables=selected_tables,
            )
            if not data_result.success:
                return data_result
            foreign_key_result = self._service.run_apply_foreign_keys(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "foreign-key-execution.json",
                selected_tables=selected_tables,
            )
            if not foreign_key_result.success:
                return foreign_key_result
            post_index_result = self._service.run_apply_indexes(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "index-execution-post-data.json",
                phase=IndexApplyTiming.POST_DATA,
                selected_tables=selected_tables,
            )
            if not post_index_result.success or not auto_validate:
                return post_index_result
            return self._service.run_validate(
                config=config,
                schema_file=schema_file,
                output_dir=output_dir,
                selected_tables=selected_tables,
            )

        def _warn_incremental_skips(self) -> bool:
            selected = self._selected_tables() or set()
            try:
                config = load_config(self._config())
            except ConfigLoadError as exc:
                QMessageBox.warning(self, "설정 오류", str(exc))
                return False
            missing = []
            for identifier in sorted(selected):
                source_table = identifier.rsplit(".", 1)[-1]
                table_config = config.tables.get(identifier)
                has_table_watermark = bool(table_config and table_config.incremental.watermark_column)
                has_legacy_watermark = source_table in config.incremental.watermarks
                if not has_table_watermark and not has_legacy_watermark:
                    missing.append(identifier)
            if not missing:
                return True
            message = "다음 테이블은 watermark 설정이 없어 증분 이관에서 건너뜁니다.\n\n" + "\n".join(missing)
            QMessageBox.warning(self, "증분 설정 누락", message)
            return True

        def _run_data_sequence(
            self,
            publisher: EventPublisher,
            *,
            config: Path,
            schema_file: Path | None,
            output_dir: Path,
            checkpoint_db: Path,
            selected_tables: set[str] | None,
            auto_validate: bool,
        ) -> CommandResult:
            data_result = self._service.run_migrate_data(
                config=config,
                schema_file=schema_file,
                checkpoint_db=checkpoint_db,
                event_publisher=publisher,
                selected_tables=selected_tables,
            )
            if not data_result.success:
                return data_result
            post_index_result = self._service.run_apply_indexes(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "index-execution-post-data.json",
                phase=IndexApplyTiming.POST_DATA,
                selected_tables=selected_tables,
            )
            if not post_index_result.success or not auto_validate:
                return post_index_result
            return self._service.run_validate(
                config=config,
                schema_file=schema_file,
                output_dir=output_dir,
                selected_tables=selected_tables,
            )

        def _run_incremental_sequence(
            self,
            *,
            config: Path,
            schema_file: Path | None,
            output_dir: Path,
            selected_tables: set[str] | None,
            auto_validate: bool,
        ) -> CommandResult:
            pre_index_result = self._service.run_apply_indexes(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "index-execution-pre-data.json",
                phase=IndexApplyTiming.PRE_DATA,
                selected_tables=selected_tables,
            )
            if not pre_index_result.success:
                return pre_index_result
            incremental_result = self._service.run_incremental(
                config=config,
                schema_file=schema_file,
                output_dir=output_dir,
                selected_tables=selected_tables,
            )
            if not incremental_result.success:
                return incremental_result
            post_index_result = self._service.run_apply_indexes(
                config=config,
                schema_file=schema_file,
                output_file=output_dir / "index-execution-post-data.json",
                phase=IndexApplyTiming.POST_DATA,
                selected_tables=selected_tables,
            )
            if not post_index_result.success or not auto_validate:
                return post_index_result
            return self._service.run_validate(
                config=config,
                schema_file=schema_file,
                output_dir=output_dir,
                selected_tables=selected_tables,
            )

        def _run(self, label: str, command: Callable[[EventPublisher], CommandResult]) -> None:
            if self._thread is not None:
                QMessageBox.warning(self, "작업 실행 중", "현재 작업이 끝난 뒤 다음 작업을 실행하세요.")
                return
            self._thread = QThread()
            self._worker = CommandWorker(label, command)
            self._worker.moveToThread(self._thread)
            self._running_label = label
            self._worker.event_published.connect(self._append_event)
            self._thread.started.connect(self._worker.run)
            self._worker.completed.connect(self._command_completed)
            self._worker.completed.connect(self._thread.quit)
            self._worker.completed.connect(self._worker.deleteLater)
            self._thread.finished.connect(self._thread.deleteLater)
            self._thread.finished.connect(self._clear_worker)
            self._set_running(True)
            self._cancel_requested = False
            self.status.setText(f"실행 중: {label}")
            self.log.appendPlainText(f"> {label}")
            self._thread.start()

        def _cancel_running_command(self) -> None:
            if self._worker is None:
                return
            self._cancel_requested = True
            self._worker.cancel()
            self.cancel_button.setEnabled(False)
            if self._is_immediate_cancel_command(self._running_label):
                self.cancel_button.setText("취소 중")
                self.status.setText(f"취소 중: {self._running_label or '작업'}")
                self.log.appendPlainText("연결/테스트 작업을 즉시 취소합니다.")
                self._terminate_running_thread()
                return
            if self._thread is not None:
                self._thread.requestInterruption()
            self.cancel_button.setText("취소 요청됨")
            self.status.setText(f"취소 요청됨: {self._running_label or '작업'}")
            self.log.appendPlainText("취소 요청됨. 현재 처리 중인 DB 호출이 끝나면 작업을 중단합니다.")

        def _is_immediate_cancel_command(self, label: str | None) -> bool:
            return bool(label and ("연결 테스트" in label or "테스트" in label))

        def _terminate_running_thread(self) -> None:
            if self._thread is None:
                return
            label = self._running_label or "작업"
            _safe_disconnect(self._thread.started)
            _safe_disconnect(self._thread.finished)
            self._thread.terminate()
            self._thread.wait(3000)
            if self._worker is not None:
                self._worker.deleteLater()
            if self._thread is not None:
                self._thread.deleteLater()
            self._thread = None
            self._worker = None
            self._running_label = None
            self._set_running(False)
            self.status.setText("취소됨")
            self.log.appendPlainText(f"{label}이 취소되었습니다.")

        @Slot(object)
        def _append_event(self, event: MigrationEvent) -> None:
            view = event_to_view(event)
            table = f" table={view.table}" if view.table else ""
            progress = f" progress={view.progress_label}" if view.progress_label else ""
            self.log.appendPlainText(f"{view.level.upper()} {view.event_type}{table} {view.message}{progress}")

        @Slot(object)
        def _command_completed(self, result: CommandResult) -> None:
            label = self._running_label or "작업"
            self._set_running(False)
            self.status.setText("준비됨" if result.success else "실패")
            self.log.appendPlainText(result.message)
            if result.success and result.command == "scan-tables":
                self._target_table_options = tuple(result.details.get("target_tables", ()))
                self._last_dry_run_tables = {}
                self._last_dry_run_report = None
                self._populate_tables(result.details.get("tables", ()))
            if result.success and result.command == "dry-run" and result.report_html is not None:
                self._last_dry_run_report = result.report_html
                self._load_dry_run_summary(result.report_html)
            if result.success and result.command == "generate-manual-ddl":
                self._open_manual_ddl_dialog(result)
            if result.success and result.report_html is not None:
                self._last_report_html = result.report_html
                if self.generate_report.isChecked():
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(result.report_html.resolve())))
            if not result.success and result.command in {"migrate-data", "resume", "retry-failed", "full-selected-migration"}:
                self.recovery_group.setVisible(True)
            if not result.success:
                QMessageBox.warning(self, f"{label} 실패", result.message)

        @Slot()
        def _clear_worker(self) -> None:
            self._thread = None
            self._worker = None
            self._running_label = None

        def _set_running(self, running: bool) -> None:
            for button in self._buttons:
                button.setEnabled(not running)
            self.cancel_button.setEnabled(running)
            self.cancel_button.setText("취소")
            if not running:
                self._set_table_actions_enabled(self.table_list.count() > 0)

        def _set_table_actions_enabled(self, enabled: bool) -> None:
            for button in self._table_actions:
                button.setEnabled(enabled)

        def _open_manual_ddl_dialog(self, result: CommandResult) -> None:
            dialog = QDialog(self)
            dialog.setWindowTitle("수동 DDL")
            dialog.resize(920, 640)
            layout = QVBoxLayout(dialog)
            path_text = str(result.output_file.resolve()) if result.output_file is not None else ""
            label = QLabel(f"생성 파일: {path_text}")
            label.setWordWrap(True)
            layout.addWidget(label)
            sql_view = QPlainTextEdit()
            sql_view.setPlainText(str(result.details.get("sql", "")))
            layout.addWidget(sql_view, stretch=1)
            actions = QHBoxLayout()
            copy_button = QPushButton("복사")
            copy_button.clicked.connect(lambda: QApplication.clipboard().setText(sql_view.toPlainText()))
            save_button = QPushButton("다른 이름으로 저장")
            save_button.clicked.connect(lambda: self._save_manual_ddl_as(sql_view.toPlainText()))
            close_button = QPushButton("닫기")
            close_button.clicked.connect(dialog.accept)
            actions.addWidget(copy_button)
            actions.addWidget(save_button)
            actions.addWidget(close_button)
            layout.addLayout(actions)
            dialog.exec()

        def _load_dry_run_summary(self, report_html: Path) -> dict | None:
            summary_path = report_html.with_name("summary.json")
            if not summary_path.exists():
                return None
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                self.log.appendPlainText(f"dry-run 요약을 읽지 못했습니다: {exc}")
                return None
            self._last_dry_run_tables = {
                f"{table.get('source_schema')}.{table.get('source_table')}": table
                for table in summary.get("tables", [])
                if table.get("source_schema") and table.get("source_table")
            }
            return summary

        def _save_manual_ddl_as(self, sql: str) -> None:
            output_path, _ = QFileDialog.getSaveFileName(self, "DDL 저장", str(self._output_dir() / "manual-ddl.sql"), "SQL Files (*.sql)")
            if not output_path:
                return
            try:
                Path(output_path).write_text(sql, encoding="utf-8")
            except OSError as exc:
                QMessageBox.warning(self, "DDL 저장 실패", str(exc))

        def _open_settings(self) -> None:
            dialog = SettingsDialog(self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.config_label.setText(f"설정 파일: {self.config_path.text()}")
                self._save_gui_path_state()
                self._load_config_into_form(show_errors=True)

        def _open_tunnel_settings(self, side: str) -> None:
            current = self._source_tunnel_config if side == "source" else self._target_tunnel_config
            label = "원본" if side == "source" else "대상"
            enabled = self.source_tunnel_enabled.isChecked() if side == "source" else self.target_tunnel_enabled.isChecked()
            current = current.model_copy(update={"enabled": enabled})
            db_host = self.source_host.text().strip() if side == "source" else self.target_host.text().strip()
            db_port = self.source_port.value() if side == "source" else self.target_port.value()
            dialog = SshTunnelDialog(self, label=label, config=current, db_host=db_host, db_port=db_port)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if side == "source":
                self._source_tunnel_config = dialog.tunnel_config(enabled=self.source_tunnel_enabled.isChecked())
                self._sync_db_endpoint_from_tunnel(self.source_host, self.source_port, self._source_tunnel_config)
            else:
                self._target_tunnel_config = dialog.tunnel_config(enabled=self.target_tunnel_enabled.isChecked())
                self._sync_db_endpoint_from_tunnel(self.target_host, self.target_port, self._target_tunnel_config)

        def _sync_db_endpoint_from_tunnel(self, host: QLineEdit, port: QSpinBox, tunnel: SshTunnelConfig) -> None:
            if not tunnel.enabled or tunnel.local_port == 0:
                return
            host.setText(tunnel.local_host)
            port.setValue(tunnel.local_port)

        def _load_config_into_form(self, *, show_errors: bool) -> None:
            try:
                config = load_config(self._config())
            except ConfigLoadError as exc:
                if show_errors:
                    QMessageBox.warning(self, "설정 파일 읽기 실패", str(exc))
                config = AppConfig()
            self.source_dbms.setCurrentText(config.source.dbms.value)
            self.source_host.setText(config.source.host)
            self.source_port.setValue(config.source.port)
            self.source_database.setText(config.source.database)
            self.source_schema.setText(config.source.schema_name)
            self.source_user.setText(config.source.user)
            self.source_password.setText(config.source.password or "")
            self._source_tunnel_config = config.source.tunnel.model_copy(deep=True)
            self.source_tunnel_enabled.setChecked(config.source.tunnel.enabled)
            self.target_dbms.setCurrentText(config.target.dbms.value)
            self.target_host.setText(config.target.host)
            self.target_port.setValue(config.target.port)
            self.target_database.setText(config.target.database)
            self.target_schema.setText(config.target.schema_name or "")
            self.target_user.setText(config.target.user)
            self.target_password.setText(config.target.password or "")
            self._target_tunnel_config = config.target.tunnel.model_copy(deep=True)
            self.target_tunnel_enabled.setChecked(config.target.tunnel.enabled)
            policy = config.migration.existing_table_policy
            self._set_existing_table_policy(policy if policy in GUI_EXISTING_TABLE_POLICIES else ExistingTablePolicy.SKIP)
            self._manual_apply_foreign_keys = config.migration.apply_foreign_keys
            self.apply_foreign_keys.setChecked(config.migration.apply_foreign_keys)
            self.migration_mode.setCurrentText(GUI_MIGRATION_MODE_LABELS.get(config.migration.mode, "기본 이관"))
            self._sync_foreign_key_option()
            self.batch_size.setValue(config.migration.batch_size)
            self.commit_interval.setValue(config.migration.commit_interval)
            self.parallel_table_count.setValue(config.migration.parallel_table_count)
            self.throttle_sleep_ms.setValue(config.migration.throttle_sleep_ms)
            self.incremental_enabled.setChecked(config.incremental.enabled)

        def _save_form_to_config(self) -> bool:
            try:
                config = load_config(self._config()) if self._config().exists() else AppConfig()
            except ConfigLoadError as exc:
                QMessageBox.warning(self, "설정 파일 읽기 실패", str(exc))
                return False
            config.source.dbms = Dbms(self.source_dbms.currentText())
            config.source.host = self.source_host.text().strip()
            config.source.port = self.source_port.value()
            config.source.database = self.source_database.text().strip()
            config.source.schema_name = self.source_schema.text().strip()
            config.source.user = self.source_user.text().strip()
            config.source.password = self.source_password.text() or None
            config.source.tunnel = self._source_tunnel_config.model_copy(update={"enabled": self.source_tunnel_enabled.isChecked()})
            config.target.dbms = Dbms(self.target_dbms.currentText())
            config.target.host = self.target_host.text().strip()
            config.target.port = self.target_port.value()
            config.target.database = self.target_database.text().strip()
            config.target.schema_name = self.target_schema.text().strip() or None
            config.target.user = self.target_user.text().strip()
            config.target.password = self.target_password.text() or None
            config.target.tunnel = self._target_tunnel_config.model_copy(update={"enabled": self.target_tunnel_enabled.isChecked()})
            config.migration.mode = self._selected_migration_mode()
            config.migration.existing_table_policy = self._selected_existing_table_policy()
            config.migration.apply_foreign_keys = self._effective_apply_foreign_keys()
            config.migration.batch_size = self.batch_size.value()
            config.migration.commit_interval = self.commit_interval.value()
            config.migration.parallel_table_count = self.parallel_table_count.value()
            config.migration.throttle_sleep_ms = self.throttle_sleep_ms.value()
            config.report.output_dir = str(self._output_dir())
            config.incremental.enabled = self.incremental_enabled.isChecked()
            self._save_table_run_configs(config)
            try:
                self._config().parent.mkdir(parents=True, exist_ok=True)
                self._config().write_text(
                    yaml.safe_dump(config.model_dump(by_alias=True, mode="json"), sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
            except OSError as exc:
                QMessageBox.warning(self, "설정 파일 저장 실패", str(exc))
                return False
            self._save_gui_path_state()
            return True

        def _save_gui_path_state(self) -> None:
            if self._state_store is None:
                return
            try:
                self._state_store.save_paths(
                    GuiPathState(
                        config_path=self.config_path.text().strip() or _default_gui_path_state().config_path,
                        schema_path=self.schema_path.text().strip(),
                        output_dir=self.output_dir.text().strip() or _default_gui_path_state().output_dir,
                        checkpoint_path=self.checkpoint_path.text().strip() or _default_gui_path_state().checkpoint_path,
                    )
                )
            except OSError as exc:
                self.log.appendPlainText(f"GUI 경로 기본값 저장 실패: {exc}")
            except sqlite3.Error as exc:
                self.log.appendPlainText(f"GUI 경로 기본값 저장 실패: {exc}")

        def _load_gui_path_state(self) -> GuiPathState:
            defaults = _default_gui_path_state()
            if self._state_store is None:
                return defaults
            try:
                return self._state_store.load_paths(defaults)
            except (OSError, sqlite3.Error):
                return defaults

        def _create_state_store(self) -> GuiStateStore | None:
            try:
                return GuiStateStore()
            except (OSError, sqlite3.Error):
                return None

        def _current_form_config(self) -> AppConfig:
            config = AppConfig()
            config.source.schema_name = self.source_schema.text().strip() or config.source.schema_name
            config.target.dbms = Dbms(self.target_dbms.currentText())
            config.target.database = self.target_database.text().strip() or config.target.database
            config.target.schema_name = self.target_schema.text().strip() or None
            return config

        def _remember_manual_foreign_key_option(self, checked: bool) -> None:
            if self._syncing_foreign_key_option or not self.apply_foreign_keys.isEnabled():
                return
            self._manual_apply_foreign_keys = checked

        def _sync_foreign_key_option(self, _mode: str | None = None) -> None:
            mode = self.migration_mode.currentText()
            self._syncing_foreign_key_option = True
            try:
                if mode == "기본 이관":
                    self.apply_foreign_keys.setChecked(True)
                    self.apply_foreign_keys.setEnabled(False)
                    self.apply_foreign_keys.setToolTip("기본 이관은 데이터 이관 성공 후 외래키를 자동 적용합니다.")
                    return
                if mode == "테이블 이관":
                    self.apply_foreign_keys.setEnabled(True)
                    self.apply_foreign_keys.setChecked(self._manual_apply_foreign_keys)
                    self.apply_foreign_keys.setToolTip("필요한 경우 데이터 이관 이후 외래키 DDL을 함께 적용합니다.")
                    return
                self.apply_foreign_keys.setChecked(False)
                self.apply_foreign_keys.setEnabled(False)
                self.apply_foreign_keys.setToolTip("현재 실행 방식에서는 외래키 적용을 사용하지 않습니다.")
            finally:
                self._syncing_foreign_key_option = False

        def _effective_apply_foreign_keys(self) -> bool:
            mode = self.migration_mode.currentText()
            if mode == "기본 이관":
                return True
            if mode == "테이블 이관":
                return self.apply_foreign_keys.isChecked()
            return False

        def _populate_tables(self, tables: tuple[TableSelection, ...]) -> None:
            self.table_list.clear()
            self.table_list.clearSelection()
            self.table_list.setCurrentRow(-1)
            config = self._current_form_config()
            for table in tables:
                source_schema = table.identifier.rsplit(".", 1)[0]
                default_target_schema = _default_target_schema_name(config, source_schema)
                item = QListWidgetItem()
                item.setData(TABLE_ID_ROLE, table.identifier)
                item.setData(TARGET_TABLE_ROLE, table.table)
                item.setData(TARGET_SCHEMA_ROLE, default_target_schema)
                item.setData(WATERMARK_COLUMN_ROLE, "")
                item.setData(WATERMARK_START_ROLE, "")
                item.setData(WATERMARK_END_ROLE, "")
                item.setData(COLUMN_COUNT_ROLE, table.column_count)
                item.setData(ESTIMATED_ROWS_ROLE, table.estimated_rows)
                item.setData(HAS_PRIMARY_KEY_ROLE, table.has_primary_key)
                item.setData(TABLE_SELECTED_ROLE, True)
                item.setData(SOURCE_ONLY_COLUMNS_ROLE, "")
                item.setData(TABLE_COLUMNS_ROLE, table.columns)
                item.setData(TABLE_COMMENT_ROLE, "")
                item.setData(COLUMN_MAPPINGS_ROLE, {})
                item.setData(TYPE_OVERRIDES_ROLE, {})
                self.table_list.addItem(item)
                row_widget = self._table_row_widget(table, item)
                item.setSizeHint(row_widget.sizeHint())
                self.table_list.setItemWidget(item, row_widget)
            self.table_summary.setText(f"{len(tables)}개 테이블을 불러왔습니다. 이관하지 않을 테이블은 체크를 해제하세요.")
            self._set_table_actions_enabled(bool(tables))

        def _set_all_tables(self, checked: bool) -> None:
            for index in range(self.table_list.count()):
                item = self.table_list.item(index)
                item.setData(TABLE_SELECTED_ROLE, checked)
                checkbox = self._row_checkbox(item)
                if checkbox is not None:
                    checkbox.setChecked(checked)

        def _selected_tables(self) -> set[str] | None:
            if self.table_list.count() == 0:
                return None
            selected = {
                str(self.table_list.item(index).data(TABLE_ID_ROLE))
                for index in range(self.table_list.count())
                if bool(self.table_list.item(index).data(TABLE_SELECTED_ROLE))
            }
            return selected

        def _save_table_run_configs(self, config: AppConfig) -> None:
            for index in range(self.table_list.count()):
                item = self.table_list.item(index)
                identifier = str(item.data(TABLE_ID_ROLE))
                source_schema, source_table = identifier.rsplit(".", 1)
                default_target_schema = _default_target_schema_name(config, identifier.rsplit(".", 1)[0])
                target_schema = str(item.data(TARGET_SCHEMA_ROLE) or "").strip()
                target_table = str(item.data(TARGET_TABLE_ROLE) or "").strip()
                watermark_column = str(item.data(WATERMARK_COLUMN_ROLE) or "").strip()
                start_value = str(item.data(WATERMARK_START_ROLE) or "").strip()
                end_value = str(item.data(WATERMARK_END_ROLE) or "").strip()
                source_only_columns = _parse_source_only_columns(str(item.data(SOURCE_ONLY_COLUMNS_ROLE) or ""))
                column_mappings = dict(item.data(COLUMN_MAPPINGS_ROLE) or {})
                type_overrides = dict(item.data(TYPE_OVERRIDES_ROLE) or {})
                comment = str(item.data(TABLE_COMMENT_ROLE) or "").strip()
                if not target_schema or target_schema == default_target_schema:
                    target_schema = None
                if not target_table or target_table == source_table:
                    target_table = None
                if not any((target_schema, target_table, comment, watermark_column, start_value, end_value, source_only_columns, column_mappings, type_overrides)):
                    config.tables.pop(identifier, None)
                    continue
                table_config = config.tables.get(identifier, TableRunConfig())
                table_config.target_schema = target_schema
                table_config.target_table = target_table
                table_config.comment = comment or None
                table_config.incremental.watermark_column = watermark_column or None
                table_config.incremental.start_value = start_value or None
                table_config.incremental.end_value = end_value or None
                table_config.source_only_columns = source_only_columns
                _apply_column_mappings_to_config(table_config, tuple(item.data(TABLE_COLUMNS_ROLE) or ()), column_mappings, type_overrides)
                config.tables[identifier] = table_config

        def _table_item_label(self, table: TableSelection, item: QListWidgetItem) -> str:
            source_schema = table.identifier.rsplit(".", 1)[0]
            target_schema = str(item.data(TARGET_SCHEMA_ROLE) or _default_target_schema_name(self._current_form_config(), source_schema))
            target_table = str(item.data(TARGET_TABLE_ROLE) or table.table)
            label = f"{table.identifier}"
            if target_schema != source_schema or target_table != table.table:
                label += f" -> {target_schema}.{target_table}"
            label += f"  컬럼={table.column_count}"
            if table.estimated_rows is not None:
                label += f" 예상 행={table.estimated_rows}"
            if str(item.data(WATERMARK_COLUMN_ROLE) or "").strip():
                label += "  증분 설정됨"
            source_only_count = len(_parse_source_only_columns(str(item.data(SOURCE_ONLY_COLUMNS_ROLE) or "")))
            if source_only_count:
                label += f"  source-only={source_only_count}"
            if not table.has_primary_key:
                label += "  PK 없음"
            return label

        def _table_row_widget(self, table: TableSelection, item: QListWidgetItem) -> QWidget:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(4, 2, 4, 2)
            checkbox = QCheckBox()
            checkbox.setChecked(bool(item.data(TABLE_SELECTED_ROLE)))
            checkbox.toggled.connect(lambda checked, selected_item=item: selected_item.setData(TABLE_SELECTED_ROLE, checked))
            label = QLabel(self._table_item_label(table, item))
            label.setObjectName("table_label")
            label.setWordWrap(False)
            settings_button = QPushButton("설정")
            settings_button.setToolTip("이 테이블의 대상/컬럼/증분 설정을 변경합니다.")
            settings_button.clicked.connect(lambda _checked=False, selected_item=item: self._edit_table_settings(selected_item))
            layout.addWidget(checkbox)
            layout.addWidget(label, stretch=1)
            layout.addWidget(settings_button)
            return row

        def _row_checkbox(self, item: QListWidgetItem) -> QCheckBox | None:
            widget = self.table_list.itemWidget(item)
            return widget.findChild(QCheckBox) if widget is not None else None

        def _edit_table_settings(self, item: QListWidgetItem | None = None) -> None:
            self._edit_target_table_settings(item)

        def _edit_target_table_settings(self, item: QListWidgetItem | None = None) -> None:
            item = item or self.table_list.currentItem()
            if item is None:
                QMessageBox.warning(self, "테이블 선택 필요", "대상 설정을 바꿀 테이블을 선택하세요.")
                return
            identifier = str(item.data(TABLE_ID_ROLE))
            source_schema, source_table = identifier.rsplit(".", 1)
            current_schema = str(item.data(TARGET_SCHEMA_ROLE) or _default_target_schema_name(self._current_form_config(), source_schema))
            current = str(item.data(TARGET_TABLE_ROLE) or source_table)
            dry_run_table = self._last_dry_run_tables.get(identifier, {})
            dialog = QDialog(self)
            dialog.setWindowTitle("대상 테이블 설정")
            dialog.resize(980, 720)
            layout = QVBoxLayout(dialog)
            form = QFormLayout()
            target_schema = QLineEdit(current_schema)
            target_table = _target_table_combo(self._target_table_options, current_schema, current)
            table_status = QLabel(_target_table_status_label(self._target_table_options, current_schema, current, dry_run_table))
            comment = QLineEdit(str(item.data(TABLE_COMMENT_ROLE) or ""))
            source_only_actions = _parse_source_only_columns(str(item.data(SOURCE_ONLY_COLUMNS_ROLE) or ""))
            column_mappings = dict(item.data(COLUMN_MAPPINGS_ROLE) or {})
            type_overrides = dict(item.data(TYPE_OVERRIDES_ROLE) or {})
            columns = tuple(item.data(TABLE_COLUMNS_ROLE) or ())
            target_dbms = self._current_form_config().target.dbms
            data_preview = self._load_table_data_preview(identifier, current_schema, current, column_mappings, type_overrides, source_only_actions)
            form.addRow("대상 스키마", target_schema)
            form.addRow("대상 테이블", _inline_row(target_table, table_status))
            form.addRow("코멘트", comment)
            layout.addLayout(form)

            tabs = QTabWidget()
            mapping_state_holder: dict[str, dict[str, QComboBox]] = {}
            preview_dirty = {"value": False}
            preview_refreshing = {"value": False}

            def mark_preview_dirty() -> None:
                preview_dirty["value"] = True

            target_columns = _target_columns_for(self._target_table_options, current_schema, target_table.currentText())
            columns_widget, mapping_state = _columns_mapping_widget(columns, target_columns, column_mappings, type_overrides, source_only_actions, target_dbms=target_dbms, on_change=mark_preview_dirty)
            mapping_state_holder.update(mapping_state)
            initial_mappings = _column_mapping_state_to_dict(mapping_state_holder.get("target", {}))
            initial_type_overrides = _type_override_state_to_dict(mapping_state_holder.get("type", {}), columns, target_columns, initial_mappings, target_dbms)
            initial_actions = _source_only_state_to_dict(mapping_state_holder.get("action", {}), initial_mappings)
            initial_mappings = _without_ignored_column_mappings(initial_mappings, initial_actions)
            initial_type_overrides = _without_ignored_type_overrides(initial_type_overrides, initial_actions)
            columns_tab_index = tabs.addTab(columns_widget, f"Columns ({len(columns)})")
            data_tab_index = tabs.addTab(_data_preview_widget(data_preview, columns), "Data Preview")
            ddl_tab_index = tabs.addTab(
                _ddl_preview_widget(
                    dry_run_table,
                    current_schema,
                    target_table.currentText(),
                    target_dbms,
                    columns,
                    target_columns,
                    initial_mappings,
                    initial_type_overrides,
                    initial_actions,
                    existing=_is_existing_target_table(self._target_table_options, current_schema, target_table.currentText(), dry_run_table),
                ),
                "DDL Preview",
            )
            incremental_widget, incremental_fields = _incremental_settings_widget(
                columns,
                str(item.data(WATERMARK_COLUMN_ROLE) or ""),
                str(item.data(WATERMARK_START_ROLE) or ""),
                str(item.data(WATERMARK_END_ROLE) or ""),
            )
            tabs.addTab(incremental_widget, "Incremental")
            layout.addWidget(tabs, stretch=1)

            def refresh_preview_tabs() -> None:
                if preview_refreshing["value"]:
                    return
                preview_refreshing["value"] = True
                selected_schema = target_schema.text().strip() or current_schema
                selected_table = target_table.currentText().strip()
                current_mappings = _column_mapping_state_to_dict(mapping_state_holder.get("target", {}))
                current_type_overrides = _type_override_state_to_dict(mapping_state_holder.get("type", {}), columns, target_columns, current_mappings, target_dbms)
                current_actions = _source_only_state_to_dict(mapping_state_holder.get("action", {}), current_mappings)
                current_mappings = _without_ignored_column_mappings(current_mappings, current_actions)
                current_type_overrides = _without_ignored_type_overrides(current_type_overrides, current_actions)
                selected_target_columns = _target_columns_for(self._target_table_options, selected_schema, selected_table)

                refreshed_preview = self._load_table_data_preview(identifier, selected_schema, selected_table, current_mappings, current_type_overrides, current_actions)
                current_index = tabs.currentIndex()
                try:
                    tabs.blockSignals(True)
                    old_data_widget = tabs.widget(data_tab_index)
                    tabs.removeTab(data_tab_index)
                    tabs.insertTab(data_tab_index, _data_preview_widget(refreshed_preview, columns), "Data Preview")
                    if old_data_widget is not None:
                        old_data_widget.deleteLater()

                    old_ddl_widget = tabs.widget(ddl_tab_index)
                    tabs.removeTab(ddl_tab_index)
                    tabs.insertTab(
                        ddl_tab_index,
                        _ddl_preview_widget(
                            dry_run_table,
                            selected_schema,
                            selected_table,
                            target_dbms,
                            columns,
                            selected_target_columns,
                            current_mappings,
                            current_type_overrides,
                            current_actions,
                            existing=_is_existing_target_table(self._target_table_options, selected_schema, selected_table, dry_run_table),
                        ),
                        "DDL Preview",
                    )
                    if old_ddl_widget is not None:
                        old_ddl_widget.deleteLater()
                    tabs.setCurrentIndex(current_index)
                    preview_dirty["value"] = False
                finally:
                    tabs.blockSignals(False)
                    preview_refreshing["value"] = False

            def refresh_target_table_preview(_value: str | None = None) -> None:
                selected_schema = target_schema.text().strip() or current_schema
                selected_table = target_table.currentText().strip()
                table_status.setText(_target_table_status_label(self._target_table_options, selected_schema, selected_table, dry_run_table))
                current_mappings = _column_mapping_state_to_dict(mapping_state_holder.get("target", {}))
                selected_target_columns = _target_columns_for(self._target_table_options, selected_schema, selected_table)
                current_type_overrides = _type_override_state_to_dict(mapping_state_holder.get("type", {}), columns, selected_target_columns, current_mappings, target_dbms)
                current_actions = _source_only_state_to_dict(mapping_state_holder.get("action", {}), current_mappings)
                current_mappings = _without_ignored_column_mappings(current_mappings, current_actions)
                current_type_overrides = _without_ignored_type_overrides(current_type_overrides, current_actions)
                refreshed_widget, refreshed_state = _columns_mapping_widget(
                    columns,
                    selected_target_columns,
                    current_mappings,
                    current_type_overrides,
                    current_actions,
                    target_dbms=target_dbms,
                    on_change=mark_preview_dirty,
                )
                mapping_state_holder.clear()
                mapping_state_holder.update(refreshed_state)
                current_index = tabs.currentIndex()
                try:
                    tabs.blockSignals(True)
                    old_widget = tabs.widget(columns_tab_index)
                    tabs.removeTab(columns_tab_index)
                    tabs.insertTab(columns_tab_index, refreshed_widget, f"Columns ({len(columns)})")
                    if old_widget is not None:
                        old_widget.deleteLater()
                    tabs.setCurrentIndex(current_index)
                finally:
                    tabs.blockSignals(False)
                refresh_preview_tabs()

            def refresh_preview_on_tab_change(index: int) -> None:
                if preview_dirty["value"] and index in {data_tab_index, ddl_tab_index}:
                    refresh_preview_tabs()

            target_table.currentTextChanged.connect(refresh_target_table_preview)
            target_schema.textChanged.connect(refresh_target_table_preview)
            tabs.currentChanged.connect(refresh_preview_on_tab_change)

            def accept_if_valid() -> None:
                invalid_types = _invalid_type_entries(mapping_state_holder.get("type", {}), target_dbms)
                if invalid_types:
                    QMessageBox.warning(
                        dialog,
                        "Target type 확인 필요",
                        "Target DBMS에서 인식하지 못하는 타입이 있습니다.\n\n" + "\n".join(invalid_types[:20]),
                    )
                    return
                dialog.accept()

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(accept_if_valid)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            item.setData(TARGET_SCHEMA_ROLE, target_schema.text().strip() or _default_target_schema_name(self._current_form_config(), source_schema))
            item.setData(TARGET_TABLE_ROLE, target_table.currentText().strip() or source_table)
            item.setData(TABLE_COMMENT_ROLE, comment.text().strip())
            saved_mappings = _column_mapping_state_to_dict(mapping_state_holder.get("target", {}))
            saved_actions = _source_only_state_to_dict(mapping_state_holder.get("action", {}), saved_mappings)
            saved_mappings = _without_ignored_column_mappings(saved_mappings, saved_actions)
            saved_type_overrides = _type_override_state_to_dict(
                mapping_state_holder.get("type", {}),
                columns,
                _target_columns_for(self._target_table_options, target_schema.text().strip() or current_schema, target_table.currentText().strip()),
                saved_mappings,
                target_dbms,
            )
            saved_type_overrides = _without_ignored_type_overrides(saved_type_overrides, saved_actions)
            item.setData(COLUMN_MAPPINGS_ROLE, saved_mappings)
            item.setData(TYPE_OVERRIDES_ROLE, saved_type_overrides)
            item.setData(SOURCE_ONLY_COLUMNS_ROLE, _source_only_columns_to_text(saved_actions))
            item.setData(WATERMARK_COLUMN_ROLE, incremental_fields["watermark"].currentText().strip())
            item.setData(WATERMARK_START_ROLE, incremental_fields["start"].text().strip())
            item.setData(WATERMARK_END_ROLE, incremental_fields["end"].text().strip())
            self._refresh_table_item_label(item)

        def _load_table_data_preview(
            self,
            identifier: str,
            target_schema: str,
            target_table: str,
            column_mappings: dict[str, str],
            type_overrides: dict[str, str],
            source_only_actions: dict[str, SourceOnlyColumnAction],
        ) -> dict:
            try:
                result = self._service.run_table_preview(
                    config=self._config(),
                    schema_file=self._schema_file(),
                    table_identifier=identifier,
                    target_schema=target_schema,
                    target_table=target_table,
                    column_mappings=column_mappings,
                    type_overrides=type_overrides,
                    source_only_actions=source_only_actions,
                    sample_size=30,
                )
            except Exception as exc:
                return {"columns": (), "rows": (), "message": f"Data Preview 조회 실패: {exc}"}
            if not result.success:
                return {"columns": (), "rows": (), "message": result.message}
            return result.details

        def _edit_incremental_table_settings(self, item: QListWidgetItem | None = None) -> None:
            self._edit_table_settings(item)

        def _refresh_table_item_label(self, item: QListWidgetItem) -> None:
            identifier = str(item.data(TABLE_ID_ROLE))
            source_table = identifier.rsplit(".", 1)[-1]
            table = TableSelection(
                identifier=identifier,
                schema=identifier.rsplit(".", 1)[0],
                table=source_table,
                column_count=int(item.data(COLUMN_COUNT_ROLE) or 0),
                estimated_rows=item.data(ESTIMATED_ROWS_ROLE),
                has_primary_key=bool(item.data(HAS_PRIMARY_KEY_ROLE)),
            )
            label_text = self._table_item_label(table, item)
            widget = self.table_list.itemWidget(item)
            if widget is not None:
                label = widget.findChild(QLabel, "table_label")
                if label is not None:
                    label.setText(label_text)
                item.setSizeHint(widget.sizeHint())

        def _confirm_write_operation(self, operation: str) -> bool:
            if not self._ensure_tables_selected():
                return False
            try:
                app_config = load_config(self._config())
            except ConfigLoadError as exc:
                QMessageBox.warning(self, "설정 오류", str(exc))
                return False
            if app_config.migration.existing_table_policy is ExistingTablePolicy.OVERWRITE:
                if not self._confirm_overwrite_operation(operation, app_config):
                    return False
            gate = evaluate_dry_run_gate(app_config, self._last_dry_run_report)
            if not gate.allowed:
                QMessageBox.warning(self, "검토 실행 필요", gate.message)
                return False
            expected = app_config.target.database
            entered, ok = QInputDialog.getText(
                self,
                operation,
                f"{operation}은 대상 데이터베이스 '{expected}'에 쓸 수 있습니다. 계속하려면 대상 데이터베이스 이름을 입력하세요.",
            )
            return ok and entered == expected

        def _set_existing_table_policy(self, policy: ExistingTablePolicy) -> None:
            for index in range(self.existing_table_policy.count()):
                if self.existing_table_policy.itemData(index) == policy.value:
                    self.existing_table_policy.setCurrentIndex(index)
                    return

        def _selected_existing_table_policy(self) -> ExistingTablePolicy:
            value = self.existing_table_policy.currentData()
            return ExistingTablePolicy(str(value or ExistingTablePolicy.SKIP.value))

        def _selected_migration_mode(self) -> MigrationMode:
            return GUI_MIGRATION_MODES_BY_LABEL.get(self.migration_mode.currentText(), MigrationMode.DDL_AND_DML)

        def _confirm_overwrite_operation(self, operation: str, app_config: AppConfig) -> bool:
            selected = sorted(self._selected_tables() or [])
            table_lines = "\n".join(selected[:20])
            if len(selected) > 20:
                table_lines += f"\n... 외 {len(selected) - 20}개"
            warning = (
                f"{operation}은 overwrite 정책으로 실행됩니다.\n\n"
                "대상 DB의 기존 테이블이 삭제되고 다시 생성될 수 있습니다.\n"
                "이 작업은 target 데이터를 잃게 만들 수 있으며 자동 백업 테이블은 아직 생성하지 않습니다.\n"
                "실행 기록은 overwrite-audit.sqlite에 남습니다.\n\n"
                f"대상 DB: {app_config.target.host}/{app_config.target.database}\n"
                f"대상 테이블:\n{table_lines}\n\n"
                "계속하려면 OVERWRITE를 입력하세요."
            )
            entered, ok = QInputDialog.getText(self, "Overwrite 확인", warning)
            return ok and entered == "OVERWRITE"

        def _ensure_tables_selected(self) -> bool:
            if self.table_list.count() > 0 and not self._selected_tables():
                QMessageBox.warning(self, "선택된 테이블 없음", "작업을 실행하기 전에 최소 한 개 이상의 테이블을 선택하세요.")
                return False
            return True

        def _open_last_report(self) -> None:
            if self._last_report_html is None or not self._last_report_html.exists():
                QMessageBox.information(self, "리포트 없음", "먼저 검토, 검증, 또는 증분 이관을 실행하세요.")
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_report_html.resolve())))

        def _config(self) -> Path:
            return Path(self.config_path.text()).expanduser()

        def _schema_file(self) -> Path | None:
            value = self.schema_path.text().strip()
            return Path(value).expanduser() if value else None

        def _output_dir(self) -> Path:
            return Path(self.output_dir.text()).expanduser()

        def _checkpoint_db(self) -> Path:
            return Path(self.checkpoint_path.text()).expanduser()

        def _command_context(self) -> tuple[Path, Path | None, Path, Path, set[str] | None]:
            return (
                self._config(),
                self._schema_file(),
                self._output_dir(),
                self._checkpoint_db(),
                self._selected_tables(),
            )


    class SshTunnelDialog(QDialog):
        def __init__(self, parent: MainWindow, *, label: str, config: SshTunnelConfig, db_host: str, db_port: int) -> None:
            super().__init__(parent)
            self._label = label
            self._test_thread: QThread | None = None
            self._test_worker: CommandWorker | None = None
            self._test_cancel_requested = False
            self.setWindowTitle(f"{label} SSH 터널 설정")
            self.resize(620, 360)
            layout = QVBoxLayout(self)

            connection_group = QGroupBox("SSH 연결")
            connection_form = QFormLayout(connection_group)
            self.host = QLineEdit(config.ssh_host or "")
            self.port = _spin(1, 65535)
            self.port.setValue(config.ssh_port)
            self.username = QLineEdit(config.ssh_user or "")
            self.auth_type = _combo([auth.value for auth in SshAuthenticationType])
            self.auth_type.setCurrentText(config.auth_type.value)
            self.password = QLineEdit(config.ssh_password or "")
            self.password.setEchoMode(QLineEdit.EchoMode.Password)
            self.key_path = QLineEdit(config.private_key_path or "")
            connection_form.addRow("Host", self.host)
            connection_form.addRow("SSH Port", self.port)
            connection_form.addRow("Username", self.username)
            connection_form.addRow("Authentication type", self.auth_type)
            connection_form.addRow("Password", self.password)
            connection_form.addRow("Key path", _path_row(self.key_path, self._choose_key, "찾기"))
            layout.addWidget(connection_group)

            remote_group = QGroupBox("터널 대상 DB")
            remote_form = QFormLayout(remote_group)
            self.remote_host = QLineEdit(config.remote_host or db_host)
            self.remote_port = _spin(1, 65535)
            self.remote_port.setValue(config.remote_port or db_port)
            remote_form.addRow("실제 DB Host", self.remote_host)
            remote_form.addRow("실제 DB Port", self.remote_port)
            layout.addWidget(remote_group)

            advanced_group = QGroupBox("옵션")
            advanced_form = QFormLayout(advanced_group)
            self.local_host = QLineEdit(config.local_host)
            self.local_port = _spin(0, 65535)
            self.local_port.setValue(config.local_port)
            self.local_port.setToolTip("0이면 실행 시 사용 가능한 로컬 포트를 자동 선택합니다.")
            self.known_hosts = QLineEdit(config.known_hosts_path or str(Path.home() / ".ssh" / "known_hosts"))
            advanced_form.addRow("터널 로컬 호스트", self.local_host)
            advanced_form.addRow("터널 로컬 포트", self.local_port)
            advanced_form.addRow("known_hosts", _path_row(self.known_hosts, self._choose_known_hosts, "찾기"))
            layout.addWidget(advanced_group)

            actions = QHBoxLayout()
            self.test_button = QPushButton("SSH 연결 테스트")
            self.test_button.clicked.connect(self._test_connection)
            self.cancel_test_button = QPushButton("취소")
            self.cancel_test_button.clicked.connect(self._cancel_test_connection)
            self.cancel_test_button.setEnabled(False)
            actions.addWidget(self.test_button)
            actions.addWidget(self.cancel_test_button)
            actions.addStretch(1)
            self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            self.buttons.accepted.connect(self.accept)
            self.buttons.rejected.connect(self.reject)
            actions.addWidget(self.buttons)
            layout.addLayout(actions)
            self.auth_type.currentTextChanged.connect(self._sync_auth_fields)
            self._sync_auth_fields()

        def tunnel_config(self, *, enabled: bool | None = None) -> SshTunnelConfig:
            known_hosts = self.known_hosts.text().strip()
            default_known_hosts = str(Path.home() / ".ssh" / "known_hosts")
            auth_type = SshAuthenticationType(self.auth_type.currentText())
            return SshTunnelConfig(
                enabled=True if enabled is None else enabled,
                ssh_host=self.host.text().strip() or None,
                ssh_port=self.port.value(),
                ssh_user=self.username.text().strip() or None,
                auth_type=auth_type,
                private_key_path=(self.key_path.text().strip() or None) if auth_type is SshAuthenticationType.KEY else None,
                ssh_password=(self.password.text() or None) if auth_type is SshAuthenticationType.PASSWORD else None,
                known_hosts_path=None if known_hosts == default_known_hosts else known_hosts or None,
                remote_host=self.remote_host.text().strip() or None,
                remote_port=self.remote_port.value(),
                local_host=self.local_host.text().strip() or "127.0.0.1",
                local_port=self.local_port.value(),
            )

        def _sync_auth_fields(self, _value: str | None = None) -> None:
            is_password = self.auth_type.currentText() == SshAuthenticationType.PASSWORD.value
            self.password.setEnabled(is_password)
            self.key_path.setEnabled(not is_password)

        def _test_connection(self) -> None:
            if self._test_thread is not None:
                return
            config = self.tunnel_config(enabled=True)
            self._test_thread = QThread()
            self._test_worker = CommandWorker("ssh-test", lambda _publisher: self._run_ssh_test(config))
            self._test_worker.moveToThread(self._test_thread)
            self._test_thread.started.connect(self._test_worker.run)
            self._test_worker.completed.connect(self._ssh_test_completed)
            self._test_worker.completed.connect(self._test_thread.quit)
            self._test_worker.completed.connect(self._test_worker.deleteLater)
            self._test_thread.finished.connect(self._test_thread.deleteLater)
            self._test_thread.finished.connect(self._clear_ssh_test_worker)
            self._set_testing(True)
            self._test_cancel_requested = False
            self._test_thread.start()

        def _run_ssh_test(self, config: SshTunnelConfig) -> CommandResult:
            try:
                SshConnectionTester().test(label=self._label, config=config)
            except TunnelError as exc:
                return CommandResult(command="ssh-test", success=False, message=str(exc))
            return CommandResult(command="ssh-test", success=True, message="SSH 연결에 성공했습니다.")

        def _cancel_test_connection(self) -> None:
            if self._test_worker is None:
                return
            self._test_cancel_requested = True
            self._test_worker.cancel()
            self.cancel_test_button.setEnabled(False)
            self.cancel_test_button.setText("취소 중")
            self._terminate_test_thread()

        @Slot(object)
        def _ssh_test_completed(self, result: CommandResult) -> None:
            if self._test_cancel_requested:
                QMessageBox.information(self, "SSH 연결 테스트 취소", "SSH 연결 테스트가 취소되었습니다.")
            elif result.success:
                QMessageBox.information(self, "SSH 연결 테스트 성공", result.message)
            else:
                QMessageBox.warning(self, "SSH 연결 테스트 실패", result.message)

        @Slot()
        def _clear_ssh_test_worker(self) -> None:
            self._test_thread = None
            self._test_worker = None
            self._set_testing(False)

        def _terminate_test_thread(self) -> None:
            if self._test_thread is None:
                return
            _safe_disconnect(self._test_thread.started)
            _safe_disconnect(self._test_thread.finished)
            self._test_thread.terminate()
            self._test_thread.wait(3000)
            if self._test_worker is not None:
                self._test_worker.deleteLater()
            if self._test_thread is not None:
                self._test_thread.deleteLater()
            self._test_thread = None
            self._test_worker = None
            self._set_testing(False)
            QMessageBox.information(self, "SSH 연결 테스트 취소", "SSH 연결 테스트가 취소되었습니다.")

        def _set_testing(self, testing: bool) -> None:
            self.test_button.setEnabled(not testing)
            self.cancel_test_button.setEnabled(testing)
            self.cancel_test_button.setText("취소")
            self.buttons.setEnabled(not testing)

        def _choose_key(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "SSH KEY 선택", str(Path.cwd()), "All files (*)")
            if path:
                self.key_path.setText(path)

        def _choose_known_hosts(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "known_hosts 선택", str(Path.home() / ".ssh"), "All files (*)")
            if path:
                self.known_hosts.setText(path)


    class SettingsDialog(QDialog):
        def __init__(self, window: MainWindow) -> None:
            super().__init__(window)
            self._window = window
            self.setWindowTitle("설정")
            self.resize(720, 420)
            layout = QVBoxLayout(self)

            paths = QGroupBox("경로 설정")
            path_form = QFormLayout(paths)
            self.config_path = QLineEdit(window.config_path.text())
            self.schema_path = QLineEdit(window.schema_path.text())
            self.output_dir = QLineEdit(window.output_dir.text())
            self.checkpoint_path = QLineEdit(window.checkpoint_path.text())
            path_form.addRow("설정 파일", _path_row(self.config_path, self._choose_config, "찾기"))
            path_form.addRow("스키마 스냅샷", _path_row(self.schema_path, self._choose_schema, "찾기"))
            path_form.addRow("리포트 출력 경로", _path_row(self.output_dir, self._choose_output_dir, "찾기"))
            path_form.addRow("Checkpoint DB", _path_row(self.checkpoint_path, self._choose_checkpoint, "찾기"))
            layout.addWidget(paths)

            advanced = QGroupBox("고급 이관 설정")
            advanced_form = QFormLayout(advanced)
            self.batch_size = _spin(1, 10_000_000)
            self.commit_interval = _spin(1, 10_000_000)
            self.parallel_table_count = _spin(1, 64)
            self.throttle_sleep_ms = _spin(0, 60_000)
            self.batch_size.setValue(window.batch_size.value())
            self.commit_interval.setValue(window.commit_interval.value())
            self.parallel_table_count.setValue(window.parallel_table_count.value())
            self.throttle_sleep_ms.setValue(window.throttle_sleep_ms.value())
            advanced_form.addRow("Batch size", self.batch_size)
            advanced_form.addRow("Commit interval", self.commit_interval)
            advanced_form.addRow("Parallel table count", self.parallel_table_count)
            advanced_form.addRow("Throttle sleep ms", self.throttle_sleep_ms)
            layout.addWidget(advanced)

            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(self.accept)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

        def accept(self) -> None:
            self._window.config_path.setText(self.config_path.text())
            self._window.schema_path.setText(self.schema_path.text())
            self._window.output_dir.setText(self.output_dir.text())
            self._window.checkpoint_path.setText(self.checkpoint_path.text())
            self._window.batch_size.setValue(self.batch_size.value())
            self._window.commit_interval.setValue(self.commit_interval.value())
            self._window.parallel_table_count.setValue(self.parallel_table_count.value())
            self._window.throttle_sleep_ms.setValue(self.throttle_sleep_ms.value())
            super().accept()

        def _choose_config(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "설정 파일 선택", str(Path.cwd()), "YAML files (*.yml *.yaml);;All files (*)")
            if path:
                self.config_path.setText(path)

        def _choose_schema(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "스키마 스냅샷 선택", str(Path.cwd()), "JSON files (*.json);;All files (*)")
            if path:
                self.schema_path.setText(path)

        def _choose_output_dir(self) -> None:
            path = QFileDialog.getExistingDirectory(self, "리포트 출력 경로 선택", str(Path.cwd()))
            if path:
                self.output_dir.setText(path)

        def _choose_checkpoint(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Checkpoint DB 선택", str(Path.cwd()), "SQLite files (*.sqlite *.db);;All files (*)")
            if path:
                self.checkpoint_path.setText(path)


    def _path_row(line_edit: QLineEdit, handler: Callable[[], None], label: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(line_edit, stretch=1)
        button = QPushButton(label)
        button.clicked.connect(handler)
        layout.addWidget(button)
        return row


    def _inline_row(*widgets: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        for widget in widgets:
            layout.addWidget(widget)
        layout.addStretch(1)
        return row


    def _safe_disconnect(signal: object) -> None:
        try:
            disconnect = getattr(signal, "disconnect")
            disconnect()
        except (RuntimeError, TypeError):
            return


    def _combo(values: list[str]) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        return combo


    def _policy_combo() -> QComboBox:
        combo = QComboBox()
        for policy in GUI_EXISTING_TABLE_POLICIES:
            combo.addItem(EXISTING_TABLE_POLICY_LABELS[policy], policy.value)
        return combo


    def _spin(minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin


    def _source_only_columns_to_text(actions: dict[str, SourceOnlyColumnAction]) -> str:
        return "\n".join(f"{column}:{action.value}" for column, action in sorted(actions.items()))


    def _column_mappings_from_config(table_config: TableRunConfig) -> dict[str, str]:
        mappings: dict[str, str] = {}
        for target_column, column_config in table_config.columns.items():
            if column_config.source:
                mappings[column_config.source] = target_column
        return mappings


    def _type_overrides_from_config(table_config: TableRunConfig) -> dict[str, str]:
        overrides: dict[str, str] = {}
        for target_column, column_config in table_config.columns.items():
            if column_config.target_type:
                overrides[column_config.source or target_column] = column_config.target_type
        return overrides


    def _apply_column_mappings_to_config(
        table_config: TableRunConfig,
        source_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
    ) -> None:
        source_column_names = {column.name for column in source_columns}
        table_config.columns = {
            target_column: column_config
            for target_column, column_config in table_config.columns.items()
            if not column_config.source or column_config.source not in source_column_names
        }
        target_by_source = {source_column: target_column for source_column, target_column in column_mappings.items() if source_column and target_column}
        for source_column in sorted(set(target_by_source) | set(type_overrides)):
            target_column = target_by_source.get(source_column, source_column)
            target_type = type_overrides.get(source_column)
            if not source_column or not target_column or (source_column == target_column and target_type is None):
                continue
            existing = table_config.columns.get(target_column)
            table_config.columns[target_column] = (
                existing.model_copy(update={"source": source_column, "target_type": target_type or existing.target_type})
                if existing is not None
                else ColumnTransformConfig(source=source_column, target_type=target_type)
            )


    def _target_table_combo(target_tables: tuple[TableSelection, ...], schema: str, current_table: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        table_names = sorted({table.table for table in target_tables if table.schema == schema})
        combo.addItems(table_names)
        if current_table and combo.findText(current_table) < 0:
            combo.addItem(current_table)
        combo.setCurrentText(current_table)
        return combo


    def _target_columns_for(target_tables: tuple[TableSelection, ...], schema: str, table_name: str) -> tuple[ColumnSelection, ...]:
        for table in target_tables:
            if table.schema == schema and table.table == table_name:
                return table.columns
        return ()


    def _target_table_status_label(
        target_tables: tuple[TableSelection, ...],
        schema: str,
        table_name: str,
        dry_run_table: dict,
    ) -> str:
        if any(table.schema == schema and table.table == table_name for table in target_tables):
            return "Existing"
        if dry_run_table.get("schema_origin") == "target_existing":
            return "Existing"
        return "New"


    def _is_existing_target_table(
        target_tables: tuple[TableSelection, ...],
        schema: str,
        table_name: str,
        dry_run_table: dict,
    ) -> bool:
        return _target_table_status_label(target_tables, schema, table_name, dry_run_table) == "Existing"


    def _column_mapping_state_to_dict(target_combos: dict[str, QComboBox]) -> dict[str, str]:
        return {
            source_column: combo.currentText().strip()
            for source_column, combo in target_combos.items()
            if combo.currentText().strip()
        }


    def _source_only_state_to_dict(
        action_combos: dict[str, QComboBox],
        column_mappings: dict[str, str],
    ) -> dict[str, SourceOnlyColumnAction]:
        actions: dict[str, SourceOnlyColumnAction] = {}
        for source_column, combo in action_combos.items():
            raw_action = str(combo.currentData() or "")
            if raw_action == SourceOnlyColumnAction.IGNORE.value:
                actions[source_column] = SourceOnlyColumnAction.IGNORE
            elif raw_action == SourceOnlyColumnAction.ADD_TO_TARGET.value and source_column not in column_mappings:
                actions[source_column] = SourceOnlyColumnAction.ADD_TO_TARGET
        return actions


    def _without_ignored_column_mappings(
        column_mappings: dict[str, str],
        source_only_actions: dict[str, SourceOnlyColumnAction],
    ) -> dict[str, str]:
        return {
            source_column: target_column
            for source_column, target_column in column_mappings.items()
            if source_only_actions.get(source_column) is not SourceOnlyColumnAction.IGNORE
        }


    def _without_ignored_type_overrides(
        type_overrides: dict[str, str],
        source_only_actions: dict[str, SourceOnlyColumnAction],
    ) -> dict[str, str]:
        return {
            source_column: target_type
            for source_column, target_type in type_overrides.items()
            if source_only_actions.get(source_column) is not SourceOnlyColumnAction.IGNORE
        }


    def _type_override_state_to_dict(
        type_edits: dict[str, QLineEdit],
        source_columns: tuple[ColumnSelection, ...],
        target_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        target_dbms: Dbms,
    ) -> dict[str, str]:
        source_by_name = {column.name: column for column in source_columns}
        target_by_name = {column.name: column for column in target_columns}
        overrides: dict[str, str] = {}
        for source_column, edit in type_edits.items():
            value = edit.text().strip()
            if not value:
                continue
            source = source_by_name.get(source_column)
            target_name = column_mappings.get(source_column, source_column)
            target = target_by_name.get(target_name) or target_by_name.get(source_column)
            default_type = target.source_type if target is not None else (_target_type_for_column(target_dbms, source) if source is not None else "")
            if _normalize_type_text(value) != _normalize_type_text(default_type):
                overrides[source_column] = value
        return overrides


    def _invalid_type_entries(type_edits: dict[str, QLineEdit], target_dbms: Dbms) -> list[str]:
        invalid: list[str] = []
        for source_column, edit in sorted(type_edits.items()):
            if edit.isEnabled() and not _is_valid_target_type(target_dbms, edit.text().strip()):
                invalid.append(f"{source_column}: {edit.text().strip()}")
        return invalid


    def _columns_mapping_widget(
        source_columns: tuple[ColumnSelection, ...],
        target_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
        source_only_actions: dict[str, SourceOnlyColumnAction],
        *,
        target_dbms: Dbms,
        on_change: Callable[[], None] | None = None,
    ) -> tuple[QWidget, dict[str, dict[str, QComboBox]]]:
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels(["Source column", "Source type", "Target column", "Target type", "Key", "Status", "Action"])
        target_by_name = {column.name: column for column in target_columns}
        source_by_name = {column.name: column for column in source_columns}
        mapped_target_names = {target for target in column_mappings.values() if target}
        row_models = [
            (column.name, column, target_by_name.get(column_mappings.get(column.name, column.name)))
            for column in source_columns
        ]
        row_models.extend((name, None, column) for name, column in target_by_name.items() if name not in source_by_name and name not in mapped_target_names)
        table.setRowCount(len(row_models))
        target_combos: dict[str, QComboBox] = {}
        type_edits: dict[str, QLineEdit] = {}
        action_combos: dict[str, QComboBox] = {}
        for row, (name, source_column, target_column) in enumerate(row_models):
            status = _column_mapping_status(source_column, target_column)
            color = _column_status_color(status)
            values = (
                source_column.name if source_column is not None else "",
                source_column.source_type if source_column is not None else "",
                target_column.name if target_column is not None else "",
                target_column.source_type if target_column is not None else "",
                _column_key_label(source_column, target_column),
                status,
            )
            row_items: list[QTableWidgetItem] = []
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if color is not None:
                    item.setBackground(QBrush(color))
                table.setItem(row, column_index, item)
                row_items.append(item)

            if source_column is not None:
                target_combo = QComboBox()
                target_combo.setEditable(True)
                target_combo.addItem("", "")
                for target_name in sorted(target_by_name):
                    target_combo.addItem(target_name, target_name)
                selected_target = column_mappings.get(source_column.name)
                if selected_target is None and source_column.name in target_by_name:
                    selected_target = source_column.name
                if selected_target is None and source_column.name not in target_by_name:
                    selected_target = source_column.name
                if selected_target and target_combo.findText(selected_target) < 0:
                    target_combo.addItem(selected_target, selected_target)
                target_combo.setCurrentText(selected_target or "")
                row_items[2].setText("")
                table.setCellWidget(row, 2, target_combo)
                target_combos[source_column.name] = target_combo
                type_edit = QLineEdit(type_overrides.get(source_column.name) or (target_column.source_type if target_column is not None else _target_type_for_column(target_dbms, source_column)))
                row_items[3].setText("")
                table.setCellWidget(row, 3, type_edit)
                type_edit.setEnabled(bool(selected_target))
                _refresh_target_type_edit_state(type_edit, source_column, target_column, target_dbms)
                type_edits[source_column.name] = type_edit

                action_combo = QComboBox()
                action_combo.addItem("", "")
                action_combo.addItem("무시 - 이관되지 않음", SourceOnlyColumnAction.IGNORE.value)
                action = source_only_actions.get(source_column.name)
                if action is SourceOnlyColumnAction.IGNORE:
                    action_combo.setCurrentIndex(max(0, action_combo.findData(action.value)))
                action_combo.setEnabled(not bool(target_by_name.get(selected_target or "")))
                table.setCellWidget(row, 6, action_combo)
                table.setRowHeight(row, max(table.rowHeight(row), target_combo.sizeHint().height() + 4, type_edit.sizeHint().height() + 4, action_combo.sizeHint().height() + 4))
                action_combos[source_column.name] = action_combo
                target_combo.currentTextChanged.connect(
                    lambda value,
                    source=source_column,
                    items=tuple(row_items),
                    combo=action_combo,
                    edit=type_edit,
                    dbms=target_dbms: _refresh_column_mapping_row(value, source, target_by_name, items, combo, edit, dbms)
                )
                _refresh_type_override_row(source_column, selected_target or "", target_by_name, tuple(row_items), type_edit, target_dbms, action_combo)
                type_edit.textChanged.connect(
                    lambda _value,
                    source=source_column,
                    target_combo=target_combo,
                    targets=target_by_name,
                    items=tuple(row_items),
                    edit=type_edit,
                    dbms=target_dbms,
                    action=action_combo: _refresh_type_override_row(source, target_combo.currentText(), targets, items, edit, dbms, action)
                )
                action_combo.currentIndexChanged.connect(
                    lambda _index,
                    source=source_column,
                    target_combo=target_combo,
                    targets=target_by_name,
                    items=tuple(row_items),
                    action=action_combo,
                    edit=type_edit,
                    dbms=target_dbms: _refresh_column_mapping_row(target_combo.currentText(), source, targets, items, action, edit, dbms)
                )
                if on_change is not None:
                    target_combo.currentTextChanged.connect(lambda _value, callback=on_change: callback())
                    type_edit.textChanged.connect(lambda _value, callback=on_change: callback())
                    action_combo.currentIndexChanged.connect(lambda _index, callback=on_change: callback())
            else:
                action_item = QTableWidgetItem("")
                action_item.setFlags(action_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if color is not None:
                    action_item.setBackground(QBrush(color))
                table.setItem(row, 6, action_item)
        table.resizeColumnsToContents()
        return table, {"target": target_combos, "type": type_edits, "action": action_combos}


    def _incremental_settings_widget(
        columns: tuple[ColumnSelection, ...],
        watermark_column: str,
        start_value: str,
        end_value: str,
    ) -> tuple[QWidget, dict[str, QComboBox | QLineEdit]]:
        widget = QWidget()
        form = QFormLayout(widget)
        watermark = QComboBox()
        watermark.setEditable(True)
        watermark.addItem("")
        watermark.addItems(column.name for column in columns)
        if watermark_column and watermark.findText(watermark_column) < 0:
            watermark.addItem(watermark_column)
        watermark.setCurrentText(watermark_column)
        start = QLineEdit(start_value)
        end = QLineEdit(end_value)
        form.addRow("Watermark 컬럼", watermark)
        form.addRow("시작값", start)
        form.addRow("종료값", end)
        return widget, {"watermark": watermark, "start": start, "end": end}


    def _data_preview_widget(data_preview: dict, fallback_columns: tuple[ColumnSelection, ...]) -> QWidget:
        table = QTableWidget()
        preview_columns = tuple(data_preview.get("columns") or tuple(column.name for column in fallback_columns))
        rows = tuple(data_preview.get("rows") or ())
        message = str(data_preview.get("message") or "")
        table.setColumnCount(len(preview_columns) or 1)
        table.setHorizontalHeaderLabels(list(preview_columns) if preview_columns else ["Preview"])
        if rows:
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column_index, column in enumerate(preview_columns):
                    table.setItem(row_index, column_index, QTableWidgetItem(_preview_value(row.get(column))))
        else:
            table.setRowCount(1)
            table.setItem(0, 0, QTableWidgetItem(message or "표시할 샘플 데이터가 없습니다."))
            if len(preview_columns) > 1:
                table.setSpan(0, 0, 1, len(preview_columns))
        table.resizeColumnsToContents()
        return table


    def _ddl_preview_widget(
        dry_run_table: dict,
        target_schema: str,
        target_table: str,
        target_dbms: Dbms,
        columns: tuple[ColumnSelection, ...],
        target_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
        source_only_actions: dict[str, SourceOnlyColumnAction],
        *,
        existing: bool,
    ) -> QWidget:
        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        target_ddl = str(dry_run_table.get("target_ddl") or dry_run_table.get("ddl") or "")
        alter_candidates = dry_run_table.get("alter_table_candidates") or ()
        sections = []
        if existing:
            alter_candidates = (
                tuple(_draft_mapped_column_change_candidates(target_schema, target_table, target_dbms, columns, target_columns, column_mappings, type_overrides))
                + tuple(_draft_type_change_candidates(target_schema, target_table, target_dbms, columns, target_columns, column_mappings, type_overrides))
                + tuple(_draft_alter_candidates(target_schema, target_table, target_dbms, columns, source_only_actions))
            ) or alter_candidates
        elif target_ddl:
            sections.append(target_ddl)
        else:
            sections.append(_draft_create_table_ddl(target_schema, target_table, target_dbms, columns, column_mappings, type_overrides))
        if alter_candidates:
            sections.append("\n".join(str(candidate) for candidate in alter_candidates))
        preview.setPlainText("\n\n".join(section for section in sections if section).strip() or "변경 DDL 없음.")
        return preview


    def _column_mapping_status(source_column: ColumnSelection | None, target_column: ColumnSelection | None) -> str:
        if source_column is not None and target_column is not None:
            if source_column.name != target_column.name:
                return "컬럼 변경"
            if _same_common_type(source_column, target_column):
                return "mapped"
            return "type 변경"
        if source_column is not None:
            return "target 컬럼 추가"
        return "대상 전용"


    def _column_status_color(status: str) -> QColor | None:
        if status in {"target 컬럼 추가", "type 변경", "컬럼 변경"}:
            return QColor(255, 246, 204)
        if status == "대상 전용":
            return QColor(238, 238, 238)
        if status == "이관 제외":
            return QColor(255, 225, 225)
        if status == "invalid type":
            return QColor(255, 205, 210)
        return None


    def _refresh_column_mapping_row(
        target_column_name: str,
        source_column: ColumnSelection,
        target_by_name: dict[str, ColumnSelection],
        row_items: tuple[QTableWidgetItem, ...],
        action_combo: QComboBox,
        type_edit: QLineEdit,
        target_dbms: Dbms,
    ) -> None:
        target_column = target_by_name.get(target_column_name.strip())
        type_edit.setEnabled(target_column is not None or bool(target_column_name.strip()))
        type_edit.setText(target_column.source_type if target_column is not None else _target_type_for_column(target_dbms, source_column))
        _refresh_target_type_edit_state(type_edit, source_column, target_column, target_dbms)
        action_combo.setEnabled(not bool(target_column))
        status = _column_status_for_type_edit(source_column, target_column, type_edit, target_dbms, action_combo)
        row_items[4].setText(_column_key_label(source_column, target_column))
        row_items[5].setText(status)
        color = _column_status_color(status)
        brush = QBrush(color) if color is not None else QBrush()
        for item in row_items:
            item.setBackground(brush)


    def _refresh_type_override_row(
        source_column: ColumnSelection,
        target_column_name: str,
        target_by_name: dict[str, ColumnSelection],
        row_items: tuple[QTableWidgetItem, ...],
        type_edit: QLineEdit,
        target_dbms: Dbms,
        action_combo: QComboBox,
    ) -> None:
        target_column = target_by_name.get(target_column_name.strip())
        _refresh_target_type_edit_state(type_edit, source_column, target_column, target_dbms)
        status = _column_status_for_type_edit(source_column, target_column, type_edit, target_dbms, action_combo)
        row_items[5].setText(status)
        color = _column_status_color(status)
        brush = QBrush(color) if color is not None else QBrush()
        for item in row_items:
            item.setBackground(brush)


    def _column_status_for_type_edit(
        source_column: ColumnSelection,
        target_column: ColumnSelection | None,
        type_edit: QLineEdit,
        target_dbms: Dbms,
        action_combo: QComboBox | None = None,
    ) -> str:
        if action_combo is not None and str(action_combo.currentData() or "") == SourceOnlyColumnAction.IGNORE.value:
            return "이관 제외"
        if not type_edit.isEnabled():
            return _column_mapping_status(source_column, target_column)
        value = type_edit.text().strip()
        if not _is_valid_target_type(target_dbms, value):
            return "invalid type"
        default_type = target_column.source_type if target_column is not None else _target_type_for_column(target_dbms, source_column)
        if _normalize_type_text(value) != _normalize_type_text(default_type):
            return "type 변경"
        return _column_mapping_status(source_column, target_column)


    def _refresh_target_type_edit_state(
        type_edit: QLineEdit,
        source_column: ColumnSelection,
        target_column: ColumnSelection | None,
        target_dbms: Dbms,
    ) -> None:
        value = type_edit.text().strip()
        if not type_edit.isEnabled() or _is_valid_target_type(target_dbms, value):
            type_edit.setStyleSheet("")
            type_edit.setToolTip("")
            return
        type_edit.setStyleSheet("border: 1px solid #c62828; background: #ffebee;")
        type_edit.setToolTip(f"{target_dbms.value}에서 인식하지 못하는 target type입니다: {value}")


    def _same_common_type(source_column: ColumnSelection, target_column: ColumnSelection) -> bool:
        return (
            source_column.common_type.kind,
            source_column.common_type.length,
            source_column.common_type.precision,
            source_column.common_type.scale,
        ) == (
            target_column.common_type.kind,
            target_column.common_type.length,
            target_column.common_type.precision,
            target_column.common_type.scale,
        )


    def _column_key_label(source_column: ColumnSelection | None, target_column: ColumnSelection | None) -> str:
        if (source_column is not None and source_column.primary_key) or (target_column is not None and target_column.primary_key):
            return "PK"
        return ""


    def _draft_create_table_ddl(
        target_schema: str,
        target_table: str,
        target_dbms: Dbms,
        columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
    ) -> str:
        table_name = _qualified_table_name(target_dbms, target_schema, target_table)
        column_lines = [
            f"  {_quote_identifier_for(target_dbms, column_mappings.get(column.name, column.name))} {_target_type_for_column(target_dbms, column, type_overrides.get(column.name))}{' NULL' if column.nullable else ' NOT NULL'}"
            for column in columns
        ]
        primary_key_columns = [_quote_identifier_for(target_dbms, column_mappings.get(column.name, column.name)) for column in columns if column.primary_key]
        if primary_key_columns:
            column_lines.append(f"  PRIMARY KEY ({', '.join(primary_key_columns)})")
        if not column_lines:
            return ""
        return f"CREATE TABLE {table_name} (\n" + ",\n".join(column_lines) + "\n);"


    def _draft_mapped_column_change_candidates(
        target_schema: str,
        target_table: str,
        target_dbms: Dbms,
        columns: tuple[ColumnSelection, ...],
        target_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
    ) -> list[str]:
        table_name = _qualified_table_name(target_dbms, target_schema, target_table)
        columns_by_name = {column.name: column for column in columns}
        existing_target_column_names = {column.name for column in target_columns}
        ddls: list[str] = []
        for source_column, target_column in sorted(column_mappings.items()):
            if not target_column or target_column in existing_target_column_names:
                continue
            column = columns_by_name.get(source_column)
            if column is None:
                continue
            if source_column in existing_target_column_names:
                ddls.append(
                    f"ALTER TABLE {table_name} RENAME COLUMN {_quote_identifier_for(target_dbms, source_column)} TO {_quote_identifier_for(target_dbms, target_column)};"
                )
            else:
                ddls.append(
                    f"ALTER TABLE {table_name} ADD COLUMN {_quote_identifier_for(target_dbms, target_column)} {_target_type_for_column(target_dbms, column, type_overrides.get(source_column))}{' NULL' if column.nullable else ' NOT NULL'};"
                )
        return ddls


    def _draft_type_change_candidates(
        target_schema: str,
        target_table: str,
        target_dbms: Dbms,
        columns: tuple[ColumnSelection, ...],
        target_columns: tuple[ColumnSelection, ...],
        column_mappings: dict[str, str],
        type_overrides: dict[str, str],
    ) -> list[str]:
        table_name = _qualified_table_name(target_dbms, target_schema, target_table)
        target_columns_by_name = {column.name: column for column in target_columns}
        ddls: list[str] = []
        for source_column in columns:
            target_column_name = column_mappings.get(source_column.name, source_column.name)
            original_target_column = target_columns_by_name.get(target_column_name) or target_columns_by_name.get(source_column.name)
            target_type = type_overrides.get(source_column.name)
            if original_target_column is None:
                continue
            if target_type is None and _same_common_type(source_column, original_target_column):
                continue
            if target_type is not None and _normalize_type_text(target_type) == _normalize_type_text(original_target_column.source_type):
                continue
            if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
                ddls.append(
                    f"ALTER TABLE {table_name} MODIFY COLUMN {_quote_identifier_for(target_dbms, target_column_name)} {_target_type_for_column(target_dbms, source_column, target_type)}{' NULL' if source_column.nullable else ' NOT NULL'};"
                )
            else:
                ddls.append(
                    f"ALTER TABLE {table_name} ALTER COLUMN {_quote_identifier_for(target_dbms, target_column_name)} TYPE {_target_type_for_column(target_dbms, source_column, target_type)};"
                )
        return ddls


    def _draft_alter_candidates(
        target_schema: str,
        target_table: str,
        target_dbms: Dbms,
        columns: tuple[ColumnSelection, ...],
        source_only_actions: dict[str, SourceOnlyColumnAction],
    ) -> list[str]:
        table_name = _qualified_table_name(target_dbms, target_schema, target_table)
        columns_by_name = {column.name: column for column in columns}
        ddls: list[str] = []
        for source_column, action in sorted(source_only_actions.items()):
            if action is not SourceOnlyColumnAction.ADD_TO_TARGET:
                continue
            column = columns_by_name.get(source_column)
            if column is None:
                continue
            ddls.append(
                f"ALTER TABLE {table_name} ADD COLUMN {_quote_identifier_for(target_dbms, column.name)} {_target_type_for_column(target_dbms, column)}{' NULL' if column.nullable else ' NOT NULL'};"
            )
        return ddls


    def _target_type_for_column(target_dbms: Dbms, column: ColumnSelection, target_type_override: str | None = None) -> str:
        if target_type_override:
            return target_type_override
        if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
            return common_type_to_mysql(column.common_type)
        return common_type_to_postgres(column.common_type)


    def _is_valid_target_type(target_dbms: Dbms, target_type: str) -> bool:
        if not target_type.strip():
            return False
        common_type = mysql_type_to_common(target_type) if target_dbms in {Dbms.MYSQL, Dbms.MARIADB} else postgres_type_to_common(target_type)
        return common_type.kind is not CommonTypeKind.UNKNOWN


    def _normalize_type_text(value: str) -> str:
        return " ".join(value.strip().lower().split())


    def _qualified_table_name(target_dbms: Dbms, schema: str, table: str) -> str:
        quote = _quote_identifier_for
        return f"{quote(target_dbms, schema)}.{quote(target_dbms, table)}"


    def _quote_identifier_for(target_dbms: Dbms, value: str) -> str:
        if target_dbms in {Dbms.MYSQL, Dbms.MARIADB}:
            return "`" + value.replace("`", "``") + "`"
        return '"' + value.replace('"', '""') + '"'


    def _preview_value(value: object) -> str:
        if value is None:
            return "<null>"
        text = str(value)
        if len(text) > 80:
            return text[:77] + "..."
        return text


    def _default_target_schema_name(config: AppConfig, source_schema: str) -> str:
        if config.target.schema_name:
            return config.target.schema_name
        if config.target.dbms in {Dbms.MYSQL, Dbms.MARIADB}:
            return config.target.database
        return source_schema


    def _default_gui_path_state() -> GuiPathState:
        return GuiPathState(
            config_path=str(Path("config.yml").resolve()),
            schema_path="",
            output_dir=str(Path("reports/live").resolve()),
            checkpoint_path=str(Path("checkpoints/migration.sqlite").resolve()),
        )


    def _parse_source_only_columns(raw_value: str) -> dict[str, SourceOnlyColumnAction]:
        actions: dict[str, SourceOnlyColumnAction] = {}
        for line in raw_value.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if ":" not in stripped:
                continue
            column, action = (part.strip() for part in stripped.split(":", 1))
            if not column:
                continue
            try:
                actions[column] = SourceOnlyColumnAction(action)
            except ValueError:
                continue
        return actions


    def _app_icon() -> QIcon:
        try:
            resource = files("db_migrator.gui.assets").joinpath("app-icon.ico")
            with as_file(resource) as icon_path:
                return QIcon(str(icon_path))
        except Exception:
            return QIcon()
