from __future__ import annotations

import sys
from importlib.resources import as_file, files
from pathlib import Path
from typing import Callable

import yaml

from db_migrator.application import CommandResult, MigrationApplicationService, TableSelection
from db_migrator.application.events import event_to_view
from db_migrator.application.safety import evaluate_dry_run_gate
from db_migrator.config.loader import ConfigLoadError, load_config
from db_migrator.config.models import AppConfig, Dbms, ExistingTablePolicy, IndexApplyTiming, MigrationMode, TableRunConfig
from db_migrator.core.events import EventPublisher, FileEventPublisher, MigrationEvent


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
    from PySide6.QtGui import QDesktopServices, QIcon
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

        def publish(self, event: MigrationEvent) -> None:
            self._file_events.publish(event)
            self._emit_event(event)


    class CommandWorker(QObject):
        event_published = Signal(object)
        completed = Signal(object)

        def __init__(self, label: str, command: Callable[[EventPublisher], CommandResult]) -> None:
            super().__init__()
            self._label = label
            self._command = command

        @Slot()
        def run(self) -> None:
            try:
                result = self._command(WorkerEventPublisher(self.event_published.emit))
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
            self._manual_apply_foreign_keys = False
            self._syncing_foreign_key_option = False
            self._last_dry_run_report: Path | None = None
            self._last_report_html: Path | None = None
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

            self.config_path = QLineEdit(str(Path("config.yml").resolve()))
            self.schema_path = QLineEdit()
            self.output_dir = QLineEdit(str(Path("reports/live").resolve()))
            self.checkpoint_path = QLineEdit(str(Path("checkpoints/migration.sqlite").resolve()))
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
            test_button = QPushButton("원본 연결 테스트")
            test_button.clicked.connect(self._test_source)
            self._buttons.append(test_button)
            form.addRow("DBMS", self.source_dbms)
            form.addRow("호스트", self.source_host)
            form.addRow("포트", self.source_port)
            form.addRow("데이터베이스", self.source_database)
            form.addRow("스키마", self.source_schema)
            form.addRow("사용자", self.source_user)
            form.addRow("비밀번호", self.source_password)
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
            test_button = QPushButton("대상 연결 테스트")
            test_button.clicked.connect(self._test_target)
            self._buttons.append(test_button)
            form.addRow("DBMS", self.target_dbms)
            form.addRow("호스트", self.target_host)
            form.addRow("포트", self.target_port)
            form.addRow("데이터베이스", self.target_database)
            form.addRow("기본 대상 스키마", self.target_schema)
            form.addRow("사용자", self.target_user)
            form.addRow("비밀번호", self.target_password)
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
            self.apply_foreign_keys = QCheckBox("테이블 생성 후 외래키 적용")
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
                self._load_config_into_form(show_errors=True)

        def _choose_schema(self) -> None:
            path, _ = QFileDialog.getOpenFileName(self, "Choose schema snapshot", str(Path.cwd()), "JSON files (*.json);;All files (*)")
            if path:
                self.schema_path.setText(path)

        def _choose_output_dir(self) -> None:
            path = QFileDialog.getExistingDirectory(self, "Choose output directory", str(Path.cwd()))
            if path:
                self.output_dir.setText(path)

        def _choose_checkpoint(self) -> None:
            path, _ = QFileDialog.getSaveFileName(self, "Choose checkpoint DB", str(Path.cwd()), "SQLite files (*.sqlite *.db);;All files (*)")
            if path:
                self.checkpoint_path.setText(path)

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
            self.status.setText(f"실행 중: {label}")
            self.log.appendPlainText(f"> {label}")
            self._thread.start()

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
                self._populate_tables(result.details.get("tables", ()))
            if result.success and result.command == "dry-run" and result.report_html is not None:
                self._last_dry_run_report = result.report_html
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
                self._load_config_into_form(show_errors=True)

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
            self.target_dbms.setCurrentText(config.target.dbms.value)
            self.target_host.setText(config.target.host)
            self.target_port.setValue(config.target.port)
            self.target_database.setText(config.target.database)
            self.target_schema.setText(config.target.schema_name or "")
            self.target_user.setText(config.target.user)
            self.target_password.setText(config.target.password or "")
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
            config.target.dbms = Dbms(self.target_dbms.currentText())
            config.target.host = self.target_host.text().strip()
            config.target.port = self.target_port.value()
            config.target.database = self.target_database.text().strip()
            config.target.schema_name = self.target_schema.text().strip() or None
            config.target.user = self.target_user.text().strip()
            config.target.password = self.target_password.text() or None
            config.target.environment = AppConfig().target.environment
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
            return True

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
                    self.apply_foreign_keys.setToolTip("기본 이관은 테이블 생성 후 외래키를 자동 적용합니다.")
                    return
                if mode == "테이블 이관":
                    self.apply_foreign_keys.setEnabled(True)
                    self.apply_foreign_keys.setChecked(self._manual_apply_foreign_keys)
                    self.apply_foreign_keys.setToolTip("필요한 경우 테이블 생성 이후 외래키 DDL을 함께 적용합니다.")
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
            try:
                config = load_config(self._config())
            except ConfigLoadError:
                config = AppConfig()
            for table in tables:
                table_config = config.tables.get(table.identifier)
                source_schema = table.identifier.rsplit(".", 1)[0]
                target_schema = table_config.target_schema if table_config is not None else None
                default_target_schema = config.target.schema_name or source_schema
                target_table = table_config.target_table if table_config is not None else None
                incremental = table_config.incremental if table_config is not None else None
                item = QListWidgetItem()
                item.setData(TABLE_ID_ROLE, table.identifier)
                item.setData(TARGET_TABLE_ROLE, target_table or table.table)
                item.setData(TARGET_SCHEMA_ROLE, target_schema or default_target_schema)
                item.setData(WATERMARK_COLUMN_ROLE, incremental.watermark_column if incremental is not None else "")
                item.setData(WATERMARK_START_ROLE, incremental.start_value if incremental is not None else "")
                item.setData(WATERMARK_END_ROLE, incremental.end_value if incremental is not None else "")
                item.setData(COLUMN_COUNT_ROLE, table.column_count)
                item.setData(ESTIMATED_ROWS_ROLE, table.estimated_rows)
                item.setData(HAS_PRIMARY_KEY_ROLE, table.has_primary_key)
                item.setData(TABLE_SELECTED_ROLE, True)
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
                default_target_schema = config.target.schema_name or source_schema
                target_schema = str(item.data(TARGET_SCHEMA_ROLE) or "").strip()
                target_table = str(item.data(TARGET_TABLE_ROLE) or "").strip()
                watermark_column = str(item.data(WATERMARK_COLUMN_ROLE) or "").strip()
                start_value = str(item.data(WATERMARK_START_ROLE) or "").strip()
                end_value = str(item.data(WATERMARK_END_ROLE) or "").strip()
                if not target_schema or target_schema == default_target_schema:
                    target_schema = None
                if not target_table or target_table == source_table:
                    target_table = None
                if not any((target_schema, target_table, watermark_column, start_value, end_value)):
                    config.tables.pop(identifier, None)
                    continue
                table_config = config.tables.get(identifier, TableRunConfig())
                table_config.target_schema = target_schema
                table_config.target_table = target_table
                table_config.incremental.watermark_column = watermark_column or None
                table_config.incremental.start_value = start_value or None
                table_config.incremental.end_value = end_value or None
                config.tables[identifier] = table_config

        def _table_item_label(self, table: TableSelection, item: QListWidgetItem) -> str:
            source_schema = table.identifier.rsplit(".", 1)[0]
            target_schema = str(item.data(TARGET_SCHEMA_ROLE) or source_schema)
            target_table = str(item.data(TARGET_TABLE_ROLE) or table.table)
            label = f"{table.identifier}"
            if target_schema != source_schema or target_table != table.table:
                label += f" -> {target_schema}.{target_table}"
            label += f"  컬럼={table.column_count}"
            if table.estimated_rows is not None:
                label += f" 예상 행={table.estimated_rows}"
            if str(item.data(WATERMARK_COLUMN_ROLE) or "").strip():
                label += "  증분 설정됨"
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
            target_button = QPushButton("대상 설정")
            target_button.setToolTip("이 테이블의 대상 스키마와 테이블명을 변경합니다.")
            target_button.clicked.connect(lambda _checked=False, selected_item=item: self._edit_target_table_settings(selected_item))
            incremental_button = QPushButton("증분")
            incremental_button.setToolTip("이 테이블의 증분 이관 watermark를 설정합니다.")
            incremental_button.clicked.connect(lambda _checked=False, selected_item=item: self._edit_incremental_table_settings(selected_item))
            layout.addWidget(checkbox)
            layout.addWidget(label, stretch=1)
            layout.addWidget(target_button)
            layout.addWidget(incremental_button)
            return row

        def _row_checkbox(self, item: QListWidgetItem) -> QCheckBox | None:
            widget = self.table_list.itemWidget(item)
            return widget.findChild(QCheckBox) if widget is not None else None

        def _edit_target_table_settings(self, item: QListWidgetItem | None = None) -> None:
            item = item or self.table_list.currentItem()
            if item is None:
                QMessageBox.warning(self, "테이블 선택 필요", "대상 설정을 바꿀 테이블을 선택하세요.")
                return
            identifier = str(item.data(TABLE_ID_ROLE))
            source_schema, source_table = identifier.rsplit(".", 1)
            current_schema = str(item.data(TARGET_SCHEMA_ROLE) or self.target_schema.text().strip() or source_schema)
            current = str(item.data(TARGET_TABLE_ROLE) or source_table)
            dialog = QDialog(self)
            dialog.setWindowTitle("대상 테이블 설정")
            form = QFormLayout(dialog)
            target_schema = QLineEdit(current_schema)
            target_table = QLineEdit(current)
            form.addRow("대상 스키마", target_schema)
            form.addRow("대상 테이블명", target_table)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            item.setData(TARGET_SCHEMA_ROLE, target_schema.text().strip() or self.target_schema.text().strip() or source_schema)
            item.setData(TARGET_TABLE_ROLE, target_table.text().strip() or source_table)
            self._refresh_table_item_label(item)

        def _edit_incremental_table_settings(self, item: QListWidgetItem | None = None) -> None:
            item = item or self.table_list.currentItem()
            if item is None:
                QMessageBox.warning(self, "테이블 선택 필요", "증분 설정을 바꿀 테이블을 선택하세요.")
                return
            dialog = QDialog(self)
            dialog.setWindowTitle("증분 이관 설정")
            form = QFormLayout(dialog)
            watermark_column = QLineEdit(str(item.data(WATERMARK_COLUMN_ROLE) or ""))
            start_value = QLineEdit(str(item.data(WATERMARK_START_ROLE) or ""))
            end_value = QLineEdit(str(item.data(WATERMARK_END_ROLE) or ""))
            form.addRow("Watermark 컬럼", watermark_column)
            form.addRow("시작값", start_value)
            form.addRow("종료값", end_value)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            form.addRow(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            item.setData(WATERMARK_COLUMN_ROLE, watermark_column.text().strip())
            item.setData(WATERMARK_START_ROLE, start_value.text().strip())
            item.setData(WATERMARK_END_ROLE, end_value.text().strip())
            self._refresh_table_item_label(item)

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


    def _app_icon() -> QIcon:
        try:
            resource = files("db_migrator.gui.assets").joinpath("app-icon.ico")
            with as_file(resource) as icon_path:
                return QIcon(str(icon_path))
        except Exception:
            return QIcon()
