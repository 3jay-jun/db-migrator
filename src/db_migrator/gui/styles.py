from __future__ import annotations

from pathlib import Path


def jigration_stylesheet() -> str:
    check_icon = Path(__file__).with_name("checkbox-check.png").as_posix()
    switch_on = Path(__file__).with_name("switch-on.png").as_posix()
    switch_off = Path(__file__).with_name("switch-off.png").as_posix()
    combo_arrow = Path(__file__).with_name("combo-arrow.png").as_posix()
    return """
    QWidget {
        color: #1F2937;
        font-family: "Malgun Gothic", "Segoe UI", Arial, sans-serif;
        font-size: 13px;
    }

    QMainWindow,
    QWidget#appRoot {
        background: #F3F4F6;
    }

    QLabel,
    QCheckBox,
    QWidget#transparentRow {
        background: transparent;
    }

    QLabel#fieldLabel {
        color: #6B7280;
        font-size: 11px;
        font-weight: 600;
    }

    QLabel#sectionTitle {
        color: #111827;
        font-size: 15px;
        font-weight: 700;
    }

    QLabel#countText {
        color: #344054;
        font-size: 12px;
        font-weight: 600;
    }

    QLabel#selectedCountValue {
        color: #2563EB;
        font-size: 11px;
        font-weight: 700;
    }

    QLabel#selectedCountSuffix {
        color: #6B7280;
        font-size: 11px;
        font-weight: 400;
        margin-left: -6px;
    }

    QLabel#actionSeparator {
        color: #D0D5DD;
        font-size: 12px;
        font-weight: 400;
        padding: 0;
    }

    QLabel#tableSummary {
        color: #6B7280;
        font-size: 11px;
        font-weight: 500;
    }

    QLabel#tableSummaryCount {
        color: #0F766E;
        font-size: 11px;
        font-weight: 700;
    }

    QLabel#tableHint {
        color: #6B7280;
        font-size: 11px;
        font-weight: 400;
    }

    QLabel#tableNamePrimary {
        color: #1F2937;
        font-size: 12px;
        font-weight: 700;
    }

    QLabel#tableNameSecondary {
        color: #6B7280;
        font-size: 9px;
        font-weight: 400;
    }

    QGroupBox {
        background: #FFFFFF;
        border: 1px solid #D8DEE8;
        border-radius: 8px;
        font-size: 15px;
        font-weight: 700;
        margin-top: 0;
        padding: 26px 14px 12px 14px;
    }

    QGroupBox::title {
        subcontrol-origin: padding;
        subcontrol-position: top left;
        left: 12px;
        top: 6px;
        padding: 0;
        color: #111827;
        background: transparent;
    }

    QGroupBox#contentPanel {
        padding: 12px 14px;
    }

    QLineEdit,
    QComboBox,
    QSpinBox {
        min-height: 32px;
        max-height: 32px;
        border: 1px solid #C5CEDB;
        border-radius: 6px;
        background: #FFFFFF;
        color: #1F2937;
        padding: 0 10px;
    }

    QComboBox {
        padding: 0 32px 0 10px;
    }

    QComboBox::drop-down {
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 30px;
        border: 0;
        background: transparent;
    }

    QComboBox::down-arrow {
        image: url("__COMBO_ARROW__");
        width: 10px;
        height: 6px;
    }

    QComboBox QAbstractItemView {
        border: 1px solid #C5CEDB;
        border-radius: 6px;
        background: #FFFFFF;
        color: #1F2937;
        selection-background-color: #EAF1FF;
        selection-color: #1F2937;
        outline: 0;
    }

    QLineEdit:focus,
    QComboBox:focus,
    QSpinBox:focus {
        border: 1px solid #2563EB;
    }

    QSpinBox::up-button,
    QSpinBox::down-button {
        width: 0;
        border: 0;
    }

    QPushButton {
        min-height: 28px;
        max-height: 30px;
        border: 1px solid #C5CEDB;
        border-radius: 6px;
        background: #FFFFFF;
        color: #344054;
        padding: 0 12px;
        font-weight: 600;
    }

    QPushButton:hover {
        border-color: #2563EB;
        background: #EAF1FF;
        color: #2563EB;
    }

    QPushButton:disabled {
        color: #8A94A6;
        background: #EEF2F7;
    }

    QPushButton#primaryButton {
        min-height: 32px;
        border-color: #2563EB;
        background: #2563EB;
        color: #FFFFFF;
        font-weight: 700;
    }

    QPushButton#primaryButton:hover {
        background: #1D4ED8;
    }

    QPushButton#textButton {
        border: 0;
        background: transparent;
        color: #2563EB;
        padding: 0 1px;
        min-height: 18px;
        max-height: 20px;
        font-size: 12px;
        font-weight: 700;
    }

    QPushButton#mutedTextButton {
        border: 0;
        background: transparent;
        color: #667085;
        padding: 0 1px;
        min-height: 18px;
        max-height: 20px;
        font-size: 12px;
        font-weight: 700;
    }

    QPushButton#dangerButton {
        border-color: transparent;
        background: transparent;
        color: #B42318;
        font-weight: 700;
    }

    QPushButton#rowSettingsButton {
        min-height: 27px;
        max-height: 28px;
        padding: 0 8px;
        font-size: 11px;
    }

    QCheckBox {
        color: #344054;
        font-size: 12px;
        spacing: 8px;
    }

    QCheckBox::indicator {
        width: 14px;
        height: 14px;
        border-radius: 4px;
        background: #FFFFFF;
        border: 1px solid #C5CEDB;
    }

    QCheckBox::indicator:hover {
        border: 1px solid #2563EB;
    }

    QCheckBox::indicator:checked {
        background: #2563EB;
        border: 1px solid #2563EB;
        image: url("__CHECK_ICON__");
    }

    QCheckBox::indicator:disabled {
        background: #EEF2F7;
        border: 1px solid #D8DEE8;
    }

    QCheckBox#switchOption {
        spacing: 9px;
    }

    QCheckBox#switchOption::indicator {
        width: 36px;
        height: 20px;
        border: 0;
        background: transparent;
        image: url("__SWITCH_OFF__");
    }

    QCheckBox#switchOption::indicator:checked {
        border: 0;
        background: transparent;
        image: url("__SWITCH_ON__");
    }

    QCheckBox#switchOption::indicator:disabled {
        border: 0;
        background: transparent;
        image: url("__SWITCH_OFF__");
    }

    QTableWidget {
        border: 1px solid #D8DEE8;
        border-radius: 7px;
        background: #FFFFFF;
        alternate-background-color: #FFFFFF;
        selection-background-color: #FFFFFF;
        selection-color: #1F2937;
        font-size: 12px;
    }

    QHeaderView::section {
        min-height: 28px;
        max-height: 30px;
        border: 0;
        border-bottom: 1px solid #D8DEE8;
        background: #EEF2F7;
        color: #475467;
        font-size: 11px;
        font-weight: 700;
        padding: 0 8px;
    }

    QTableWidget::item {
        padding: 0 8px;
        border-bottom: 1px solid #E5EAF1;
    }

    QTableWidget::item:selected {
        background: #FFFFFF;
        color: #1F2937;
    }

    QWidget#tableSelectCell {
        background: transparent;
        border-left: 0;
    }

    QWidget#tableSelectCell[focused="true"] {
        border-left: 3px solid #2563EB;
    }

    QPlainTextEdit {
        border: 1px solid #D8DEE8;
        border-radius: 7px;
        background: #FBFCFE;
        color: #475467;
        font-family: Consolas, "Courier New", monospace;
        font-size: 11px;
        padding: 12px;
    }

    QTabWidget::pane {
        border: 1px solid #D8DEE8;
        border-radius: 7px;
        background: #FFFFFF;
    }

    QTabBar::tab {
        padding: 8px 12px;
        color: #6B7280;
        background: #FFFFFF;
        border: 0;
        border-bottom: 2px solid transparent;
        font-weight: 700;
    }

    QTabBar::tab:selected {
        color: #2563EB;
        border-bottom: 2px solid #2563EB;
    }
    """.replace("__CHECK_ICON__", check_icon).replace("__SWITCH_ON__", switch_on).replace("__SWITCH_OFF__", switch_off).replace("__COMBO_ARROW__", combo_arrow)
