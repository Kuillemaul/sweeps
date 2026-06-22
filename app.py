from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from sweep_engine import (
    Allocation,
    CUP_SWEEPS,
    SweepBook,
    load_attendees_file,
    load_race_csv,
    default_payout_settings,
    load_workbook_data,
    money,
    parse_race_number_from_path,
    split_payout_setting_key,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Flemington Sweeps")
        # The admin app is deliberately designed for a 1080p minimum workspace.
        # Smaller windows make the race tables, Cup controls and web-display tools too cramped.
        self.setMinimumSize(1920, 1080)
        self.resize(1920, 1080)
        self.book = self.load_default_data()
        self.current_event_path: Optional[Path] = None
        self.theme_mode = "dark"
        self.web_server: Optional[LocalWebServer] = None
        self.current_cup_reveal: Optional[dict[str, object]] = None
        self.current_cup_spin_event: Optional[dict[str, object]] = None
        self._cup_spin_serial = 0
        self.cup_drawn_reveals: dict[str, List[dict[str, object]]] = {}
        self.race_pages: dict[int, RacePage] = {}
        self.stack = QStackedWidget()
        self.nav_layout = QVBoxLayout()
        self.nav_layout.setContentsMargins(14, 14, 14, 14)
        self.nav_layout.setSpacing(8)
        self.main_layout = QHBoxLayout()
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        root = QWidget()
        root.setLayout(self.main_layout)
        self.setCentralWidget(root)
        self.left_panel = QFrame()
        self.left_panel.setObjectName("leftPanel")
        self.left_panel.setFixedWidth(360)
        self.left_panel.setLayout(self.nav_layout)
        self.main_layout.addWidget(self.left_panel)
        self.main_layout.addWidget(self.stack, 1)
        self.build_menu()
        self.build_pages()
        self.apply_theme()

    def closeEvent(self, event) -> None:
        if self.web_server:
            self.web_server.stop()
        super().closeEvent(event)

    def load_default_data(self) -> SweepBook:
        # Start with an empty book. Race buttons are created only after race data
        # is imported or a workbook is loaded. This avoids showing fake/demo races
        # before the user has imported the real race card.
        return SweepBook()

    def build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")

        new_action = QAction("New / Clear Event", self)
        new_action.triggered.connect(self.clear_all_data)
        file_menu.addAction(new_action)

        load_event_action = QAction("Load Event", self)
        load_event_action.triggered.connect(self.load_event_dialog)
        file_menu.addAction(load_event_action)

        save_action = QAction("Save Event", self)
        save_action.triggered.connect(self.save_event_dialog)
        file_menu.addAction(save_action)

        save_as_action = QAction("Save Event As", self)
        save_as_action.triggered.connect(self.save_event_as_dialog)
        file_menu.addAction(save_as_action)

        file_menu.addSeparator()

        load_action = QAction("Load Race Workbook", self)
        load_action.triggered.connect(self.load_workbook_dialog)
        file_menu.addAction(load_action)

        import_action = QAction("Import Race CSV", self)
        import_action.triggered.connect(self.open_import_page)
        file_menu.addAction(import_action)

        file_menu.addSeparator()

        generate_action = QAction("Generate All Normal Sweeps", self)
        generate_action.triggered.connect(self.generate_all_normal_sweeps)
        file_menu.addAction(generate_action)

        export_action = QAction("Export / Print Sheets", self)
        export_action.triggered.connect(self.export_print_sheets_dialog)
        file_menu.addAction(export_action)

    def clear_all_data(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear all data",
            "Clear all imported races, attendees, generated sweeps and draw results?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        current_seed = self.seed_box.value() if hasattr(self, "seed_box") and self.seed_box.value() else None
        self.book = SweepBook()
        self.current_event_path = None
        self.current_cup_reveal = None
        self.current_cup_spin_event = None
        self._cup_spin_serial = 0
        self.cup_drawn_reveals = {}
        self.book.set_seed(current_seed)
        self.race_pages = {}
        self.build_pages()
        QMessageBox.information(self, "Data cleared", "All race, attendee and sweep data has been cleared.")

    def save_event_dialog(self) -> None:
        if self.current_event_path is None:
            self.save_event_as_dialog()
            return
        self.save_event_to_path(self.current_event_path)

    def save_event_as_dialog(self) -> None:
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Save sweep event",
            str(Path("data") / "events" / "sweeps_event.sweeps.json"),
            "Sweep Event Files (*.sweeps.json);;JSON Files (*.json)",
        )
        if not file_name:
            return
        self.save_event_to_path(Path(file_name))

    def save_event_to_path(self, path: Path) -> None:
        try:
            path = Path(path)
            self.book.audit("Saved event", str(path))
            path.parent.mkdir(parents=True, exist_ok=True)
            event_data = self.book.to_dict()
            event_data["cup_drawn_reveals"] = self.cup_drawn_reveals
            event_data["current_cup_reveal"] = self.current_cup_reveal
            event_data["current_cup_spin_event"] = self.current_cup_spin_event
            path.write_text(json.dumps(event_data, indent=2), encoding="utf-8")
            self.current_event_path = path
            QMessageBox.information(self, "Event saved", f"Saved event to {path.name}")
            self.refresh_all_views()
        except Exception as error:
            QMessageBox.critical(self, "Could not save event", str(error))

    def load_event_dialog(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Load sweep event",
            "",
            "Sweep Event Files (*.sweeps.json);;JSON Files (*.json)",
        )
        if not file_name:
            return
        try:
            data = json.loads(Path(file_name).read_text(encoding="utf-8"))
            self.book = SweepBook.from_dict(data)
            self.book.audit("Loaded event", str(file_name))
            raw_draws = data.get("cup_drawn_reveals", {}) if isinstance(data, dict) else {}
            self.cup_drawn_reveals = raw_draws if isinstance(raw_draws, dict) else {}
            current_reveal = data.get("current_cup_reveal") if isinstance(data, dict) else None
            self.current_cup_reveal = current_reveal if isinstance(current_reveal, dict) else None
            raw_spin = data.get("current_cup_spin_event") if isinstance(data, dict) else None
            self.current_cup_spin_event = raw_spin if isinstance(raw_spin, dict) else None
            self.current_event_path = Path(file_name)
            self.race_pages = {}
            self.build_pages()
            QMessageBox.information(self, "Event loaded", f"Loaded {Path(file_name).name}")
        except Exception as error:
            QMessageBox.critical(self, "Could not load event", str(error))

    def export_print_sheets_dialog(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not folder:
            return
        try:
            export_print_sheets(self.book, Path(folder))
            self.book.audit("Exported print sheets", folder)
            self.refresh_all_views()
            QMessageBox.information(self, "Export complete", f"Exported printable sheets and CSV files to:\n{folder}")
        except Exception as error:
            QMessageBox.critical(self, "Could not export sheets", str(error))

    def open_import_page(self) -> None:
        if hasattr(self, "import_page"):
            self.stack.setCurrentWidget(self.import_page)

    def rebuild_after_data_import(
        self,
        status: str = "",
        race_summary: Optional[List[List[object]]] = None,
        attendee_summary: Optional[List[List[object]]] = None,
    ) -> None:
        self.race_pages = {}
        self.build_pages()
        self.stack.setCurrentWidget(self.import_page)
        if status:
            self.import_page.status_label.setText(status)
        if race_summary:
            self.import_page.set_race_summary(race_summary)
        if attendee_summary:
            self.import_page.set_attendee_summary(attendee_summary)

    def build_pages(self) -> None:
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        clear_layout(self.nav_layout)
        title = QLabel("Flemington Sweeps")
        title.setObjectName("navTitle")
        self.nav_layout.addWidget(title)

        self.dashboard_page = DashboardPage(self.book, self)
        dashboard_button = QPushButton("Dashboard")
        dashboard_button.setMinimumHeight(46)
        self.stack.addWidget(self.dashboard_page)
        dashboard_button.clicked.connect(lambda checked=False, p=self.dashboard_page: self.stack.setCurrentWidget(p))
        self.nav_layout.addWidget(dashboard_button)

        if self.book.races:
            for race_no in sorted(self.book.races):
                race = self.book.races[race_no]
                button_text = "Race 7 - Cup Special" if race_no == 7 else f"Race {race_no}"
                button = QPushButton(button_text)
                button.setToolTip(f"{race.race_name} - {len(race.runners)} runners")
                button.setMinimumHeight(42)
                page = RacePage(self.book, race_no, self)
                self.race_pages[race_no] = page
                self.stack.addWidget(page)
                button.clicked.connect(lambda checked=False, p=page: self.stack.setCurrentWidget(p))
                self.nav_layout.addWidget(button)
        else:
            no_races = QLabel("Import race data to show race buttons.")
            no_races.setObjectName("navLabel")
            no_races.setWordWrap(True)
            self.nav_layout.addWidget(no_races)
        self.nav_layout.addSpacing(12)
        self.import_page = ImportDataPage(self.book, self)
        self.money_page = MoneyOwingPage(self.book, self)
        self.attendees_page = AttendeesPage(self.book, self)
        self.cup_page = CupSweepPage(self.book, self)
        self.payout_settings_page = PayoutSettingsPage(self.book, self)
        self.audit_page = AuditLogPage(self.book, self)
        self.web_display_page = WebDisplayPage(self.book, self)
        for label, page in [
            ("Import Data", self.import_page),
            ("Money Owing", self.money_page),
            ("Attendees", self.attendees_page),
            ("Cup Sweep Generator", self.cup_page),
            ("Payout Settings", self.payout_settings_page),
            ("Audit Log", self.audit_page),
            ("Web Display", self.web_display_page),
        ]:
            button = QPushButton(label)
            button.setMinimumHeight(46)
            self.stack.addWidget(page)
            button.clicked.connect(lambda checked=False, p=page: self.stack.setCurrentWidget(p))
            self.nav_layout.addWidget(button)

        save_button = QPushButton("Save Event")
        save_button.setMinimumHeight(46)
        save_button.clicked.connect(self.save_event_dialog)
        self.nav_layout.addWidget(save_button)

        load_button = QPushButton("Load Event")
        load_button.setMinimumHeight(46)
        load_button.clicked.connect(self.load_event_dialog)
        self.nav_layout.addWidget(load_button)

        export_button = QPushButton("Export / Print Sheets")
        export_button.setMinimumHeight(46)
        export_button.clicked.connect(self.export_print_sheets_dialog)
        self.nav_layout.addWidget(export_button)

        clear_button = QPushButton("Clear Data")
        clear_button.setObjectName("dangerButton")
        clear_button.setMinimumHeight(46)
        clear_button.clicked.connect(self.clear_all_data)
        self.nav_layout.addWidget(clear_button)

        self.nav_layout.addStretch(1)

        theme_label = QLabel("Theme")
        theme_label.setObjectName("navLabel")
        self.nav_layout.addWidget(theme_label)

        self.theme_toggle = QCheckBox("Dark mode")
        self.theme_toggle.setObjectName("themeToggle")
        self.theme_toggle.setChecked(self.theme_mode == "dark")
        self.theme_toggle.toggled.connect(self.toggle_theme)
        self.nav_layout.addWidget(self.theme_toggle)

        seed_label = QLabel("Random seed")
        seed_label.setObjectName("navLabel")
        current_seed = self.book.random_seed
        if current_seed is None and hasattr(self, "seed_box"):
            previous_value = self.seed_box.value()
            current_seed = previous_value if previous_value else None
        self.seed_box = QSpinBox()
        self.seed_box.setRange(0, 999999)
        self.seed_box.setSpecialValueText("Random")
        self.seed_box.setToolTip("Leave as Random for a fresh draw each time. Set a number only when you deliberately want repeatable test draws.")
        self.seed_box.setValue(int(current_seed) if current_seed is not None else 0)
        self.seed_box.valueChanged.connect(lambda value: self.book.set_seed(value if value else None))
        self.book.set_seed(self.seed_box.value() if self.seed_box.value() else None)
        self.nav_layout.addWidget(seed_label)
        self.nav_layout.addWidget(self.seed_box)
        self.stack.setCurrentIndex(0)
        self.apply_theme()

    def refresh_all_views(self) -> None:
        if hasattr(self, "dashboard_page"):
            self.dashboard_page.refresh()
        if hasattr(self, "web_display_page"):
            self.web_display_page.refresh()
        for page in self.race_pages.values():
            page.refresh()
        self.money_page.refresh()
        self.attendees_page.refresh()
        self.cup_page.refresh()
        if hasattr(self, "payout_settings_page"):
            self.payout_settings_page.refresh()
        if hasattr(self, "audit_page"):
            self.audit_page.refresh()

    def generate_all_normal_sweeps(self) -> None:
        try:
            self.book.generate_all_normal_races()
            self.refresh_all_views()
            QMessageBox.information(self, "Sweeps generated", "Normal race sweeps have been generated.")
        except Exception as error:
            QMessageBox.critical(self, "Could not generate sweeps", str(error))

    def load_workbook_dialog(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Load race workbook", "", "Excel Files (*.xlsx)")
        if not file_name:
            return
        try:
            self.book = load_workbook_data(file_name)
            self.current_event_path = None
            self.current_cup_reveal = None
            self.current_cup_spin_event = None
            self._cup_spin_serial = 0
            self.cup_drawn_reveals = {}
            self.book.set_seed(self.seed_box.value() if hasattr(self, "seed_box") and self.seed_box.value() else None)
            self.race_pages = {}
            self.build_pages()
            QMessageBox.information(self, "Workbook loaded", f"Loaded {Path(file_name).name}")
        except Exception as error:
            QMessageBox.critical(self, "Could not load workbook", str(error))

    def toggle_theme(self, checked: bool) -> None:
        self.theme_mode = "dark" if checked else "light"
        self.apply_theme()

    def apply_theme(self) -> None:
        if self.theme_mode == "dark":
            colors = {
                "window_bg": "#0b1020",
                "panel_bg": "#111827",
                "panel_line": "#2dd4bf",
                "card_bg": "#151c2f",
                "card_alt": "#1f2937",
                "text": "#f8fafc",
                "muted": "#b6c2d1",
                "border": "#334155",
                "button_bg": "#1f2937",
                "button_hover": "#334155",
                "button_text": "#f8fafc",
                "primary_bg": "#0f766e",
                "primary_hover": "#0d9488",
                "header_bg": "#243044",
                "input_bg": "#111827",
                "menu_bg": "#0b1020",
            }
        else:
            colors = {
                "window_bg": "#eef2f7",
                "panel_bg": "#182235",
                "panel_line": "#14b8a6",
                "card_bg": "#ffffff",
                "card_alt": "#f7fafc",
                "text": "#101828",
                "muted": "#475467",
                "border": "#d0d5dd",
                "button_bg": "#ffffff",
                "button_hover": "#e0f2fe",
                "button_text": "#101828",
                "primary_bg": "#0f766e",
                "primary_hover": "#0d9488",
                "header_bg": "#e5e7eb",
                "input_bg": "#ffffff",
                "menu_bg": "#eef2f7",
            }

        self.setStyleSheet(
            f"""
            QMainWindow {{ background: {colors['window_bg']}; color: {colors['text']}; }}
            QWidget {{ background: {colors['window_bg']}; color: {colors['text']}; font-family: Segoe UI, Arial; }}
            QMenuBar {{ background: {colors['menu_bg']}; color: {colors['text']}; padding: 3px; }}
            QMenuBar::item {{ padding: 6px 10px; border-radius: 6px; }}
            QMenuBar::item:selected {{ background: {colors['button_hover']}; }}
            QMenu {{ background: {colors['card_bg']}; color: {colors['text']}; border: 1px solid {colors['border']}; }}
            QMenu::item {{ padding: 7px 22px; }}
            QMenu::item:selected {{ background: {colors['button_hover']}; }}

            #leftPanel {{
                background: {colors['panel_bg']};
                border-right: 4px solid {colors['panel_line']};
            }}
            #leftPanel QLabel {{ background: transparent; color: #ffffff; }}
            #leftPanel QPushButton {{
                background: rgba(255, 255, 255, 0.08);
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 12px;
                text-align: left;
                padding-left: 16px;
                font-weight: 650;
            }}
            #leftPanel QPushButton:hover {{ background: rgba(45, 212, 191, 0.18); border-color: {colors['panel_line']}; }}
            #leftPanel QPushButton:pressed {{ background: rgba(45, 212, 191, 0.28); }}
            #leftPanel QPushButton#dangerButton {{ background: rgba(185, 28, 28, 0.84); color: #ffffff; border: 1px solid #ef4444; }}
            #leftPanel QPushButton#dangerButton:hover {{ background: #991b1b; }}
            #navTitle {{ color: #ffffff; font-size: 25px; font-weight: 900; padding: 8px 4px 20px 4px; letter-spacing: 0.5px; }}
            #navLabel {{ color: #cbd5e1; font-size: 13px; font-weight: 750; padding-top: 8px; }}
            #themeToggle {{ background: transparent; color: #ffffff; font-size: 14px; padding: 6px 2px; }}

            QPushButton {{
                font-size: 14px;
                padding: 9px 14px;
                border-radius: 10px;
                background: {colors['button_bg']};
                color: {colors['button_text']};
                border: 1px solid {colors['border']};
                font-weight: 600;
            }}
            QPushButton:hover {{ background: {colors['button_hover']}; }}
            QPushButton:pressed {{ background: {colors['header_bg']}; }}
            QPushButton:disabled {{ color: {colors['muted']}; background: {colors['card_alt']}; }}
            QPushButton#primaryButton {{ background: {colors['primary_bg']}; color: #ffffff; border: 1px solid {colors['primary_hover']}; }}
            QPushButton#primaryButton:hover {{ background: {colors['primary_hover']}; }}
            QPushButton#dangerButton {{ background: #7f1d1d; color: #ffffff; border: 1px solid #991b1b; }}
            QPushButton#dangerButton:hover {{ background: #991b1b; }}

            QLabel {{ color: {colors['text']}; background: transparent; }}
            QLabel#pageTitle {{ color: {colors['text']}; font-size: 28px; font-weight: 900; padding-bottom: 2px; }}
            QLabel#sectionTitle {{ color: {colors['text']}; font-size: 17px; font-weight: 800; padding-top: 8px; }}
            QLabel#hint {{ color: {colors['muted']}; font-size: 13px; }}
            QLabel#statTitle {{ color: {colors['muted']}; font-size: 12px; font-weight: 800; letter-spacing: 1px; }}
            QLabel#statValue {{ color: {colors['text']}; font-size: 28px; font-weight: 900; }}
            QLabel#spinnerTitle {{ color: {colors['muted']}; font-size: 14px; font-weight: 900; letter-spacing: 1.5px; }}
            QLabel#spinnerLabel {{
                color: {colors['text']};
                background: {colors['card_alt']};
                border: 2px solid {colors['panel_line']};
                border-radius: 18px;
                padding: 26px;
                font-size: 34px;
                font-weight: 950;
            }}

            QLineEdit, QSpinBox, QComboBox, QTextEdit {{
                background: {colors['input_bg']};
                color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 9px;
                padding: 7px;
                selection-background-color: {colors['button_hover']};
            }}
            QComboBox QAbstractItemView {{
                background: {colors['card_bg']};
                color: {colors['text']};
                selection-background-color: {colors['button_hover']};
            }}

            QFrame#card, QFrame#statCard {{
                background: {colors['card_bg']};
                border: 1px solid {colors['border']};
                border-radius: 16px;
            }}
            QFrame#statCard {{ border-left: 5px solid {colors['panel_line']}; }}
            QTableWidget {{
                background: {colors['card_bg']};
                color: {colors['text']};
                alternate-background-color: {colors['card_alt']};
                gridline-color: {colors['border']};
                font-size: 13px;
                selection-background-color: rgba(20, 184, 166, 0.24);
                selection-color: {colors['text']};
                border: 1px solid {colors['border']};
                border-radius: 12px;
            }}
            QTableCornerButton::section {{ background: {colors['header_bg']}; border: 1px solid {colors['border']}; }}
            QHeaderView::section {{
                background: {colors['header_bg']};
                color: {colors['text']};
                padding: 8px;
                border: 0;
                border-right: 1px solid {colors['border']};
                border-bottom: 1px solid {colors['border']};
                font-weight: 800;
            }}
            QTabWidget::pane {{ border: 1px solid {colors['border']}; background: {colors['card_bg']}; border-radius: 12px; }}
            QTabBar::tab {{
                background: {colors['header_bg']};
                color: {colors['text']};
                padding: 10px 18px;
                border: 1px solid {colors['border']};
                border-bottom: 0;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                margin-right: 3px;
                font-weight: 700;
            }}
            QTabBar::tab:selected {{ background: {colors['card_bg']}; color: {colors['panel_line']}; }}
            QDialog {{ background: {colors['window_bg']}; color: {colors['text']}; }}
            """
        )


class DashboardPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        title = QLabel("Event Dashboard")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)

        hint = QLabel("Quick health-check for the sweep day. Import races and attendees, generate/lock sweeps, then use Web Display for TV or other screens.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        stats = QHBoxLayout()
        self.races_value = QLabel("0")
        self.attendees_value = QLabel("0")
        self.allocations_value = QLabel("0")
        self.owing_value = QLabel("$0.00")
        self.locked_value = QLabel("0")
        stats.addWidget(make_stat_card("RACES IMPORTED", self.races_value))
        stats.addWidget(make_stat_card("ACTIVE ATTENDEES", self.attendees_value))
        stats.addWidget(make_stat_card("ALLOCATIONS", self.allocations_value))
        stats.addWidget(make_stat_card("TOTAL OWING", self.owing_value))
        stats.addWidget(make_stat_card("LOCKED SWEEPS", self.locked_value))
        layout.addLayout(stats)

        self.race_table = QTableWidget()
        self.race_table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("Race status"))
        layout.addWidget(self.race_table, 1)

        self.payout_table = QTableWidget()
        self.payout_table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("Payout balance check"))
        layout.addWidget(self.payout_table, 1)
        self.refresh()

    def refresh(self) -> None:
        total_owing = sum(row["Total Owing"] for row in self.book.amount_owing_rows())
        self.races_value.setText(str(len(self.book.races)))
        self.attendees_value.setText(str(len(self.book.active_attendees)))
        self.allocations_value.setText(str(len(self.book.allocations)))
        self.owing_value.setText(money(total_owing))
        self.locked_value.setText(str(len(self.book.locked_sweeps)))

        headers = ["Race", "Race Name", "Runners", "Sweeps", "Locked", "Results Entered"]
        rows = []
        for race_no in sorted(self.book.races):
            race = self.book.races[race_no]
            labels = []
            for allocation in self.book.allocations:
                if allocation.race_number == race_no and allocation.sweep_label not in labels:
                    labels.append(allocation.sweep_label)
            results_entered = any(h.result_position in {1, 2, 3} for h in race.runners)
            rows.append([
                race_no,
                race.race_name,
                len(race.runners),
                len(labels),
                len(self.book.locked_labels_for_race(race_no)),
                "Yes" if results_entered else "No",
            ])
        self.race_table.setRowCount(len(rows))
        self.race_table.setColumnCount(len(headers))
        self.race_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for col, value in enumerate(row):
                self.race_table.setItem(row_index, col, table_item(value))
        self.race_table.resizeColumnsToContents()

        summary = self.book.payout_summary_rows()
        sum_headers = ["Race", "Sweep", "Collected", "Payout Total", "Difference"]
        self.payout_table.setRowCount(len(summary))
        self.payout_table.setColumnCount(len(sum_headers))
        self.payout_table.setHorizontalHeaderLabels(sum_headers)
        for row_index, row in enumerate(summary):
            values = [row["Race"], row["Sweep"], money(row["Collected"]), money(row["Payout Total"]), money(row["Difference"])]
            for col, value in enumerate(values):
                self.payout_table.setItem(row_index, col, table_item(value))
        self.payout_table.resizeColumnsToContents()


class WebDisplayPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)

        header = QHBoxLayout()
        title = QLabel("Web Display")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        self.start_button = QPushButton("Start web display")
        self.start_button.setObjectName("primaryButton")
        self.stop_button = QPushButton("Stop")
        self.refresh_button = QPushButton("Refresh")
        self.start_button.clicked.connect(self.start_server)
        self.stop_button.clicked.connect(self.stop_server)
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.start_button)
        header.addWidget(self.stop_button)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)

        hint = QLabel("Starts a read-only local web display for other screens on the same network. The admin app must stay open. Windows may ask you to allow Python through the firewall.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)
        self.status_label = QLabel()
        self.status_label.setObjectName("sectionTitle")
        self.local_url_label = QLabel()
        self.network_url_label = QLabel()
        self.tip_label = QLabel("Use /cup3d for the 3D WebGL Cup screen, /cup for the 2D fallback, /race/1 for Race 1, /money for owing, and /payouts for winners.")
        self.tip_label.setObjectName("hint")
        self.local_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.network_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(self.status_label)
        card_layout.addWidget(self.local_url_label)
        card_layout.addWidget(self.network_url_label)
        card_layout.addWidget(self.tip_label)
        layout.addWidget(card)

        link_row = QHBoxLayout()
        open_home = QPushButton("Open home")
        open_cup = QPushButton("Open 3D Cup display")
        copy_network = QPushButton("Copy network URL")
        open_home.clicked.connect(lambda: self.open_url("/"))
        open_cup.clicked.connect(lambda: self.open_url("/cup3d"))
        copy_network.clicked.connect(self.copy_network_url)
        link_row.addWidget(open_home)
        link_row.addWidget(open_cup)
        link_row.addWidget(copy_network)
        link_row.addStretch(1)
        layout.addLayout(link_row)

        self.preview_table = QTableWidget()
        self.preview_table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("Available web pages"))
        layout.addWidget(self.preview_table, 1)
        self.refresh()

    def start_server(self) -> None:
        try:
            if not self.main_window.web_server:
                self.main_window.web_server = LocalWebServer(self.main_window)
            self.main_window.web_server.start()
            self.book.audit("Started web display", self.main_window.web_server.network_url())
            self.refresh()
        except Exception as error:
            QMessageBox.critical(self, "Could not start web display", str(error))

    def stop_server(self) -> None:
        if self.main_window.web_server:
            self.main_window.web_server.stop()
            self.book.audit("Stopped web display", "Local web display stopped")
        self.refresh()

    def open_url(self, path: str) -> None:
        if not self.ensure_started():
            return
        url = self.main_window.web_server.local_url(path)  # type: ignore[union-attr]
        try:
            if sys.platform.startswith("win"):
                os.startfile(url)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
        except Exception as error:
            QMessageBox.warning(self, "Could not open browser", str(error))

    def copy_network_url(self) -> None:
        if not self.ensure_started():
            return
        QApplication.clipboard().setText(self.main_window.web_server.network_url())  # type: ignore[union-attr]
        QMessageBox.information(self, "Copied", "Network display URL copied to clipboard.")

    def ensure_started(self) -> bool:
        if self.main_window.web_server and self.main_window.web_server.is_running:
            return True
        self.start_server()
        return bool(self.main_window.web_server and self.main_window.web_server.is_running)

    def refresh(self) -> None:
        server = self.main_window.web_server
        running = bool(server and server.is_running)
        self.status_label.setText("Status: running" if running else "Status: stopped")
        if running and server:
            self.local_url_label.setText(f"Local screen: {server.local_url('/')}")
            self.network_url_label.setText(f"Network screen: {server.network_url('/')}")
        else:
            self.local_url_label.setText("Local screen: not running")
            self.network_url_label.setText("Network screen: not running")

        rows = [
            ["Home / dashboard", "/"],
            ["Big Cup display", "/cup"],
            ["Money owing", "/money"],
            ["Payout winners", "/payouts"],
            ["Attendees", "/attendees"],
        ]
        for race_no in sorted(self.book.races):
            rows.append([f"Race {race_no}", f"/race/{race_no}"])
        self.preview_table.setRowCount(len(rows))
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["Page", "Path"])
        for row_index, row in enumerate(rows):
            for col, value in enumerate(row):
                self.preview_table.setItem(row_index, col, table_item(value))
        self.preview_table.resizeColumnsToContents()


class ImportDataPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)

        header = QHBoxLayout()
        title = QLabel("Import Data")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        layout.addLayout(header)

        hint = QLabel(
            "Import race CSV files like the Punters export. The app uses Num, Horse Name, Barrier, Weight/Weight Carried, Jockey, Trainer and Finish Result when available. Use Cup Special import for the Melbourne Cup/special sweep race."
        )
        hint.setObjectName("hint")
        layout.addWidget(hint)

        controls = QFrame()
        controls.setObjectName("card")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 14, 14, 14)

        race_form = QFormLayout()
        self.race_number_box = QSpinBox()
        self.race_number_box.setRange(0, 99)
        self.race_number_box.setSpecialValueText("Auto from filename")
        self.race_name_edit = QLineEdit()
        self.race_name_edit.setPlaceholderText("Optional. Leave blank to infer from CSV/form-guide URL.")
        race_form.addRow("Race number override", self.race_number_box)
        race_form.addRow("Race name override", self.race_name_edit)
        controls_layout.addLayout(race_form)

        button_row = QHBoxLayout()
        import_race = QPushButton("Import Race CSV(s)")
        import_race.setObjectName("primaryButton")
        import_cup_race = QPushButton("Import Cup Special Race CSV")
        import_cup_race.setObjectName("primaryButton")
        import_attendees = QPushButton("Import Attendees")
        import_race.clicked.connect(self.import_race_csvs)
        import_cup_race.clicked.connect(self.import_cup_race_csv)
        import_attendees.clicked.connect(self.import_attendees_file)
        button_row.addWidget(import_race)
        button_row.addWidget(import_cup_race)
        button_row.addWidget(import_attendees)
        button_row.addStretch(1)
        controls_layout.addLayout(button_row)
        layout.addWidget(controls)

        self.status_label = QLabel("No import run yet.")
        self.status_label.setObjectName("hint")
        layout.addWidget(self.status_label)

        self.race_table = QTableWidget()
        self.race_table.setAlternatingRowColors(True)
        self.race_table.setMinimumHeight(180)
        layout.addWidget(QLabel("Race import summary"))
        layout.addWidget(self.race_table, 1)
        self.set_race_summary(self.current_race_summary())

        self.attendee_table = QTableWidget()
        self.attendee_table.setAlternatingRowColors(True)
        self.attendee_table.setMinimumHeight(180)
        layout.addWidget(QLabel("Attendee preview"))
        layout.addWidget(self.attendee_table, 1)
        self.set_attendee_summary(self.current_attendee_summary())

    def import_race_csvs(self) -> None:
        file_names, _ = QFileDialog.getOpenFileNames(self, "Import race CSV files", "", "CSV Files (*.csv)")
        if not file_names:
            return
        summaries: List[List[object]] = []
        errors: List[str] = []
        single_file = len(file_names) == 1
        for file_name in file_names:
            try:
                override_number = self.race_number_box.value() if single_file and self.race_number_box.value() > 0 else None
                override_name = self.race_name_edit.text().strip() if single_file else ""
                race = load_race_csv(file_name, race_number=override_number, race_name=override_name)
                self.book.replace_race(race)
                summaries.append([race.race_number, race.race_name, len(race.runners), Path(file_name).name, "Imported"])
            except Exception as error:
                guessed = parse_race_number_from_path(file_name) or "?"
                summaries.append([guessed, "", 0, Path(file_name).name, f"FAILED: {error}"])
                errors.append(f"{Path(file_name).name}: {error}")
        status = f"Imported {len(summaries) - len(errors)} race CSV file(s)."
        if errors:
            status += " Some files failed."
        self.main_window.rebuild_after_data_import(status=status, race_summary=summaries)
        if errors:
            QMessageBox.warning(self.main_window, "Import finished with errors", "\n".join(errors[:8]))

    def import_cup_race_csv(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Import Cup Special race CSV", "", "CSV Files (*.csv)")
        if not file_name:
            return
        try:
            override_name = self.race_name_edit.text().strip()
            race = load_race_csv(file_name, race_number=7, race_name=override_name)
            self.book.replace_race(race)
            summary = [[race.race_number, race.race_name, len(race.runners), Path(file_name).name, "Imported as Cup Special"]]
            self.main_window.rebuild_after_data_import(
                status=f"Imported Cup Special race as Race 7 from {Path(file_name).name}. Existing Cup allocations were cleared.",
                race_summary=summary,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could not import Cup Special race", str(error))

    def import_attendees_file(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Import attendees",
            "",
            "Attendee Files (*.csv *.txt *.xlsx *.xlsm);;CSV Files (*.csv);;Text Files (*.txt);;Excel Files (*.xlsx *.xlsm)",
        )
        if not file_name:
            return
        try:
            attendees = load_attendees_file(file_name)
            self.book.replace_attendees(attendees)
            summary = [[a.attendee_id, a.name, bool_text(a.active), bool_text(a.cup_eligible), bool_text(a.paid)] for a in attendees]
            self.main_window.rebuild_after_data_import(
                status=f"Imported {len(attendees)} attendees from {Path(file_name).name}. Existing draw allocations were cleared.",
                attendee_summary=summary,
            )
        except Exception as error:
            QMessageBox.critical(self, "Could not import attendees", str(error))

    def current_race_summary(self) -> List[List[object]]:
        rows: List[List[object]] = []
        for race_no in sorted(self.book.races):
            race = self.book.races[race_no]
            rows.append([race.race_number, race.race_name, len(race.runners), "Current data", "Loaded"])
        return rows

    def current_attendee_summary(self) -> List[List[object]]:
        return [[a.attendee_id, a.name, bool_text(a.active), bool_text(a.cup_eligible), bool_text(a.paid)] for a in self.book.attendees]

    def set_race_summary(self, rows: List[List[object]]) -> None:
        headers = ["Race", "Race Name", "Runners", "Source", "Status"]
        self.race_table.setRowCount(len(rows))
        self.race_table.setColumnCount(len(headers))
        self.race_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for col, value in enumerate(row):
                self.race_table.setItem(row_index, col, table_item(value))
        self.race_table.resizeColumnsToContents()
        self.race_table.setSortingEnabled(True)

    def set_attendee_summary(self, rows: List[List[object]]) -> None:
        headers = ["ID", "Name", "Active", "Cup Eligible", "Paid"]
        self.attendee_table.setRowCount(len(rows))
        self.attendee_table.setColumnCount(len(headers))
        self.attendee_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for col, value in enumerate(row):
                self.attendee_table.setItem(row_index, col, table_item(value))
        self.attendee_table.resizeColumnsToContents()
        self.attendee_table.setSortingEnabled(True)


class RacePage(QWidget):
    def __init__(self, book: SweepBook, race_no: int, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.race_no = race_no
        self.main_window = main_window
        self.tabs = QTabWidget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        self.title_label = QLabel()
        self.title_label.setObjectName("pageTitle")
        header.addWidget(self.title_label, 1)
        self.generate_button = QPushButton("Use Cup generator" if race_no == 7 else "Generate sweeps")
        self.generate_button.setObjectName("primaryButton")
        self.results_button = QPushButton("Enter winning horse numbers")
        self.payout_button = QPushButton("Payout")
        self.lock_button = QPushButton("Lock current sweep")
        self.unlock_button = QPushButton("Unlock current sweep")
        self.export_button = QPushButton("Export / Print")
        self.generate_button.clicked.connect(self.generate)
        self.results_button.clicked.connect(self.enter_results)
        self.payout_button.clicked.connect(self.show_payouts)
        self.lock_button.clicked.connect(self.lock_current_sweep)
        self.unlock_button.clicked.connect(self.unlock_current_sweep)
        self.export_button.clicked.connect(lambda: self.main_window.export_print_sheets_dialog())
        header.addWidget(self.generate_button)
        header.addWidget(self.results_button)
        header.addWidget(self.payout_button)
        header.addWidget(self.lock_button)
        header.addWidget(self.unlock_button)
        header.addWidget(self.export_button)
        layout.addLayout(header)
        self.hint_label = QLabel()
        self.hint_label.setObjectName("hint")
        layout.addWidget(self.hint_label)
        stats = QHBoxLayout()
        self.runners_value = QLabel("0")
        self.attendees_value = QLabel("0")
        self.sweeps_value = QLabel("0")
        self.locked_value = QLabel("0")
        stats.addWidget(make_stat_card("RUNNERS", self.runners_value))
        stats.addWidget(make_stat_card("ACTIVE ATTENDEES", self.attendees_value))
        stats.addWidget(make_stat_card("SWEEP TABS", self.sweeps_value))
        stats.addWidget(make_stat_card("LOCKED", self.locked_value))
        layout.addLayout(stats)
        layout.addWidget(self.tabs, 1)
        self.refresh()

    def refresh(self) -> None:
        race = self.book.races[self.race_no]
        self.title_label.setText(f"Race {race.race_number}: {race.race_name}")
        race_allocations = [a for a in self.book.allocations if a.race_number == self.race_no]
        labels_for_stats = []
        for allocation in race_allocations:
            if allocation.sweep_label not in labels_for_stats:
                labels_for_stats.append(allocation.sweep_label)
        self.hint_label.setText(f"{len(race.runners)} runners | {len(self.book.active_attendees)} active attendees")
        self.runners_value.setText(str(len(race.runners)))
        self.attendees_value.setText(str(len(self.book.active_attendees)))
        self.sweeps_value.setText(str(len(labels_for_stats)))
        self.locked_value.setText(str(len(self.book.locked_labels_for_race(self.race_no))))
        self.tabs.clear()
        if not race_allocations:
            self.tabs.addTab(empty_message_table("No sweeps generated yet."), "No sweep")
            return
        labels = []
        for allocation in race_allocations:
            if allocation.sweep_label not in labels:
                labels.append(allocation.sweep_label)
        for label in labels:
            tab_allocations = [a for a in race_allocations if a.sweep_label == label]
            tab_label = f"{label} 🔒" if self.book.is_sweep_locked(self.race_no, label) else label
            self.tabs.addTab(allocation_table(tab_allocations), tab_label)

    def generate(self) -> None:
        if self.race_no == 7:
            self.main_window.stack.setCurrentWidget(self.main_window.cup_page)
            return
        try:
            self.book.generate_normal_race(self.race_no)
            self.main_window.refresh_all_views()
        except Exception as error:
            QMessageBox.critical(self, "Draw failed", str(error))

    def current_sweep_label(self) -> Optional[str]:
        race_allocations = [a for a in self.book.allocations if a.race_number == self.race_no]
        labels: List[str] = []
        for allocation in race_allocations:
            if allocation.sweep_label not in labels:
                labels.append(allocation.sweep_label)
        index = self.tabs.currentIndex()
        if 0 <= index < len(labels):
            return labels[index]
        return None

    def lock_current_sweep(self) -> None:
        label = self.current_sweep_label()
        if not label:
            QMessageBox.information(self, "No sweep", "Generate a sweep before locking it.")
            return
        self.book.lock_sweep(self.race_no, label)
        self.main_window.refresh_all_views()
        QMessageBox.information(self, "Sweep locked", f"Race {self.race_no} - {label} is now locked.")

    def unlock_current_sweep(self) -> None:
        label = self.current_sweep_label()
        if not label:
            QMessageBox.information(self, "No sweep", "There is no sweep selected.")
            return
        answer = QMessageBox.question(
            self,
            "Unlock sweep",
            f"Unlock Race {self.race_no} - {label}? This will allow it to be regenerated.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.book.unlock_sweep(self.race_no, label)
        self.main_window.refresh_all_views()

    def enter_results(self) -> None:
        race = self.book.races[self.race_no]
        dialog = ResultsDialog(race.runners, self)
        if dialog.exec() == QDialog.Accepted:
            first, second, third = dialog.values()
            race.set_results(first, second, third)
            self.book.audit("Entered results", f"Race {self.race_no}: 1st #{first}, 2nd #{second}, 3rd #{third}")
            for allocation in self.book.allocations:
                if allocation.race_number == self.race_no:
                    horse = next((h for h in race.runners if h.horse_number == allocation.horse_number), None)
                    allocation.result_position = horse.result_position if horse else None
            self.main_window.refresh_all_views()

    def show_payouts(self) -> None:
        rows = self.book.payout_rows(self.race_no)
        dialog = TableDialog(f"Race {self.race_no} payouts", payout_table(rows), self)
        dialog.exec()


class CupSweepPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        self.draw_order: List[Allocation] = []
        self.draw_index = 0
        self.animating = False
        self.animation_tick = 0
        self.target_allocation: Optional[Allocation] = None

        import random
        self.spinner_rng = random.Random()
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(65)
        self.anim_timer.timeout.connect(self.animation_step)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        title = QLabel("Cup Sweep Generator")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)

        self.sweep_select = QComboBox()
        for label, _price in CUP_SWEEPS:
            self.sweep_select.addItem(label)
        self.sweep_select.currentTextChanged.connect(self.reset_draw_display)

        generate_selected = QPushButton("Generate selected sweep")
        draw = QPushButton("Draw next")
        reset = QPushButton("Reset selected draw")
        lock = QPushButton("Lock selected")
        unlock = QPushButton("Unlock selected")
        generate_selected.clicked.connect(self.generate_selected_cup_sweep)
        draw.clicked.connect(self.draw_next)
        reset.clicked.connect(self.reset_selected_draw)
        lock.clicked.connect(self.lock_selected_sweep)
        unlock.clicked.connect(self.unlock_selected_sweep)
        header.addWidget(self.sweep_select)
        header.addWidget(generate_selected)
        header.addWidget(draw)
        header.addWidget(reset)
        header.addWidget(lock)
        header.addWidget(unlock)
        layout.addLayout(header)

        hint = QLabel("Draw one Cup sweep at a time. Pick $1, $2 or $5, generate that sweep, then reveal each horse with the spinner draw.")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        voice_card = QFrame()
        voice_card.setObjectName("card")
        voice_layout = QVBoxLayout(voice_card)
        voice_layout.setContentsMargins(14, 10, 14, 10)
        voice_layout.setSpacing(8)

        voice_controls = QHBoxLayout()
        self.announce_checkbox = QCheckBox("Announce draw")
        self.announce_checkbox.setChecked(True)
        self.voice_engine_select = QComboBox()
        self.voice_engine_select.addItems(["OpenAI live", "Windows/offline fallback"])
        self.voice_select = QComboBox()
        self.voice_select.addItems(["marin", "cedar", "coral", "alloy", "ash", "ballad", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse"])
        self.delivery_select = QComboBox()
        self.delivery_select.addItems(["Plain", "Race caller", "Big reveal"])
        self.delivery_select.setCurrentText("Race caller")
        test_voice = QPushButton("Test voice")
        test_voice.clicked.connect(self.test_voice)
        open_tts_folder = QPushButton("Open audio folder")
        open_tts_folder.clicked.connect(self.open_tts_folder)
        precache_voice = QPushButton("Pre-cache web stop voices")
        precache_voice.clicked.connect(self.precache_selected_stop_voices)
        self.tts_status = QLabel(tts_runtime_status_text())
        self.tts_status.setObjectName("hint")
        voice_controls.addWidget(self.announce_checkbox)
        voice_controls.addWidget(QLabel("Voice engine"))
        voice_controls.addWidget(self.voice_engine_select)
        voice_controls.addWidget(QLabel("OpenAI voice"))
        voice_controls.addWidget(self.voice_select)
        voice_controls.addWidget(QLabel("Delivery"))
        voice_controls.addWidget(self.delivery_select)
        voice_controls.addWidget(test_voice)
        voice_controls.addWidget(open_tts_folder)
        voice_controls.addWidget(precache_voice)
        voice_controls.addWidget(self.tts_status, 1)
        voice_layout.addLayout(voice_controls)

        prompt_controls = QHBoxLayout()
        prompt_label = QLabel("Voice prompt")
        prompt_label.setObjectName("hint")
        self.voice_prompt = QTextEdit()
        self.voice_prompt.setPlainText(cup_voice_instructions())
        self.voice_prompt.setFixedHeight(76)
        self.voice_prompt.setPlaceholderText("Describe the delivery style for the OpenAI race-caller voice.")
        reset_prompt = QPushButton("Reset prompt")
        reset_prompt.clicked.connect(lambda: self.voice_prompt.setPlainText(cup_voice_instructions()))
        prompt_controls.addWidget(prompt_label)
        prompt_controls.addWidget(self.voice_prompt, 1)
        prompt_controls.addWidget(reset_prompt)
        voice_layout.addLayout(prompt_controls)
        prompt_note = QLabel("Note: the prompt changes delivery, not the base voice identity. For a stronger effect, use Delivery = Race caller or Big reveal; the app then rewrites the spoken announcement with pauses and race-day wording.")
        prompt_note.setObjectName("hint")
        voice_layout.addWidget(prompt_note)
        layout.addWidget(voice_card)

        self.tts_status_timer = QTimer(self)
        self.tts_status_timer.setInterval(500)
        self.tts_status_timer.timeout.connect(self.refresh_tts_status)
        self.tts_status_timer.start()

        spinner_frame = QFrame()
        spinner_frame.setObjectName("card")
        spinner_layout = QHBoxLayout(spinner_frame)
        spinner_layout.setContentsMargins(18, 16, 18, 16)

        horse_box = QVBoxLayout()
        horse_title = QLabel("HORSE")
        horse_title.setObjectName("spinnerTitle")
        self.horse_spinner = QLabel("---")
        self.horse_spinner.setObjectName("spinnerLabel")
        horse_box.addWidget(horse_title)
        horse_box.addWidget(self.horse_spinner)

        attendee_box = QVBoxLayout()
        attendee_title = QLabel("ATTENDEE")
        attendee_title.setObjectName("spinnerTitle")
        self.attendee_spinner = QLabel("---")
        self.attendee_spinner.setObjectName("spinnerLabel")
        attendee_box.addWidget(attendee_title)
        attendee_box.addWidget(self.attendee_spinner)

        spinner_layout.addLayout(horse_box, 1)
        spinner_layout.addLayout(attendee_box, 1)
        layout.addWidget(spinner_frame)

        self.reveal_label = QLabel("Generate a selected Cup sweep, then press Draw next.")
        self.reveal_label.setObjectName("pageTitle")
        layout.addWidget(self.reveal_label)

        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.refresh()

    def selected_sweep(self) -> str:
        return self.sweep_select.currentText()

    def generate_selected_cup_sweep(self) -> None:
        try:
            label = self.selected_sweep()
            self.book.generate_cup_sweep(label)
            self.main_window.cup_drawn_reveals[label] = []
            self.main_window.current_cup_reveal = None
            self.main_window.current_cup_spin_event = None
            self.reset_draw_display()
            self.main_window.refresh_all_views()
            self.main_window.stack.setCurrentWidget(self)
            self.reveal_label.setText(f"{label} generated. Ready to draw.")
        except Exception as error:
            QMessageBox.critical(self, "Cup draw failed", str(error))

    def reset_draw_display(self) -> None:
        self.anim_timer.stop()
        self.animating = False
        selected = self.selected_sweep()
        self.draw_order = [a for a in self.book.allocations if a.race_number == 7 and a.sweep_label == selected]
        revealed = list(self.main_window.cup_drawn_reveals.get(selected, []))

        # Generated Cup allocations are the hidden draw order. Only revealed rows
        # are shown in the table/web display.
        valid_horse_numbers = {str(a.horse_number) for a in self.draw_order}
        revealed = [r for r in revealed if str(r.get("horse_number")) in valid_horse_numbers]
        self.main_window.cup_drawn_reveals[selected] = revealed
        self.draw_index = len(revealed)
        self.target_allocation = None
        self.horse_spinner.setText("---")
        self.attendee_spinner.setText("---")

        if revealed:
            last = revealed[-1]
            self.horse_spinner.setText(f"#{last.get('horse_number', '---')} {last.get('horse_name', '')}".strip())
            self.attendee_spinner.setText(str(last.get("attendee_name", "---")))
            self.main_window.current_cup_reveal = last
        else:
            self.main_window.current_cup_reveal = None

        locked_note = " Locked." if self.book.is_sweep_locked(7, selected) else ""
        if self.draw_order:
            self.reveal_label.setText(f"{selected}: {self.draw_index} of {len(self.draw_order)} drawn." + locked_note)
        else:
            self.reveal_label.setText(f"No allocations generated yet for {selected}.")

        self.table.setRowCount(0)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Draw", "Horse No", "Horse", "Attendee", "Type"])
        for reveal in revealed:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = [row + 1, reveal.get("horse_number", ""), reveal.get("horse_name", ""), reveal.get("attendee_name", ""), reveal.get("allocation_type", "")]
            for col, value in enumerate(values):
                self.table.setItem(row, col, table_item(value))
        self.table.resizeColumnsToContents()

        if hasattr(self.main_window, "web_display_page"):
            self.main_window.web_display_page.refresh()

    def reset_selected_draw(self) -> None:
        selected = self.selected_sweep()
        answer = QMessageBox.question(
            self,
            "Reset selected draw",
            f"Reset the visible draw progress for {selected}? The generated allocation order stays hidden and can be drawn again from the start.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.main_window.cup_drawn_reveals[selected] = []
        self.main_window.current_cup_reveal = None
        self.main_window.current_cup_spin_event = None
        self.book.audit("Reset Cup draw progress", selected)
        self.reset_draw_display()

    def draw_next(self) -> None:
        if self.animating:
            return
        if not self.draw_order:
            self.reset_draw_display()
            if not self.draw_order:
                QMessageBox.information(self, "No Cup sweep", f"Generate {self.selected_sweep()} before drawing.")
                return
        if self.draw_index >= len(self.draw_order):
            self.reveal_label.setText(f"{self.selected_sweep()} draw complete.")
            return

        self.target_allocation = self.draw_order[self.draw_index]
        self.main_window._cup_spin_serial += 1
        spin_id = f"{self.target_allocation.sweep_label}-{self.draw_index + 1}-{datetime.now().strftime('%Y%m%d%H%M%S%f')}-{self.main_window._cup_spin_serial}"
        self.main_window.current_cup_spin_event = {
            "spin_id": spin_id,
            "status": "spinning",
            "sweep_label": self.target_allocation.sweep_label,
            "draw_index": self.draw_index + 1,
            "draw_total": len(self.draw_order),
            "started_at": datetime.now().isoformat(timespec="milliseconds"),
            "horse_duration_ms": 3200,
            "attendee_duration_ms": 5200,
            "voice_engine": self.selected_voice_engine(),
            "voice": self.voice_select.currentText(),
            "voice_instructions": self.current_voice_instructions(),
            "delivery_mode": self.selected_delivery_mode(),
            "announce_enabled": self.announce_checkbox.isChecked(),
            "target": {
                "race_number": self.target_allocation.race_number,
                "sweep_label": self.target_allocation.sweep_label,
                "horse_number": self.target_allocation.horse_number,
                "horse_name": self.target_allocation.horse_name,
                "attendee_name": self.target_allocation.attendee_name,
                "allocation_type": self.target_allocation.allocation_type,
                "odds": getattr(self.target_allocation, "odds", ""),
                "flair": cup_flair_for_allocation(self.book, self.target_allocation),
            },
            "horse_options": [
                {"horse_number": a.horse_number, "horse_name": a.horse_name, "odds": getattr(a, "odds", "")}
                for a in self.draw_order
            ],
            "attendee_options": sorted({a.attendee_name for a in self.draw_order}),
        }
        self.animation_tick = 0
        self.animating = True
        self.reveal_label.setText("Spinning...")
        self.anim_timer.start()

    def animation_step(self) -> None:
        if not self.target_allocation:
            self.anim_timer.stop()
            self.animating = False
            return

        self.animation_tick += 1
        horse_options = [f"#{a.horse_number} {a.horse_name}" for a in self.draw_order] or ["---"]
        attendee_options = [a.attendee_name for a in self.draw_order] or ["---"]

        if self.animation_tick < 18:
            self.horse_spinner.setText(self.spinner_rng.choice(horse_options))
        else:
            self.horse_spinner.setText(f"#{self.target_allocation.horse_number} {self.target_allocation.horse_name}")

        if self.animation_tick < 34:
            self.attendee_spinner.setText(self.spinner_rng.choice(attendee_options))
        else:
            self.attendee_spinner.setText(self.target_allocation.attendee_name)
            self.anim_timer.stop()
            self.animating = False
            self.finish_reveal(self.target_allocation)

    def finish_reveal(self, allocation: Allocation) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [row + 1, allocation.horse_number, allocation.horse_name, allocation.attendee_name, allocation.allocation_type]
        for col, value in enumerate(values):
            self.table.setItem(row, col, table_item(value))
        self.table.resizeColumnsToContents()
        self.draw_index += 1
        self.reveal_label.setText(f"{allocation.attendee_name} has drawn #{allocation.horse_number} {allocation.horse_name}")
        current_spin = self.main_window.current_cup_spin_event if isinstance(self.main_window.current_cup_spin_event, dict) else {}
        reveal = {
            "spin_id": current_spin.get("spin_id", ""),
            "sweep_label": allocation.sweep_label,
            "attendee_name": allocation.attendee_name,
            "horse_number": allocation.horse_number,
            "horse_name": allocation.horse_name,
            "allocation_type": allocation.allocation_type,
            "draw_index": self.draw_index,
            "draw_total": len(self.draw_order),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "odds": getattr(allocation, "odds", ""),
            "flair": cup_flair_for_allocation(self.book, allocation),
        }
        if isinstance(self.main_window.current_cup_spin_event, dict):
            self.main_window.current_cup_spin_event["status"] = "revealed"
            self.main_window.current_cup_spin_event["revealed_at"] = datetime.now().isoformat(timespec="milliseconds")
        self.main_window.current_cup_reveal = reveal
        revealed = self.main_window.cup_drawn_reveals.setdefault(str(allocation.sweep_label), [])
        if len(revealed) < self.draw_index:
            revealed.append(reveal)
        else:
            revealed[:] = revealed[: self.draw_index - 1] + [reveal]
        if hasattr(self.main_window, "web_display_page"):
            self.main_window.web_display_page.refresh()
        self.announce_allocation(allocation)

    def test_voice(self) -> None:
        engine = self.selected_voice_engine()
        set_tts_runtime_status("OpenAI TTS test requested." if engine == "openai" else "Windows/offline test requested.")
        self.refresh_tts_status()
        sample = self.format_announcement_text(
            sweep_label="Cup five dollar sweep",
            attendee_name="Brad",
            horse_number=7,
            horse_name="Half Yours",
            is_complete=False,
        )
        announce_text(
            sample,
            engine=engine,
            voice=self.voice_select.currentText(),
            instructions=self.current_voice_instructions(),
        )

    def refresh_tts_status(self) -> None:
        self.tts_status.setText(tts_runtime_status_text())

    def open_tts_folder(self) -> None:
        folder = ensure_tts_cache_dir()
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(folder))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as error:
            QMessageBox.warning(self, "Audio folder", f"Could not open the audio folder:\n{error}")

    def selected_voice_engine(self) -> str:
        return "openai" if self.voice_engine_select.currentText().startswith("OpenAI") else "windows"

    def selected_delivery_mode(self) -> str:
        return self.delivery_select.currentText() if hasattr(self, "delivery_select") else "Race caller"

    def current_voice_instructions(self) -> str:
        text = self.voice_prompt.toPlainText().strip() if hasattr(self, "voice_prompt") else ""
        return text or cup_voice_instructions()

    def format_announcement_text(
        self,
        *,
        sweep_label: str,
        attendee_name: str,
        horse_number: int | str,
        horse_name: str,
        is_complete: bool,
    ) -> str:
        mode = self.selected_delivery_mode()
        attendee_name = str(attendee_name).title()
        horse_name = str(horse_name).strip()

        if mode == "Plain":
            phrase = f"{sweep_label}. {attendee_name} has drawn horse number {horse_number}, {horse_name}."
        elif mode == "Big reveal":
            phrase = (
                f"Here we go for the {sweep_label}! "
                f"The horse is locked in... horse number {horse_number}. "
                f"And the runner is... {horse_name}! "
                f"Now for the ticket holder... wait for it... {attendee_name}! "
                f"{attendee_name}, you are riding with {horse_name}!"
            )
        else:
            phrase = (
                f"Next up, the {sweep_label}. "
                f"The draw is in... horse number {horse_number}... {horse_name}. "
                f"And that goes to... {attendee_name}! "
                f"{attendee_name} has drawn {horse_name}."
            )

        if is_complete:
            phrase += f" That completes the {sweep_label} draw."
        return phrase

    def announce_allocation(self, allocation: Allocation) -> None:
        if not self.announce_checkbox.isChecked():
            return
        sweep_label = spoken_sweep_label(self.selected_sweep())
        phrase = self.format_announcement_text(
            sweep_label=sweep_label,
            attendee_name=str(allocation.attendee_name),
            horse_number=allocation.horse_number,
            horse_name=str(allocation.horse_name),
            is_complete=self.draw_index >= len(self.draw_order),
        )
        engine = self.selected_voice_engine()
        set_tts_runtime_status("OpenAI TTS announcement requested." if engine == "openai" else "Windows/offline announcement requested.")
        self.refresh_tts_status()
        announce_text(phrase, engine=engine, voice=self.voice_select.currentText(), instructions=self.current_voice_instructions())

    def precache_selected_stop_voices(self) -> None:
        if self.selected_voice_engine() != "openai":
            QMessageBox.information(self, "Pre-cache web stop voices", "Select OpenAI live before pre-caching web reel-stop voice clips.")
            return
        label = self.selected_sweep()
        allocations = [a for a in self.book.allocations if a.race_number == 7 and a.sweep_label == label]
        if not allocations:
            QMessageBox.information(self, "Pre-cache web stop voices", f"Generate {label} first.")
            return
        voice = self.voice_select.currentText()
        instructions = self.current_voice_instructions()
        delivery_mode = self.selected_delivery_mode()
        targets = []
        seen_horses = set()
        seen_attendees = set()
        for a in allocations:
            h_key = (a.horse_number, a.horse_name)
            if h_key not in seen_horses:
                seen_horses.add(h_key)
                targets.append(("horse", {"horse_number": a.horse_number, "horse_name": a.horse_name, "attendee_name": a.attendee_name}, delivery_mode, voice, instructions))
            attendee_key = str(a.attendee_name).strip().lower()
            if attendee_key and attendee_key not in seen_attendees:
                seen_attendees.add(attendee_key)
                targets.append(("attendee", {"horse_number": a.horse_number, "horse_name": a.horse_name, "attendee_name": a.attendee_name}, delivery_mode, voice, instructions))

        def worker() -> None:
            done = 0
            total = len(targets)
            try:
                set_tts_runtime_status(f"Web stop voice cache: generating {total} clips for {label}...")
                for part, target, mode, v, inst in targets:
                    phrase = web_reel_stop_phrase(part, target, mode)
                    get_or_create_web_tts_clip(phrase, voice=v, instructions=inst, part=part)
                    done += 1
                    set_tts_runtime_status(f"Web stop voice cache: {done} of {total} clips ready for {label}.")
                set_tts_runtime_status(f"Web stop voice cache complete: {done} clips ready for {label}.")
            except Exception as error:
                set_tts_runtime_status(f"Web stop voice cache failed after {done} clips: {error}")

        threading.Thread(target=worker, daemon=True).start()

    def lock_selected_sweep(self) -> None:
        label = self.selected_sweep()
        allocations = [a for a in self.book.allocations if a.race_number == 7 and a.sweep_label == label]
        if not allocations:
            QMessageBox.information(self, "No Cup sweep", f"Generate {label} before locking it.")
            return
        self.book.lock_sweep(7, label)
        self.main_window.refresh_all_views()
        self.main_window.stack.setCurrentWidget(self)
        QMessageBox.information(self, "Cup sweep locked", f"{label} is now locked.")

    def unlock_selected_sweep(self) -> None:
        label = self.selected_sweep()
        answer = QMessageBox.question(
            self,
            "Unlock Cup sweep",
            f"Unlock {label}? This will allow it to be regenerated.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.book.unlock_sweep(7, label)
        self.main_window.refresh_all_views()
        self.main_window.stack.setCurrentWidget(self)

    def refresh(self) -> None:
        self.reset_draw_display()


class MoneyOwingPage(QWidget):
    PAID_COLUMN = 6

    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        title = QLabel("Money Owing")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        hint = QLabel("Double-click Paid to toggle Yes/No.")
        hint.setObjectName("hint")
        header.addWidget(hint)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.toggle_paid_cell)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        rows = self.book.amount_owing_rows()
        headers = ["Attendee", "Normal Sweeps", "Cup $1", "Cup $2", "Cup $5", "Total Owing", "Paid"]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            values = [
                row["Attendee"],
                money(row["Normal Sweeps"]),
                money(row["Cup $1"]),
                money(row["Cup $2"]),
                money(row["Cup $5"]),
                money(row["Total Owing"]),
                "Yes" if row["Paid"] else "No",
            ]
            attendee_id = str(row.get("Attendee ID", ""))
            for col, value in enumerate(values):
                item = table_item(value)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if attendee_id:
                    item.setData(Qt.UserRole, attendee_id)
                self.table.setItem(row_index, col, item)
        self.table.resizeColumnsToContents()
        self.table.setSortingEnabled(True)

    def toggle_paid_cell(self, item: QTableWidgetItem) -> None:
        if item.column() != self.PAID_COLUMN:
            return
        attendee_id = item.data(Qt.UserRole)
        attendee = self.book.attendee_by_id(str(attendee_id)) if attendee_id else None
        if attendee is None:
            attendee_name = cell_text(self.table, item.row(), 0).strip().upper()
            attendee = next((a for a in self.book.attendees if a.name.upper() == attendee_name), None)
        if attendee is None:
            QMessageBox.warning(self, "Paid toggle", "Could not find the selected attendee.")
            return
        attendee.paid = not attendee.paid
        self.book.audit("Updated paid status", f"{attendee.name}: Paid set to {bool_text(attendee.paid)} from Money Owing screen")
        self.refresh()
        self.main_window.attendees_page.refresh()
        self.main_window.cup_page.refresh()
        if hasattr(self.main_window, "payout_settings_page"):
            self.main_window.payout_settings_page.refresh()
        if hasattr(self.main_window, "audit_page"):
            self.main_window.audit_page.refresh()


class AttendeesPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        title = QLabel("Attendees")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        add_button = QPushButton("Add attendee")
        apply_button = QPushButton("Apply table changes")
        add_button.clicked.connect(self.add_attendee)
        apply_button.clicked.connect(self.apply_changes)
        header.addWidget(add_button)
        header.addWidget(apply_button)
        layout.addLayout(header)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.itemDoubleClicked.connect(self.toggle_yes_no_cell)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        headers = ["ID", "Name", "Active", "Cup Eligible", "Paid"]
        self.table.setRowCount(len(self.book.attendees))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        for row, attendee in enumerate(self.book.attendees):
            values = [attendee.attendee_id, attendee.name, bool_text(attendee.active), bool_text(attendee.cup_eligible), bool_text(attendee.paid)]
            for col, value in enumerate(values):
                item = table_item(value)
                if col == 0 or col in (2, 3, 4):
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()

    def add_attendee(self) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        new_id = f"A{row + 1:03d}"
        for col, value in enumerate([new_id, "NEW ATTENDEE", "Yes", "Yes", "No"]):
            item = table_item(value)
            if col == 0 or col in (2, 3, 4):
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, col, item)

    def toggle_yes_no_cell(self, item: QTableWidgetItem) -> None:
        if item.column() not in (2, 3, 4):
            return
        new_value = not parse_bool_text(item.text())
        item.setText(bool_text(new_value))
        old_text = item.text()
        self.apply_row_to_book(item.row())
        attendee_name = cell_text(self.table, item.row(), 1)
        self.book.audit("Updated attendee flag", f"{attendee_name}: column {item.column()} set to {old_text}")
        self.refresh_related_views()

    def apply_row_to_book(self, row: int) -> None:
        if row < 0 or row >= len(self.book.attendees):
            return
        attendee = self.book.attendees[row]
        attendee.name = (cell_text(self.table, row, 1).strip() or attendee.name).upper()
        attendee.active = parse_bool_text(cell_text(self.table, row, 2))
        attendee.cup_eligible = parse_bool_text(cell_text(self.table, row, 3))
        attendee.paid = parse_bool_text(cell_text(self.table, row, 4))

    def refresh_related_views(self) -> None:
        for page in self.main_window.race_pages.values():
            page.refresh()
        self.main_window.money_page.refresh()
        self.main_window.cup_page.refresh()
        if hasattr(self.main_window, "payout_settings_page"):
            self.main_window.payout_settings_page.refresh()
        if hasattr(self.main_window, "audit_page"):
            self.main_window.audit_page.refresh()

    def apply_changes(self) -> None:
        from sweep_engine import Attendee
        updated = []
        for row in range(self.table.rowCount()):
            attendee_id = cell_text(self.table, row, 0) or f"A{row + 1:03d}"
            name = cell_text(self.table, row, 1).strip()
            if not name:
                continue
            updated.append(
                Attendee(
                    attendee_id=attendee_id,
                    name=name.upper(),
                    active=parse_bool_text(cell_text(self.table, row, 2)),
                    cup_eligible=parse_bool_text(cell_text(self.table, row, 3)),
                    paid=parse_bool_text(cell_text(self.table, row, 4)),
                )
            )
        self.book.attendees = updated
        self.book.audit("Updated attendees", f"{len(updated)} attendee rows applied from table")
        self.main_window.refresh_all_views()


class PayoutSettingsPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)

        header = QHBoxLayout()
        title = QLabel("Payout Settings")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        apply_button = QPushButton("Apply payout settings")
        add_button = QPushButton("Add rule")
        reset_button = QPushButton("Reset defaults")
        refresh_button = QPushButton("Refresh")
        apply_button.clicked.connect(self.apply_changes)
        add_button.clicked.connect(self.add_rule)
        reset_button.clicked.connect(self.reset_defaults)
        refresh_button.clicked.connect(self.refresh)
        header.addWidget(apply_button)
        header.addWidget(add_button)
        header.addWidget(reset_button)
        header.addWidget(refresh_button)
        layout.addLayout(header)

        hint = QLabel(
            "Edit payout amounts in cents. Type 'Label' for a sweep-specific rule like Cup $5, or 'Pool' for a collected pool total like 400 cents. "
            "The Summary table shows whether collected money and payout totals balance."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.settings_table = QTableWidget()
        self.settings_table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("Payout rules"))
        layout.addWidget(self.settings_table, 2)

        self.summary_table = QTableWidget()
        self.summary_table.setAlternatingRowColors(True)
        layout.addWidget(QLabel("Current generated sweep payout summary"))
        layout.addWidget(self.summary_table, 1)
        self.refresh()

    def refresh(self) -> None:
        rows = []
        for key, payout in sorted(self.book.payout_settings.items()):
            kind, value = split_payout_setting_key(key)
            rows.append([kind.title(), value, payout[0], payout[1], payout[2], sum(payout)])
        headers = ["Type", "Key", "1st cents", "2nd cents", "3rd cents", "Total cents"]
        self.settings_table.setRowCount(len(rows))
        self.settings_table.setColumnCount(len(headers))
        self.settings_table.setHorizontalHeaderLabels(headers)
        for row_index, row in enumerate(rows):
            for col, value in enumerate(row):
                item = table_item(value)
                if col == 5:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                self.settings_table.setItem(row_index, col, item)
        self.settings_table.resizeColumnsToContents()

        summary = self.book.payout_summary_rows()
        sum_headers = ["Race", "Sweep", "Collected", "Payout Total", "Difference"]
        self.summary_table.setRowCount(len(summary))
        self.summary_table.setColumnCount(len(sum_headers))
        self.summary_table.setHorizontalHeaderLabels(sum_headers)
        for row_index, row in enumerate(summary):
            values = [row["Race"], row["Sweep"], money(row["Collected"]), money(row["Payout Total"]), money(row["Difference"])]
            for col, value in enumerate(values):
                self.summary_table.setItem(row_index, col, table_item(value))
        self.summary_table.resizeColumnsToContents()

    def add_rule(self) -> None:
        row = self.settings_table.rowCount()
        self.settings_table.insertRow(row)
        for col, value in enumerate(["Pool", "400", 250, 100, 50, 400]):
            item = table_item(value)
            if col == 5:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.settings_table.setItem(row, col, item)

    def apply_changes(self) -> None:
        settings = {}
        for row in range(self.settings_table.rowCount()):
            kind = cell_text(self.settings_table, row, 0).strip().lower()
            key = cell_text(self.settings_table, row, 1).strip()
            if not kind or not key:
                continue
            if kind not in {"label", "pool"}:
                QMessageBox.warning(self, "Invalid payout type", f"Row {row + 1}: Type must be Label or Pool.")
                return
            first = parse_money_to_cents(cell_text(self.settings_table, row, 2))
            second = parse_money_to_cents(cell_text(self.settings_table, row, 3))
            third = parse_money_to_cents(cell_text(self.settings_table, row, 4))
            settings[f"{kind}:{key}"] = (first, second, third)
        self.book.payout_settings = settings
        self.book.audit("Updated payout settings", f"{len(settings)} payout rules applied")
        self.main_window.refresh_all_views()
        QMessageBox.information(self, "Payout settings saved", "Payout settings have been applied.")

    def reset_defaults(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset payout settings",
            "Reset payout settings back to the default office sweep payout table?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.book.payout_settings = default_payout_settings()
        self.book.audit("Reset payout settings", "Defaults restored")
        self.main_window.refresh_all_views()


class AuditLogPage(QWidget):
    def __init__(self, book: SweepBook, main_window: MainWindow) -> None:
        super().__init__()
        self.book = book
        self.main_window = main_window
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 24)
        header = QHBoxLayout()
        title = QLabel("Audit Log")
        title.setObjectName("pageTitle")
        header.addWidget(title, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)
        hint = QLabel("Tracks imports, generation, result entry, paid-status changes, locked sweeps, payout changes, saves and exports.")
        hint.setObjectName("hint")
        layout.addWidget(hint)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.refresh()

    def refresh(self) -> None:
        headers = ["Time", "Action", "Details"]
        rows = self.book.audit_log
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        for row_index, entry in enumerate(rows):
            for col, value in enumerate([entry.timestamp, entry.action, entry.details]):
                self.table.setItem(row_index, col, table_item(value))
        self.table.resizeColumnsToContents()



class ResultsDialog(QDialog):
    def __init__(self, horses, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Enter winning horse numbers")
        layout = QFormLayout(self)
        self.first = QSpinBox()
        self.second = QSpinBox()
        self.third = QSpinBox()
        max_number = max((h.horse_number for h in horses), default=99)
        for box in (self.first, self.second, self.third):
            box.setRange(1, max_number)
        existing = {h.result_position: h.horse_number for h in horses if h.result_position in {1, 2, 3}}
        self.first.setValue(existing.get(1, 1))
        self.second.setValue(existing.get(2, min(2, max_number)))
        self.third.setValue(existing.get(3, min(3, max_number)))
        layout.addRow("1st horse number", self.first)
        layout.addRow("2nd horse number", self.second)
        layout.addRow("3rd horse number", self.third)
        buttons = QHBoxLayout()
        ok = QPushButton("Save")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addRow(buttons)

    def values(self) -> tuple[int, int, int]:
        return self.first.value(), self.second.value(), self.third.value()


class TableDialog(QDialog):
    def __init__(self, title: str, table: QTableWidget, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1200, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(table)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)



class LocalWebServer:
    def __init__(self, main_window: MainWindow, host: str = "0.0.0.0", port: int = 8765) -> None:
        self.main_window = main_window
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False

    def start(self) -> None:
        if self.is_running:
            return
        last_error: Optional[Exception] = None
        for candidate_port in range(self.port, self.port + 20):
            try:
                handler = self._handler_factory()
                self.httpd = ThreadingHTTPServer((self.host, candidate_port), handler)
                self.httpd.daemon_threads = True
                self.port = candidate_port
                self.thread = threading.Thread(target=self.httpd.serve_forever, name="SweepsWebDisplay", daemon=True)
                self.thread.start()
                self.is_running = True
                return
            except OSError as error:
                last_error = error
                continue
        raise RuntimeError(f"Could not start web server. Last error: {last_error}")

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        self.httpd = None
        self.thread = None
        self.is_running = False

    def local_url(self, path: str = "/") -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"http://127.0.0.1:{self.port}{path}"

    def network_url(self, path: str = "/") -> str:
        path = path if path.startswith("/") else f"/{path}"
        return f"http://{local_network_ip()}:{self.port}{path}"

    def _handler_factory(self):
        main_window = self.main_window

        class SweepsRequestHandler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path.rstrip("/") or "/"
                try:
                    if path == "/api/state":
                        body = json.dumps(web_state(main_window), indent=2)
                        self._send(body, "application/json")
                        return
                    if path == "/api/cup_stop_tts":
                        body = json.dumps(web_cup_stop_tts_response(main_window, urllib.parse.parse_qs(parsed.query)), indent=2)
                        self._send(body, "application/json")
                        return
                    if path.startswith("/tts_cache/"):
                        self._send_tts_cache_file(path)
                        return
                    if path.startswith("/race/"):
                        race_no_text = path.split("/")[-1]
                        race_no = int(race_no_text)
                        body = render_web_race(main_window, race_no)
                        self._send(body)
                        return
                    if path == "/cup3d":
                        self._send(render_web_cup3d(main_window))
                        return
                    if path == "/cup":
                        self._send(render_web_cup(main_window))
                        return
                    if path == "/money":
                        self._send(render_web_money(main_window))
                        return
                    if path == "/payouts":
                        self._send(render_web_payouts(main_window))
                        return
                    if path == "/attendees":
                        self._send(render_web_attendees(main_window))
                        return
                    self._send(render_web_home(main_window))
                except Exception as error:
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(f"Sweeps display error: {error}".encode("utf-8"))

            def _send(self, body: str, content_type: str = "text/html; charset=utf-8") -> None:
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _send_bytes(self, data: bytes, content_type: str, *, cache: bool = True) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _send_tts_cache_file(self, path: str) -> None:
                rel_text = urllib.parse.unquote(path[len("/tts_cache/"):])
                cache_root = ensure_tts_cache_dir().resolve()
                target = (cache_root / rel_text).resolve()
                if not str(target).startswith(str(cache_root)) or not target.exists() or target.is_dir():
                    self.send_response(404)
                    self.end_headers()
                    return
                content_type = "audio/wav" if target.suffix.lower() == ".wav" else "application/octet-stream"
                self._send_bytes(target.read_bytes(), content_type, cache=True)

            def log_message(self, format: str, *args) -> None:  # noqa: A002
                return

        return SweepsRequestHandler


def local_network_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.2)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return "127.0.0.1"


def make_stat_card(title: str, value_label: QLabel) -> QFrame:
    frame = QFrame()
    frame.setObjectName("statCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    label = QLabel(title)
    label.setObjectName("statTitle")
    value_label.setObjectName("statValue")
    layout.addWidget(label)
    layout.addWidget(value_label)
    return frame


def parse_odds_value(value: object) -> Optional[float]:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    text = text.replace("$", "").replace(",", "").strip()
    if "/" in text:
        parts = text.split("/", 1)
        try:
            return (float(parts[0].strip()) / float(parts[1].strip())) + 1.0
        except Exception:
            return None
    try:
        return float(text)
    except Exception:
        return None


def format_odds(value: object) -> str:
    odds = parse_odds_value(value)
    if odds is None:
        return ""
    if odds == int(odds):
        return f"${int(odds)}"
    return f"${odds:.2f}".rstrip("0").rstrip(".")


def cup_flair_for_allocation(book: SweepBook, allocation: object) -> dict[str, object]:
    race_no = int(getattr(allocation, "race_number", 7) or 7)
    horse_no = int(getattr(allocation, "horse_number", 0) or 0)
    return cup_flair_for_horse(book, race_no, horse_no, getattr(allocation, "odds", ""))


def cup_flair_for_reveal(main_window: MainWindow, reveal: dict[str, object]) -> dict[str, object]:
    existing = reveal.get("flair") if isinstance(reveal, dict) else None
    if isinstance(existing, dict) and existing.get("title"):
        return existing
    race_no = int(reveal.get("race_number", 7) or 7)
    horse_no = int(reveal.get("horse_number", 0) or 0)
    odds = reveal.get("odds", "")
    return cup_flair_for_horse(main_window.book, race_no, horse_no, odds)


def cup_flair_for_horse(book: SweepBook, race_no: int, horse_no: int, odds_value: object = "") -> dict[str, object]:
    race = book.races.get(race_no)
    if not race:
        return {"title": "", "text": "", "class": "", "emoji": "", "odds": ""}
    odds_rows = []
    horse_odds = parse_odds_value(odds_value)
    for horse in race.runners:
        odds = parse_odds_value(getattr(horse, "odds", ""))
        if odds is not None and odds > 0:
            odds_rows.append((horse.horse_number, odds))
        if horse.horse_number == horse_no and horse_odds is None:
            horse_odds = odds
    if horse_odds is None:
        return {"title": "", "text": "", "class": "", "emoji": "", "odds": ""}
    odds_label = format_odds(horse_odds)
    odds_values = [odds for _no, odds in odds_rows]
    min_odds = min(odds_values) if odds_values else horse_odds
    max_odds = max(odds_values) if odds_values else horse_odds
    if abs(horse_odds - min_odds) < 0.0001:
        return {
            "title": "Market favourite",
            "text": f"{odds_label} — shortest-priced runner in the race.",
            "class": "favourite",
            "emoji": "🔥",
            "odds": odds_label,
        }
    if horse_odds >= 20 or (abs(horse_odds - max_odds) < 0.0001 and horse_odds >= 10):
        return {
            "title": "Long odds roughie",
            "text": f"{odds_label} — big price, big bragging rights if it gets up.",
            "class": "longshot",
            "emoji": "🎯",
            "odds": odds_label,
        }
    if horse_odds <= 6:
        return {
            "title": "Well fancied",
            "text": f"{odds_label} — not favourite, but the market gives it a real chance.",
            "class": "fancied",
            "emoji": "⚡",
            "odds": odds_label,
        }
    return {"title": "", "text": "", "class": "", "emoji": "", "odds": odds_label}


def enriched_cup_reveal(main_window: MainWindow, reveal: object) -> dict[str, object]:
    if not isinstance(reveal, dict):
        return {}
    out = dict(reveal)
    if not out.get("odds"):
        race = main_window.book.races.get(7)
        if race:
            horse = next((h for h in race.runners if h.horse_number == int(out.get("horse_number", 0) or 0)), None)
            if horse:
                out["odds"] = getattr(horse, "odds", "")
    out["flair"] = cup_flair_for_reveal(main_window, out)
    return out


def allocation_to_cup_option(allocation: Allocation) -> dict[str, object]:
    return {
        "horse_number": allocation.horse_number,
        "horse_name": allocation.horse_name,
        "display": f"#{allocation.horse_number} {allocation.horse_name}",
        "odds": getattr(allocation, "odds", ""),
    }


def enrich_cup_spin_event(main_window: MainWindow, spin_event: object) -> dict[str, object]:
    if not isinstance(spin_event, dict):
        return {}
    out = dict(spin_event)
    target = out.get("target")
    if isinstance(target, dict):
        target = dict(target)
        target["flair"] = cup_flair_for_reveal(main_window, target)
        out["target"] = target
    return out


def web_reel_stop_phrase(part: str, target: dict[str, object], delivery_mode: str = "Race caller") -> str:
    part = str(part or "").lower().strip()
    horse_number = target.get("horse_number", "")
    horse_name = str(target.get("horse_name", "")).strip()
    attendee_name = str(target.get("attendee_name", "")).strip().title()
    mode = delivery_mode or "Race caller"
    if part == "horse":
        if mode == "Big reveal":
            return f"The horse is locked in... number {horse_number}... {horse_name}!"
        if mode == "Plain":
            return f"Horse number {horse_number}, {horse_name}."
        return f"Horse number {horse_number}... {horse_name}!"
    if mode == "Big reveal":
        return f"And the ticket holder is... wait for it... {attendee_name}!"
    if mode == "Plain":
        return f"{attendee_name}."
    return f"And that goes to... {attendee_name}!"


def web_tts_cache_url(path: Path) -> str:
    root = ensure_tts_cache_dir().resolve()
    rel = path.resolve().relative_to(root).as_posix()
    return "/tts_cache/" + urllib.parse.quote(rel)


def get_or_create_web_tts_clip(text: str, *, voice: str, instructions: str, part: str, speed: float = 1.0) -> Path:
    text = " ".join(str(text or "").split())
    if not text:
        raise RuntimeError("Empty TTS clip text.")
    key_text = json.dumps({"v": voice or "marin", "i": instructions or cup_voice_instructions(), "t": text, "s": speed, "part": part}, sort_keys=True)
    digest = hashlib.sha1(key_text.encode("utf-8")).hexdigest()[:24]
    folder = ensure_tts_cache_dir() / "web_stop_clips"
    folder.mkdir(parents=True, exist_ok=True)
    output_path = folder / f"{str(part or 'clip').lower()}_{digest}.wav"
    if output_path.exists() and output_path.stat().st_size > 64:
        return output_path
    temp_path = generate_openai_tts_wav(text, voice=voice or "marin", instructions=instructions or cup_voice_instructions(), speed=speed)
    try:
        temp_path.replace(output_path)
    except Exception:
        output_path.write_bytes(temp_path.read_bytes())
        temp_path.unlink(missing_ok=True)
    repair_streaming_wav_header(output_path)
    return output_path


def web_cup_stop_tts_response(main_window: MainWindow, query: dict[str, list[str]]) -> dict[str, object]:
    spin_id = (query.get("spin_id") or [""])[0]
    part = (query.get("part") or [""])[0].lower().strip()
    spin = getattr(main_window, "current_cup_spin_event", None)
    if not isinstance(spin, dict) or not spin_id or spin.get("spin_id") != spin_id:
        return {"ok": False, "error": "Spin event not found or no longer current."}
    if part not in {"horse", "attendee"}:
        return {"ok": False, "error": "part must be horse or attendee."}
    target = spin.get("target")
    if not isinstance(target, dict):
        return {"ok": False, "error": "Spin target not found."}
    phrase = web_reel_stop_phrase(part, target, str(spin.get("delivery_mode") or "Race caller"))
    if not bool(spin.get("announce_enabled", True)):
        return {"ok": False, "error": "Announcements are turned off.", "text": phrase}
    if str(spin.get("voice_engine") or "openai") != "openai":
        return {"ok": False, "error": "OpenAI live is not selected; browser will use fallback speech if available.", "text": phrase}
    try:
        clip = get_or_create_web_tts_clip(
            phrase,
            voice=str(spin.get("voice") or "marin"),
            instructions=str(spin.get("voice_instructions") or cup_voice_instructions()),
            part=part,
        )
        return {"ok": True, "url": web_tts_cache_url(clip), "text": phrase, "bytes": clip.stat().st_size}
    except Exception as error:
        set_tts_runtime_status(f"Web reel-stop voice failed: {error}")
        return {"ok": False, "error": str(error), "text": phrase}


def web_state(main_window: MainWindow) -> dict[str, object]:
    book = main_window.book
    selected_cup = "Cup $5"
    try:
        selected_cup = main_window.cup_page.selected_sweep()
    except Exception:
        pass
    cup_allocations = [a for a in book.allocations if a.race_number == 7 and a.sweep_label == selected_cup]
    drawn_by_label: dict[str, list[dict[str, object]]] = {}
    raw_drawn = getattr(main_window, "cup_drawn_reveals", {}) or {}
    if isinstance(raw_drawn, dict):
        for label, rows in raw_drawn.items():
            if isinstance(rows, list):
                drawn_by_label[str(label)] = [enriched_cup_reveal(main_window, r) for r in rows if isinstance(r, dict)]
    return {
        "races": len(book.races),
        "active_attendees": len(book.active_attendees),
        "allocations": len(book.allocations),
        "locked_sweeps": len(book.locked_sweeps),
        "current_cup_reveal": enriched_cup_reveal(main_window, main_window.current_cup_reveal),
        "cup": {
            "selected_sweep": selected_cup,
            "spin_event": enrich_cup_spin_event(main_window, getattr(main_window, "current_cup_spin_event", None)),
            "current_reveal": enriched_cup_reveal(main_window, main_window.current_cup_reveal),
            "drawn_by_label": drawn_by_label,
            "horse_options": [allocation_to_cup_option(a) for a in cup_allocations],
            "attendee_options": sorted({a.attendee_name for a in cup_allocations}),
        },
    }


def web_escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


def web_nav(book: SweepBook) -> str:
    race_links = "".join(f'<a href="/race/{race_no}">Race {race_no}</a>' for race_no in sorted(book.races))
    return f"""
    <nav>
      <a href="/">Dashboard</a>
      {race_links}
      <a href="/cup">Cup Display</a>
      <a href="/cup3d">3D Cup Display</a>
      <a href="/money">Money Owing</a>
      <a href="/payouts">Payouts</a>
      <a href="/attendees">Attendees</a>
    </nav>
    """


def web_page(main_window: MainWindow, title: str, body: str, *, refresh_seconds: int = 4) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>{web_escape(title)}</title>
<style>
:root {{
  --bg:#07111f; --panel:#101a2b; --card:#172338; --card2:#1e2c45;
  --text:#f8fafc; --muted:#b6c2d1; --line:#2dd4bf; --warn:#f59e0b; --bad:#ef4444;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Segoe UI, Arial, sans-serif; background:radial-gradient(circle at top left,#17304a 0,#07111f 40%,#050b14 100%); color:var(--text); }}
header {{ padding:28px 34px 14px; border-bottom:1px solid rgba(255,255,255,.08); }}
h1 {{ margin:0; font-size:42px; letter-spacing:.2px; }}
h2 {{ margin:26px 0 12px; font-size:26px; }}
p {{ color:var(--muted); }}
nav {{ display:flex; gap:10px; padding:12px 34px; flex-wrap:wrap; background:rgba(7,17,31,.72); position:sticky; top:0; backdrop-filter: blur(8px); z-index:5; }}
nav a {{ color:var(--text); text-decoration:none; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14); padding:10px 14px; border-radius:999px; font-weight:700; }}
nav a:hover {{ border-color:var(--line); background:rgba(45,212,191,.16); }}
main {{ padding:24px 34px 60px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px; }}
.card {{ background:linear-gradient(180deg,var(--card),var(--panel)); border:1px solid rgba(255,255,255,.1); border-left:5px solid var(--line); border-radius:20px; padding:18px; box-shadow:0 14px 30px rgba(0,0,0,.24); }}
.label {{ color:var(--muted); font-size:13px; font-weight:900; letter-spacing:1px; text-transform:uppercase; }}
.value {{ font-size:36px; font-weight:950; margin-top:4px; }}
table {{ width:100%; border-collapse:separate; border-spacing:0; overflow:hidden; border-radius:18px; background:var(--card); border:1px solid rgba(255,255,255,.1); }}
th, td {{ text-align:left; padding:13px 14px; border-bottom:1px solid rgba(255,255,255,.08); }}
th {{ background:var(--card2); font-size:13px; color:#dbeafe; text-transform:uppercase; letter-spacing:.8px; }}
tr:nth-child(even) td {{ background:rgba(255,255,255,.035); }}
.bigReveal {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-top:20px; }}
.revealBox {{ min-height:210px; display:flex; flex-direction:column; justify-content:center; align-items:center; background:linear-gradient(135deg,#0f766e,#172554); border-radius:28px; border:2px solid rgba(45,212,191,.65); box-shadow:0 20px 60px rgba(0,0,0,.32); padding:20px; }}
.revealBox .label {{ color:#ccfbf1; }}
.revealBox .value {{ font-size:52px; text-align:center; line-height:1.05; }}
.badge {{ display:inline-block; border-radius:999px; padding:5px 10px; background:rgba(45,212,191,.15); border:1px solid rgba(45,212,191,.5); font-weight:800; }}
.warn {{ color:var(--warn); }}
.bad {{ color:var(--bad); }}
@media (max-width:900px) {{ .bigReveal {{ grid-template-columns:1fr; }} h1 {{ font-size:32px; }} .revealBox .value {{ font-size:38px; }} }}
</style>
</head>
<body>
<header><h1>{web_escape(title)}</h1><p>Read-only display from the Sweeps admin app. Auto-refreshes every {refresh_seconds} seconds.</p></header>
{web_nav(main_window.book)}
<main>{body}</main>
</body>
</html>"""


def stat_card(title: str, value: object) -> str:
    return f'<div class="card"><div class="label">{web_escape(title)}</div><div class="value">{web_escape(value)}</div></div>'


def web_table(headers: List[str], rows: List[List[object]]) -> str:
    head = "".join(f"<th>{web_escape(header)}</th>" for header in headers)
    body = ""
    for row in rows:
        body += "<tr>" + "".join(f"<td>{web_escape(value)}</td>" for value in row) + "</tr>"
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_web_home(main_window: MainWindow) -> str:
    book = main_window.book
    total_owing = sum(row["Total Owing"] for row in book.amount_owing_rows())
    body = '<section class="grid">'
    body += stat_card("Races Imported", len(book.races))
    body += stat_card("Active Attendees", len(book.active_attendees))
    body += stat_card("Allocations", len(book.allocations))
    body += stat_card("Total Owing", money(total_owing))
    body += stat_card("Locked Sweeps", len(book.locked_sweeps))
    body += "</section>"
    rows = []
    for race_no in sorted(book.races):
        race = book.races[race_no]
        labels = sorted({a.sweep_label for a in book.allocations if a.race_number == race_no})
        rows.append([race_no, race.race_name, len(race.runners), len(labels), len(book.locked_labels_for_race(race_no))])
    body += "<h2>Race Status</h2>" + web_table(["Race", "Race Name", "Runners", "Sweeps", "Locked"], rows)
    return web_page(main_window, "Sweeps Dashboard", body)


def render_web_race(main_window: MainWindow, race_no: int) -> str:
    book = main_window.book
    race = book.races.get(race_no)
    if not race:
        return web_page(main_window, f"Race {race_no}", "<p>Race not imported yet.</p>")
    body = '<section class="grid">'
    body += stat_card("Runners", len(race.runners))
    body += stat_card("Active Attendees", len(book.active_attendees))
    body += stat_card("Locked Sweeps", len(book.locked_labels_for_race(race_no)))
    body += "</section>"
    allocations = [a for a in book.allocations if a.race_number == race_no]
    if not allocations:
        body += "<h2>No sweeps generated yet</h2>"
        return web_page(main_window, f"Race {race_no}: {race.race_name}", body)
    labels: List[str] = []
    for allocation in allocations:
        if allocation.sweep_label not in labels:
            labels.append(allocation.sweep_label)
    for label in labels:
        rows = []
        for allocation in [a for a in allocations if a.sweep_label == label]:
            rows.append([allocation.attendee_name, allocation.horse_number, allocation.horse_name, allocation.allocation_type])
        locked = " 🔒" if book.is_sweep_locked(race_no, label) else ""
        body += f"<h2>{web_escape(label)}{locked}</h2>" + web_table(["Attendee", "Horse No", "Horse", "Type"], rows)
    return web_page(main_window, f"Race {race_no}: {race.race_name}", body)


def render_web_cup(main_window: MainWindow) -> str:
    nav = web_nav(main_window.book)
    template = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Melbourne Cup Draw</title>
<style>
:root {
  --bg:#040917; --panel:#0b1526; --panel2:#111f35; --card:#16243d;
  --text:#f8fafc; --muted:#b9c7d8; --line:#2dd4bf; --line2:#38bdf8;
  --gold:#fbbf24; --red:#ef4444; --green:#22c55e; --violet:#a78bfa;
}
* { box-sizing:border-box; }
html, body { min-height:100%; }
body {
  margin:0; font-family:Segoe UI, Arial, sans-serif; color:var(--text);
  background:
    radial-gradient(circle at 12% 0%, rgba(45,212,191,.18), transparent 30%),
    radial-gradient(circle at 84% 12%, rgba(251,191,36,.13), transparent 25%),
    linear-gradient(145deg,#050a14 0%, #081326 48%, #040917 100%);
  overflow-x:hidden;
}
body.flash { animation: screenFlash .72s ease-out; }
@keyframes screenFlash { 0% { filter:brightness(1); } 18% { filter:brightness(1.9); } 100% { filter:brightness(1); } }
header { padding:18px 26px 10px; border-bottom:1px solid rgba(255,255,255,.08); background:rgba(4,9,23,.72); backdrop-filter:blur(10px); }
.headerRow { display:flex; align-items:center; gap:16px; flex-wrap:wrap; }
h1 { margin:0; font-size:44px; letter-spacing:.2px; text-shadow:0 4px 18px rgba(45,212,191,.25); }
.small { color:var(--muted); }
.statusLine { color:#dbeafe; margin-top:6px; }
.soundBtn { margin-left:auto; cursor:pointer; border:1px solid rgba(45,212,191,.6); background:rgba(45,212,191,.13); color:var(--text); border-radius:999px; padding:11px 16px; font-weight:900; font-size:15px; }
.soundBtn.enabled { background:rgba(34,197,94,.2); border-color:rgba(34,197,94,.75); }
nav { display:flex; gap:10px; padding:12px 26px; flex-wrap:wrap; background:rgba(7,17,31,.75); position:sticky; top:0; backdrop-filter:blur(8px); z-index:5; border-bottom:1px solid rgba(255,255,255,.08); }
nav a { color:var(--text); text-decoration:none; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14); padding:10px 15px; border-radius:999px; font-weight:800; }
nav a:hover { border-color:var(--line); background:rgba(45,212,191,.16); }
main { padding:22px 26px 60px; }
.topStrip { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:18px; }
.badge { display:inline-flex; align-items:center; gap:7px; border-radius:999px; padding:7px 12px; background:rgba(45,212,191,.15); border:1px solid rgba(45,212,191,.55); font-weight:950; color:#ccfbf1; }
.badge.gold { background:rgba(251,191,36,.18); border-color:rgba(251,191,36,.65); color:#fde68a; }
.machine {
  position:relative; display:grid; grid-template-columns:1fr 1fr; gap:24px; margin:8px 0 20px;
  padding:22px; border-radius:34px; border:1px solid rgba(45,212,191,.22);
  background:linear-gradient(180deg,rgba(15,23,42,.88),rgba(8,15,30,.88));
  box-shadow:0 30px 90px rgba(0,0,0,.42), inset 0 0 0 1px rgba(255,255,255,.04);
}
.machine:before { content:""; position:absolute; inset:10px; border-radius:26px; border:1px dashed rgba(251,191,36,.16); pointer-events:none; }
.reelPanel { position:relative; overflow:hidden; min-height:360px; border-radius:28px; padding:22px; border:2px solid rgba(45,212,191,.6); background:linear-gradient(145deg,rgba(16,118,110,.82),rgba(23,37,84,.86)); box-shadow:0 16px 50px rgba(0,0,0,.35), inset 0 0 32px rgba(255,255,255,.05); }
.reelTitle { text-align:center; font-weight:1000; color:#ccfbf1; letter-spacing:2px; text-transform:uppercase; margin-bottom:12px; }
.reelViewport { position:relative; height:250px; overflow:hidden; border-radius:20px; background:rgba(0,0,0,.28); border:1px solid rgba(255,255,255,.12); box-shadow:inset 0 10px 30px rgba(0,0,0,.35); }
.reelViewport:before, .reelViewport:after { content:""; position:absolute; left:0; right:0; height:72px; z-index:3; pointer-events:none; }
.reelViewport:before { top:0; background:linear-gradient(180deg,rgba(4,9,23,.95),transparent); }
.reelViewport:after { bottom:0; background:linear-gradient(0deg,rgba(4,9,23,.95),transparent); }
.payline { position:absolute; top:96px; left:10px; right:10px; height:58px; border-radius:14px; z-index:2; border:2px solid rgba(251,191,36,.75); box-shadow:0 0 24px rgba(251,191,36,.18), inset 0 0 14px rgba(251,191,36,.08); pointer-events:none; }
.reel { position:absolute; inset:0; display:flex; flex-direction:column; justify-content:center; }
.reelItem { height:58px; display:flex; align-items:center; justify-content:center; padding:0 18px; text-align:center; font-size:34px; line-height:1.05; font-weight:1000; color:#e5e7eb; opacity:.45; transform:scale(.92); transition:opacity .12s, transform .12s, text-shadow .12s; }
.reelItem.center { opacity:1; transform:scale(1.05); color:#fff; text-shadow:0 4px 24px rgba(45,212,191,.35); }
.reelPanel.spinning .reelItem { filter:blur(1.4px); }
.reelPanel.landed { animation: reelLand .42s ease-out; border-color:rgba(251,191,36,.9); }
@keyframes reelLand { 0% { transform:scale(1); } 35% { transform:scale(1.035); } 100% { transform:scale(1); } }
.revealText { margin:16px 0 0; text-align:center; font-weight:1000; font-size:30px; min-height:42px; }
.flairBox { display:none; margin:14px 0 22px; border-radius:24px; padding:18px 22px; font-weight:950; border:2px solid rgba(255,255,255,.16); box-shadow:0 18px 46px rgba(0,0,0,.28); }
.flairBox.show { display:flex; align-items:center; gap:14px; animation: pop .54s ease-out; }
@keyframes pop { 0% { transform:scale(.92); opacity:0; } 70% { transform:scale(1.03); } 100% { transform:scale(1); opacity:1; } }
.flairEmoji { font-size:42px; }
.flairTitle { font-size:30px; }
.flairText { color:#e2e8f0; font-size:17px; margin-top:2px; }
.flairBox.favourite { background:linear-gradient(135deg,rgba(251,191,36,.28),rgba(180,83,9,.28)); border-color:rgba(251,191,36,.75); }
.flairBox.longshot { background:linear-gradient(135deg,rgba(239,68,68,.24),rgba(124,58,237,.25)); border-color:rgba(248,113,113,.72); }
.flairBox.fancied { background:linear-gradient(135deg,rgba(56,189,248,.24),rgba(45,212,191,.20)); border-color:rgba(56,189,248,.72); }
table { width:100%; border-collapse:separate; border-spacing:0; overflow:hidden; border-radius:18px; background:#111827; border:1px solid rgba(255,255,255,.1); }
th, td { text-align:left; padding:13px 14px; border-bottom:1px solid rgba(255,255,255,.08); font-size:18px; }
th { background:#1e2c45; font-size:13px; color:#dbeafe; text-transform:uppercase; letter-spacing:.8px; }
tr:nth-child(even) td { background:rgba(255,255,255,.035); }
tr.newest td { background:rgba(45,212,191,.12); }
.empty { color:var(--muted); border:1px dashed rgba(255,255,255,.18); border-radius:20px; padding:20px; background:rgba(255,255,255,.04); }
@media (max-width:900px) { .machine { grid-template-columns:1fr; } h1 { font-size:32px; } .reelItem { font-size:28px; } }
</style>
</head>
<body>
<header>
  <div class="headerRow">
    <h1>Melbourne Cup Draw</h1>
    <button id="soundBtn" class="soundBtn">Enable sound</button>
  </div>
  <div id="statusLine" class="statusLine">Waiting for the Sweeps admin app...</div>
</header>
__NAV__
<main>
  <section class="topStrip">
    <span id="sweepBadge" class="badge gold">Cup Display</span>
    <span id="drawProgress" class="small">No draw yet</span>
  </section>

  <section class="machine">
    <div id="horsePanel" class="reelPanel">
      <div class="reelTitle">Horse</div>
      <div class="reelViewport"><div id="horseReel" class="reel"></div><div class="payline"></div></div>
    </div>
    <div id="attendeePanel" class="reelPanel">
      <div class="reelTitle">Attendee</div>
      <div class="reelViewport"><div id="attendeeReel" class="reel"></div><div class="payline"></div></div>
    </div>
  </section>

  <div id="flairBox" class="flairBox"><div id="flairEmoji" class="flairEmoji"></div><div><div id="flairTitle" class="flairTitle"></div><div id="flairText" class="flairText"></div></div></div>
  <div id="revealText" class="revealText">Generate a Cup sweep in the admin app, then press Draw next.</div>
  <h2>Revealed Cup Draws</h2>
  <div id="drawTable" class="empty">Nothing has been revealed yet.</div>
</main>
<script>
let lastSpinId = null;
let spinRunning = false;
let latestState = null;
let audioCtx = null;
let soundEnabled = false;
let lastRenderedRevealSpin = null;
let lastDisplayedRevealKey = null;
let activeSpinToken = 0;
const $ = (id) => document.getElementById(id);

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
}

function displayHorse(obj) {
  if (!obj) return '---';
  if (typeof obj === 'string') return obj;
  if (obj.display) return obj.display;
  return `#${obj.horse_number ?? '---'} ${obj.horse_name ?? ''}`.trim();
}
function displayAttendee(v) {
  if (!v) return '---';
  return typeof v === 'string' ? v : (v.display || v.attendee_name || '---');
}
function randomFrom(arr, fallback='---') {
  if (!arr || !arr.length) return fallback;
  return arr[Math.floor(Math.random() * arr.length)];
}
function makeReelRows(centerText, pool, displayFn) {
  const rows = [];
  for (let i=0; i<5; i++) rows.push(displayFn(randomFrom(pool, centerText)));
  rows[2] = centerText;
  return rows;
}
function setReel(reel, rows) {
  reel.innerHTML = rows.map((text, i) => `<div class="reelItem ${i===2?'center':''}">${escapeHtml(text)}</div>`).join('');
}
function setFinalReel(reel, panel, text, pool, displayFn) {
  setReel(reel, makeReelRows(text, pool, displayFn));
  panel.classList.remove('spinning');
  panel.classList.add('landed');
  setTimeout(() => panel.classList.remove('landed'), 520);
}

$('soundBtn').addEventListener('click', () => {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    audioCtx.resume();
    soundEnabled = true;
    $('soundBtn').textContent = 'Sound enabled';
    $('soundBtn').classList.add('enabled');
    playChime();
  } catch (e) {
    $('soundBtn').textContent = 'Sound unavailable';
  }
});
function tone(freq, duration=0.06, type='square', gain=0.035) {
  if (!soundEnabled || !audioCtx) return;
  const now = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const g = audioCtx.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  g.gain.setValueAtTime(gain, now);
  g.gain.exponentialRampToValueAtTime(0.0001, now + duration);
  osc.connect(g); g.connect(audioCtx.destination);
  osc.start(now); osc.stop(now + duration);
}
function playTick() { tone(420 + Math.random()*70, 0.032, 'square', 0.018); }
function playStop() { tone(95, 0.09, 'sine', 0.075); setTimeout(()=>tone(180,0.045,'triangle',0.035), 70); }
function playChime() { [523,659,784,1046].forEach((f,i)=>setTimeout(()=>tone(f,0.12,'sine',0.04), i*85)); }
function playRoughie() { [180,155,220,330].forEach((f,i)=>setTimeout(()=>tone(f,0.12,'sawtooth',0.035), i*95)); }
function playFanfare() { [392,523,659,784,1046].forEach((f,i)=>setTimeout(()=>tone(f,0.11,'triangle',0.045), i*80)); }
function playFlair(flair) {
  if (!flair || !flair.title) return;
  if (flair.class === 'favourite') playChime();
  else if (flair.class === 'longshot') playRoughie();
  else if (flair.class === 'fancied') playFanfare();
}

function animateReel(reel, panel, pool, finalText, durationMs, displayFn, token) {
  return new Promise(resolve => {
    const start = performance.now();
    panel.classList.add('spinning');
    panel.classList.remove('landed');
    function tick() {
      if (token !== activeSpinToken) {
        panel.classList.remove('spinning');
        resolve();
        return;
      }
      const elapsed = performance.now() - start;
      const progress = Math.min(1, elapsed / durationMs);
      if (progress >= 1) {
        if (token === activeSpinToken) {
          setFinalReel(reel, panel, finalText, pool, displayFn);
          playStop();
        }
        resolve();
        return;
      }
      const center = displayFn(randomFrom(pool, finalText));
      setReel(reel, makeReelRows(center, pool, displayFn));
      playTick();
      const delay = 42 + Math.pow(progress, 3) * 210;
      setTimeout(tick, delay);
    }
    tick();
  });
}

async function runSpin(spin) {
  if (!spin || !spin.target) return;
  spinRunning = true;
  const token = ++activeSpinToken;
  const target = spin.target;
  const horsePool = spin.horse_options && spin.horse_options.length ? spin.horse_options : (latestState?.cup?.horse_options || []);
  const attendeePool = spin.attendee_options && spin.attendee_options.length ? spin.attendee_options : (latestState?.cup?.attendee_options || []);
  const horseText = displayHorse(target);
  const attendeeText = displayAttendee(target.attendee_name);
  const horseMs = Number(spin.horse_duration_ms || 3200);
  const attendeeMs = Number(spin.attendee_duration_ms || 5200);
  $('sweepBadge').textContent = spin.sweep_label || 'Cup Display';
  $('drawProgress').textContent = `Draw ${spin.draw_index || ''} of ${spin.draw_total || ''}`;
  $('statusLine').textContent = 'Spinning the wheels... horse lands first, attendee lands second.';
  $('revealText').textContent = 'Spinning...';
  $('flairBox').className = 'flairBox';
  $('horsePanel').classList.add('spinning');
  $('attendeePanel').classList.add('spinning');

  const horseDone = animateReel($('horseReel'), $('horsePanel'), horsePool, horseText, horseMs, displayHorse, token);
  const attendeeDone = animateReel($('attendeeReel'), $('attendeePanel'), attendeePool, attendeeText, attendeeMs, displayAttendee, token);
  await horseDone;
  if (token !== activeSpinToken) return;
  $('revealText').textContent = `${horseText} is locked in... waiting for the attendee.`;
  await attendeeDone;
  if (token !== activeSpinToken) return;
  spinRunning = false;
  document.body.classList.add('flash');
  setTimeout(()=>document.body.classList.remove('flash'), 760);
  const flair = target.flair || {};
  showFlair(flair);
  playFlair(flair);
  $('revealText').textContent = `${attendeeText} has drawn ${horseText}`;
  $('statusLine').textContent = 'Reveal complete.';
  await fetchState(true);
}

function showFlair(flair) {
  const box = $('flairBox');
  if (!flair || !flair.title) { box.className = 'flairBox'; return; }
  box.className = `flairBox show ${flair.class || ''}`;
  $('flairEmoji').textContent = flair.emoji || '✨';
  $('flairTitle').textContent = flair.title;
  $('flairText').textContent = flair.text || '';
}

function renderTable(cup) {
  if (spinRunning) return;
  const drawn = cup.drawn_by_label || {};
  let html = '';
  for (const label of ['Cup $1','Cup $2','Cup $5']) {
    const rows = drawn[label] || [];
    if (!rows.length) continue;
    html += `<h3>${escapeHtml(label)} — revealed only</h3>`;
    html += '<table><thead><tr><th>Draw</th><th>Attendee</th><th>Horse No</th><th>Horse</th><th>Type</th></tr></thead><tbody>';
    rows.forEach((r, i) => {
      const newest = (r.spin_id && r.spin_id === lastRenderedRevealSpin) || i === rows.length - 1 ? ' class="newest"' : '';
      html += `<tr${newest}><td>${escapeHtml(r.draw_index || i+1)}</td><td>${escapeHtml(r.attendee_name || '')}</td><td>${escapeHtml(r.horse_number || '')}</td><td>${escapeHtml(r.horse_name || '')}</td><td>${escapeHtml(r.allocation_type || '')}</td></tr>`;
    });
    html += '</tbody></table>';
  }
  $('drawTable').className = html ? '' : 'empty';
  $('drawTable').innerHTML = html || 'Nothing has been revealed yet.';
}

function renderFromState(state) {
  const cup = state.cup || {};
  const reveal = cup.current_reveal || {};
  $('sweepBadge').textContent = reveal.sweep_label || cup.selected_sweep || 'Cup Display';
  if (reveal && reveal.horse_number) {
    const horseText = `#${reveal.horse_number} ${reveal.horse_name || ''}`.trim();
    const revealKey = reveal.spin_id || `${reveal.sweep_label || ''}-${reveal.draw_index || ''}-${reveal.horse_number || ''}-${reveal.attendee_name || ''}`;
    if (revealKey !== lastDisplayedRevealKey) {
      setFinalReel($('horseReel'), $('horsePanel'), horseText, cup.horse_options || [], displayHorse);
      setFinalReel($('attendeeReel'), $('attendeePanel'), reveal.attendee_name || '---', cup.attendee_options || [], displayAttendee);
      lastDisplayedRevealKey = revealKey;
    } else {
      $('horsePanel').classList.remove('spinning');
      $('attendeePanel').classList.remove('spinning');
    }
    $('drawProgress').textContent = `Draw ${reveal.draw_index || ''} of ${reveal.draw_total || ''}${reveal.timestamp ? ' at ' + reveal.timestamp : ''}`;
    $('revealText').textContent = `${reveal.attendee_name || '---'} has drawn ${horseText}`;
    lastRenderedRevealSpin = reveal.spin_id || lastRenderedRevealSpin;
    showFlair(reveal.flair || {});
  } else {
    if (lastDisplayedRevealKey !== '__empty__') {
      setFinalReel($('horseReel'), $('horsePanel'), '---', cup.horse_options || [], displayHorse);
      setFinalReel($('attendeeReel'), $('attendeePanel'), '---', cup.attendee_options || [], displayAttendee);
      lastDisplayedRevealKey = '__empty__';
    }
    $('drawProgress').textContent = 'No draw revealed yet';
    $('revealText').textContent = 'Generate a Cup sweep in the admin app, then press Draw next.';
    showFlair({});
  }
  renderTable(cup);
}

async function fetchState(forceRender=false) {
  try {
    const res = await fetch('/api/state?ts=' + Date.now(), {cache:'no-store'});
    const state = await res.json();
    latestState = state;
    const cup = state.cup || {};
    const spin = cup.spin_event || {};
    const reveal = cup.current_reveal || {};
    // Do not let the admin app's quick reveal state cut the browser animation short.
    // The backend may finish and record the result before the big-screen reels have
    // completed their longer theatrical spin. The web display should keep spinning
    // until its own horse and attendee reels have landed.
    if (spin && spin.spin_id && spin.status === 'spinning' && spin.spin_id !== lastSpinId) {
      lastSpinId = spin.spin_id;
      runSpin(spin);
      return;
    }
    if (!spinRunning || forceRender) {
      renderFromState(state);
      $('statusLine').textContent = 'Connected to Sweeps admin app. Waiting for Draw next.';
    }
  } catch (e) {
    $('statusLine').textContent = 'Waiting for the Sweeps admin app web server...';
  }
}

setReel($('horseReel'), ['---','---','---','---','---']);
setReel($('attendeeReel'), ['---','---','---','---','---']);
fetchState(true);
setInterval(fetchState, 350);
</script>
</body>
</html>"""
    return template.replace("__NAV__", nav)



def render_web_cup3d(main_window: MainWindow) -> str:
    """3D/WebGL cup display. The backend still controls the real draw; this page only performs the theatrical reveal."""
    nav = web_nav(main_window.book)
    template = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>3D Melbourne Cup Draw</title>
<style>
:root { --gold:#fbbf24; --teal:#2dd4bf; --red:#ef4444; --bg:#030712; --text:#f8fafc; --muted:#b7c3d7; }
* { box-sizing:border-box; }
html, body { margin:0; min-height:100%; overflow:hidden; font-family:Segoe UI, Arial, sans-serif; background:#030712; color:var(--text); }
#gl { position:fixed; inset:0; width:100vw; height:100vh; display:block; background:radial-gradient(circle at 50% 0%,#172554,#030712 55%); }
.overlay { position:relative; z-index:2; min-height:100vh; display:flex; flex-direction:column; }
header { padding:18px 28px 10px; background:linear-gradient(180deg,rgba(2,6,23,.86),rgba(2,6,23,.28)); border-bottom:1px solid rgba(255,255,255,.10); }
.headerRow { display:flex; gap:16px; align-items:center; }
h1 { margin:0; font-size:44px; letter-spacing:.4px; text-shadow:0 0 18px rgba(251,191,36,.35); }
.soundBtn { margin-left:auto; cursor:pointer; border:1px solid rgba(251,191,36,.65); background:rgba(251,191,36,.14); color:var(--text); border-radius:999px; padding:11px 16px; font-weight:900; }
.soundBtn.enabled { border-color:rgba(34,197,94,.8); background:rgba(34,197,94,.20); }
.status { color:#dbeafe; margin-top:6px; font-weight:700; }
nav { display:flex; gap:10px; flex-wrap:wrap; padding:10px 28px; background:rgba(2,6,23,.60); backdrop-filter:blur(8px); }
nav a { color:var(--text); text-decoration:none; background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.14); padding:8px 13px; border-radius:999px; font-weight:800; font-size:14px; }
.stage { flex:1; display:grid; grid-template-columns:1fr 1fr; gap:34px; align-items:center; padding:22px 5vw 16px; }
.reelCard { position:relative; min-height:480px; border-radius:34px; border:2px solid rgba(251,191,36,.50); background:linear-gradient(145deg,rgba(15,23,42,.76),rgba(8,47,73,.62)); box-shadow:0 40px 90px rgba(0,0,0,.55), inset 0 0 38px rgba(251,191,36,.08); overflow:hidden; transform-style:preserve-3d; perspective:1000px; }
.reelCard:before { content:""; position:absolute; inset:12px; border-radius:25px; border:1px dashed rgba(251,191,36,.25); pointer-events:none; }
.reelCard.spinning { border-color:rgba(45,212,191,.90); box-shadow:0 0 0 2px rgba(45,212,191,.18), 0 36px 88px rgba(0,0,0,.55), inset 0 0 54px rgba(45,212,191,.13); }
.reelCard.landed { animation: land .55s ease-out; border-color:rgba(251,191,36,.95); }
@keyframes land { 0%{transform:scale(1)} 35%{transform:scale(1.035)} 100%{transform:scale(1)} }
.reelTitle { position:absolute; top:22px; left:0; right:0; text-align:center; color:#fde68a; font-size:24px; font-weight:1000; letter-spacing:3px; text-transform:uppercase; }
.reelWindow { position:absolute; left:7%; right:7%; top:118px; height:230px; border-radius:24px; overflow:hidden; border:2px solid rgba(255,255,255,.18); background:rgba(0,0,0,.34); box-shadow:inset 0 20px 40px rgba(0,0,0,.50); }
.reelWindow:before, .reelWindow:after { content:""; position:absolute; left:0; right:0; height:80px; z-index:4; pointer-events:none; }
.reelWindow:before { top:0; background:linear-gradient(180deg,rgba(3,7,18,1),transparent); }
.reelWindow:after { bottom:0; background:linear-gradient(0deg,rgba(3,7,18,1),transparent); }
.payline { position:absolute; left:10px; right:10px; top:86px; height:58px; border-radius:14px; border:2px solid rgba(251,191,36,.9); z-index:3; box-shadow:0 0 30px rgba(251,191,36,.22); }
.reelItems { position:absolute; inset:0; display:flex; flex-direction:column; justify-content:center; transform-style:preserve-3d; }
.reelItem { height:58px; display:flex; align-items:center; justify-content:center; padding:0 16px; font-size:34px; font-weight:1000; text-align:center; color:#e5e7eb; opacity:.42; text-shadow:0 8px 24px rgba(0,0,0,.7); transform:translateZ(0); }
.reelItem.center { opacity:1; color:white; transform:scale(1.08) translateZ(36px); text-shadow:0 0 18px rgba(45,212,191,.55), 0 8px 24px rgba(0,0,0,.8); }
.reelCard.spinning .reelItem { filter:blur(1.2px); }
.resultText { position:absolute; left:20px; right:20px; bottom:26px; min-height:72px; text-align:center; font-size:28px; font-weight:1000; color:#fff; }
.flair { margin:0 5vw 14px; display:none; gap:18px; align-items:center; padding:18px 22px; border-radius:24px; border:2px solid rgba(255,255,255,.16); box-shadow:0 22px 60px rgba(0,0,0,.38); font-weight:950; }
.flair.show { display:flex; animation:pop .6s ease-out; }
@keyframes pop { 0%{transform:scale(.90); opacity:0} 70%{transform:scale(1.035)} 100%{transform:scale(1); opacity:1} }
.flairEmoji { font-size:48px; }
.flairTitle { font-size:34px; }
.flairText { font-size:18px; color:#e2e8f0; }
.flair.favourite { background:linear-gradient(135deg,rgba(251,191,36,.30),rgba(180,83,9,.24)); border-color:rgba(251,191,36,.80); }
.flair.longshot { background:linear-gradient(135deg,rgba(239,68,68,.25),rgba(124,58,237,.28)); border-color:rgba(248,113,113,.78); }
.flair.fancied { background:linear-gradient(135deg,rgba(56,189,248,.24),rgba(45,212,191,.22)); border-color:rgba(56,189,248,.75); }
.footer { padding:0 5vw 18px; display:grid; grid-template-columns:1fr 1fr; gap:20px; color:var(--muted); }
.tableBox { max-height:165px; overflow:auto; border-radius:18px; border:1px solid rgba(255,255,255,.12); background:rgba(2,6,23,.56); }
table { width:100%; border-collapse:collapse; }
th,td { padding:10px 12px; border-bottom:1px solid rgba(255,255,255,.08); text-align:left; }
th { color:#dbeafe; font-size:12px; text-transform:uppercase; letter-spacing:.8px; }
body.flash { animation:screenFlash .75s ease-out; }
@keyframes screenFlash { 0%{filter:brightness(1)} 18%{filter:brightness(2)} 100%{filter:brightness(1)} }
@media (max-width:1100px){ html, body{overflow:auto;} .stage{grid-template-columns:1fr;} .footer{grid-template-columns:1fr;} h1{font-size:34px;} }
</style>
</head>
<body>
<canvas id="gl"></canvas>
<div class="overlay">
<header><div class="headerRow"><h1>Melbourne Cup 3D Draw</h1><button id="soundBtn" class="soundBtn">Enable sound</button></div><div id="status" class="status">Waiting for the Sweeps admin app...</div></header>
__NAV__
<section class="stage">
  <div id="horseCard" class="reelCard"><div class="reelTitle">Horse Reel</div><div class="reelWindow"><div id="horseReel" class="reelItems"></div><div class="payline"></div></div><div id="horseResult" class="resultText">---</div></div>
  <div id="attendeeCard" class="reelCard"><div class="reelTitle">Attendee Reel</div><div class="reelWindow"><div id="attendeeReel" class="reelItems"></div><div class="payline"></div></div><div id="attendeeResult" class="resultText">---</div></div>
</section>
<div id="flair" class="flair"><div id="flairEmoji" class="flairEmoji"></div><div><div id="flairTitle" class="flairTitle"></div><div id="flairText" class="flairText"></div></div></div>
<section class="footer"><div><b id="sweepName">Cup Display</b><div id="progress">No draw revealed yet</div></div><div id="drawTable" class="tableBox">No revealed draws yet.</div></section>
</div>
<script>
let audioCtx=null, soundEnabled=false, lastSpinId=null, spinning=false, latestState=null, spinToken=0, glRot=0;
const $ = id => document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function horseText(o){ if(!o)return '---'; if(typeof o==='string')return o; return o.display || (`#${o.horse_number??'---'} ${o.horse_name??''}`).trim(); }
function attText(o){ if(!o)return '---'; return typeof o==='string' ? o : (o.attendee_name || o.display || '---'); }
function rnd(arr, fb='---'){ return (arr&&arr.length) ? arr[Math.floor(Math.random()*arr.length)] : fb; }
function rows(center,pool,fmt){ const r=[]; for(let i=0;i<7;i++) r.push(fmt(rnd(pool,center))); r[3]=center; return r; }
function setReel(id, values){ $(id).innerHTML = values.map((v,i)=>`<div class="reelItem ${i===3?'center':''}">${esc(v)}</div>`).join(''); }
function tone(freq,dur=.05,type='square',gain=.03){ if(!soundEnabled||!audioCtx)return; const now=audioCtx.currentTime, osc=audioCtx.createOscillator(), g=audioCtx.createGain(); osc.type=type; osc.frequency.value=freq; g.gain.setValueAtTime(gain,now); g.gain.exponentialRampToValueAtTime(.0001,now+dur); osc.connect(g); g.connect(audioCtx.destination); osc.start(now); osc.stop(now+dur); }
function tick(){ tone(430+Math.random()*80,.028,'square',.016); } function stopSound(){ tone(90,.10,'sine',.07); setTimeout(()=>tone(170,.06,'triangle',.04),70); } function chime(){ [523,659,784,1046].forEach((f,i)=>setTimeout(()=>tone(f,.13,'sine',.045),i*80)); } function roughie(){ [180,140,260,360].forEach((f,i)=>setTimeout(()=>tone(f,.13,'sawtooth',.04),i*90)); }
$('soundBtn').onclick=()=>{try{audioCtx=audioCtx||new(window.AudioContext||window.webkitAudioContext)(); audioCtx.resume(); soundEnabled=true; $('soundBtn').textContent='Sound enabled'; $('soundBtn').classList.add('enabled'); chime();}catch(e){$('soundBtn').textContent='Sound unavailable';}};
function animateReel(reelId, cardId, pool, finalText, ms, fmt, token){ return new Promise(resolve=>{ const start=performance.now(); $(cardId).classList.add('spinning'); function frame(now){ if(token!==spinToken)return; const p=Math.min(1,(now-start)/ms); const c = p<.9 ? fmt(rnd(pool,finalText)) : finalText; setReel(reelId, rows(c,pool,fmt)); if(Math.random()<.55) tick(); if(p<1) requestAnimationFrame(frame); else { setReel(reelId, rows(finalText,pool,fmt)); $(cardId).classList.remove('spinning'); $(cardId).classList.add('landed'); setTimeout(()=>$(cardId).classList.remove('landed'),600); stopSound(); resolve(); } } requestAnimationFrame(frame); }); }
function showFlair(f){ const box=$('flair'); if(!f||!f.title){box.className='flair'; return;} box.className='flair show '+(f.class||''); $('flairEmoji').textContent=f.emoji||'✨'; $('flairTitle').textContent=f.title; $('flairText').textContent=f.text||''; if((f.class||'').includes('longshot')) roughie(); else chime(); }
function fallbackSpeak(text){ if(!soundEnabled || !('speechSynthesis' in window) || !text) return; try{ const u=new SpeechSynthesisUtterance(text); u.rate=1.02; u.pitch=1.0; u.volume=1.0; window.speechSynthesis.cancel(); window.speechSynthesis.speak(u); }catch(e){} }
function stopVoicePromise(spin, part){ return fetch(`/api/cup_stop_tts?spin_id=${encodeURIComponent(spin.spin_id||'')}&part=${encodeURIComponent(part)}&ts=${Date.now()}`,{cache:'no-store'}).then(r=>r.json()).catch(e=>({ok:false,error:String(e)})); }
function playStopVoice(promise){ if(!soundEnabled) return; promise.then(data=>{ if(data && data.ok && data.url){ const a=new Audio(data.url); a.volume=1.0; a.play().catch(()=>fallbackSpeak(data.text||'')); } else if(data && data.text){ fallbackSpeak(data.text); } }).catch(()=>{}); }
async function runSpin(spin){ if(!spin||!spin.target)return; spinning=true; const token=++spinToken; const t=spin.target; const hp=spin.horse_options?.length?spin.horse_options:(latestState?.cup?.horse_options||[]); const ap=spin.attendee_options?.length?spin.attendee_options:(latestState?.cup?.attendee_options||[]); const h=horseText(t), a=attText(t); const horseVoice=stopVoicePromise(spin,'horse'); const attendeeVoice=stopVoicePromise(spin,'attendee'); $('sweepName').textContent=spin.sweep_label||'Cup Display'; $('progress').textContent=`Draw ${spin.draw_index||''} of ${spin.draw_total||''}`; $('status').textContent='Spinning in 3D... horse lands first, attendee lands second.'; $('horseResult').textContent='Spinning...'; $('attendeeResult').textContent='Spinning...'; showFlair({}); const hd=animateReel('horseReel','horseCard',hp,h,Number(spin.horse_duration_ms||3200),horseText,token); const ad=animateReel('attendeeReel','attendeeCard',ap,a,Number(spin.attendee_duration_ms||5200),attText,token); await hd; if(token!==spinToken)return; $('horseResult').textContent=h+' is locked in'; playStopVoice(horseVoice); await ad; if(token!==spinToken)return; $('attendeeResult').textContent=a; playStopVoice(attendeeVoice); $('status').textContent='Reveal complete.'; $('horseResult').textContent=h; document.body.classList.add('flash'); setTimeout(()=>document.body.classList.remove('flash'),750); showFlair(t.flair||{}); spinning=false; await fetchState(true); }
function renderTable(cup){ const drawn=cup.drawn_by_label||{}; let html=''; ['Cup $1','Cup $2','Cup $5'].forEach(label=>{ const rows=drawn[label]||[]; if(!rows.length)return; html += `<table><thead><tr><th colspan="4">${esc(label)}</th></tr><tr><th>#</th><th>Attendee</th><th>Horse</th><th>Type</th></tr></thead><tbody>`; rows.forEach((r,i)=> html += `<tr><td>${esc(r.draw_index||i+1)}</td><td>${esc(r.attendee_name||'')}</td><td>#${esc(r.horse_number||'')} ${esc(r.horse_name||'')}</td><td>${esc(r.allocation_type||'')}</td></tr>`); html+='</tbody></table>'; }); $('drawTable').innerHTML=html||'No revealed draws yet.'; }
function finalFromState(state){ const cup=state.cup||{}, r=cup.current_reveal||{}; if(r&&r.horse_number){ const h=`#${r.horse_number} ${r.horse_name||''}`.trim(); setReel('horseReel',rows(h,cup.horse_options||[],horseText)); setReel('attendeeReel',rows(r.attendee_name||'---',cup.attendee_options||[],attText)); $('horseResult').textContent=h; $('attendeeResult').textContent=r.attendee_name||'---'; $('sweepName').textContent=r.sweep_label||cup.selected_sweep||'Cup Display'; $('progress').textContent=`Draw ${r.draw_index||''} of ${r.draw_total||''}`; showFlair(r.flair||{}); } else { setReel('horseReel',rows('---',cup.horse_options||[],horseText)); setReel('attendeeReel',rows('---',cup.attendee_options||[],attText)); $('horseResult').textContent='---'; $('attendeeResult').textContent='---'; $('sweepName').textContent=cup.selected_sweep||'Cup Display'; $('progress').textContent='No draw revealed yet'; showFlair({}); } renderTable(cup); }
async function fetchState(force=false){ try{ const res=await fetch('/api/state?ts='+Date.now(),{cache:'no-store'}); const state=await res.json(); latestState=state; const spin=state.cup?.spin_event||{}; const reveal=state.cup?.current_reveal||{}; /* Do not interrupt the 3D wheel when the admin app records the reveal early. The PySide panel finishes its small local spinner faster than the big-screen web animation, so cutting over to finalFromState here makes both reels appear to stop together. Let runSpin() finish: horse first, attendee second. */ if(spin && spin.spin_id && spin.status==='spinning' && spin.spin_id!==lastSpinId){ lastSpinId=spin.spin_id; runSpin(spin); return;} if(!spinning||force){ finalFromState(state); $('status').textContent='Connected. Waiting for Draw next.';} }catch(e){ $('status').textContent='Waiting for the Sweeps admin app web server...'; } }

// Lightweight WebGL backdrop/cabinet lights, no external internet dependency.
(function initGL(){ const c=$('gl'), gl=c.getContext('webgl',{alpha:false,antialias:true}); if(!gl)return; function resize(){ c.width=innerWidth; c.height=innerHeight; gl.viewport(0,0,c.width,c.height);} addEventListener('resize',resize); resize(); const vs=`attribute vec2 p; varying vec2 v; void main(){v=p; gl_Position=vec4(p,0.0,1.0);}`; const fs=`precision mediump float; varying vec2 v; uniform float t; void main(){ vec2 uv=v; float r=length(uv); float glow=.08/max(.05,abs(sin(uv.x*7.0+t)*.08+r*.55)); float lamps=step(.94,abs(sin((atan(uv.y,uv.x)+t*.7)*24.0))) * smoothstep(.85,.35,r); vec3 col=mix(vec3(.01,.03,.08),vec3(.05,.10,.22),1.0-r); col += glow*vec3(.9,.55,.12); col += lamps*vec3(.1,.9,.85); gl_FragColor=vec4(col,1.0);}`; function sh(type,src){ const s=gl.createShader(type); gl.shaderSource(s,src); gl.compileShader(s); return s;} const pr=gl.createProgram(); gl.attachShader(pr,sh(gl.VERTEX_SHADER,vs)); gl.attachShader(pr,sh(gl.FRAGMENT_SHADER,fs)); gl.linkProgram(pr); gl.useProgram(pr); const buf=gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER,buf); gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,1,1]),gl.STATIC_DRAW); const loc=gl.getAttribLocation(pr,'p'); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0); const tl=gl.getUniformLocation(pr,'t'); function draw(now){ gl.uniform1f(tl,now*.001); gl.drawArrays(gl.TRIANGLE_STRIP,0,4); requestAnimationFrame(draw);} requestAnimationFrame(draw); })();
setReel('horseReel',rows('---',[],horseText)); setReel('attendeeReel',rows('---',[],attText)); fetchState(true); setInterval(fetchState,350);
</script>
</body>
</html>"""
    return template.replace("__NAV__", nav)

def render_web_money(main_window: MainWindow) -> str:
    rows = []
    for row in main_window.book.amount_owing_rows():
        rows.append([
            row["Attendee"],
            money(row["Normal Sweeps"]),
            money(row["Cup $1"]),
            money(row["Cup $2"]),
            money(row["Cup $5"]),
            money(row["Total Owing"]),
            "Yes" if row["Paid"] else "No",
        ])
    body = web_table(["Attendee", "Normal", "Cup $1", "Cup $2", "Cup $5", "Total", "Paid"], rows)
    return web_page(main_window, "Money Owing", body)


def render_web_payouts(main_window: MainWindow) -> str:
    rows = []
    for row in main_window.book.payout_rows():
        rows.append([
            row.race_number,
            row.sweep_label,
            f"{row.placing}{ordinal_suffix(row.placing)}",
            row.horse_number,
            row.horse_name,
            row.attendee_name,
            money(row.payout_cents),
            row.note,
        ])
    body = web_table(["Race", "Sweep", "Placing", "Horse No", "Horse", "Attendee", "Payout", "Note"], rows)
    return web_page(main_window, "Payout Winners", body)


def render_web_attendees(main_window: MainWindow) -> str:
    rows = [[a.attendee_id, a.name, bool_text(a.active), bool_text(a.cup_eligible), bool_text(a.paid)] for a in main_window.book.attendees]
    body = web_table(["ID", "Name", "Active", "Cup Eligible", "Paid"], rows)
    return web_page(main_window, "Attendees", body)


def ordinal_suffix(value: int) -> str:
    if 10 <= value % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")


def parse_money_to_cents(value: str) -> int:
    text = str(value or "").strip().replace("$", "").replace(",", "")
    if not text:
        return 0
    try:
        # Payout settings are labelled as cents, but this also accepts $2.50.
        if "." in text:
            return int(round(float(text) * 100))
        return int(float(text))
    except Exception:
        return 0


def export_print_sheets(book: SweepBook, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)

    write_csv(
        folder / "all_allocations.csv",
        ["Race", "Race Name", "Sweep", "Horse No", "Horse", "Attendee", "Type", "Price", "Result"],
        [
            [
                a.race_number,
                a.race_name,
                a.sweep_label,
                a.horse_number,
                a.horse_name,
                a.attendee_name,
                a.allocation_type,
                money(a.price_cents),
                a.result_position or "",
            ]
            for a in sorted(book.allocations, key=lambda x: (x.race_number, x.sweep_label, x.horse_number))
        ],
    )

    amount_rows = book.amount_owing_rows()
    write_csv(
        folder / "amounts_owing.csv",
        ["Attendee", "Normal Sweeps", "Cup $1", "Cup $2", "Cup $5", "Total Owing", "Paid"],
        [
            [
                row["Attendee"],
                money(row["Normal Sweeps"]),
                money(row["Cup $1"]),
                money(row["Cup $2"]),
                money(row["Cup $5"]),
                money(row["Total Owing"]),
                "Yes" if row["Paid"] else "No",
            ]
            for row in amount_rows
        ],
    )

    payout_rows = book.payout_rows()
    write_csv(
        folder / "payouts.csv",
        ["Race", "Race Name", "Sweep", "Place", "Horse No", "Horse", "Attendee", "Payout", "Eligible", "Note"],
        [
            [
                row.race_number,
                row.race_name,
                row.sweep_label,
                row.placing,
                row.horse_number,
                row.horse_name,
                row.attendee_name,
                money(row.payout_cents),
                "Yes" if row.eligible else "No",
                row.note,
            ]
            for row in payout_rows
        ],
    )

    write_csv(
        folder / "audit_log.csv",
        ["Time", "Action", "Details"],
        [[entry.timestamp, entry.action, entry.details] for entry in book.audit_log],
    )

    attendee_rows = []
    attendee_links = []
    attendees_folder = folder / "attendees"
    attendees_folder.mkdir(parents=True, exist_ok=True)
    for attendee in sorted(book.attendees, key=lambda a: a.name):
        attendee_allocations = sorted(
            [a for a in book.allocations if a.attendee_id == attendee.attendee_id],
            key=lambda x: (x.race_number, x.sweep_label, x.horse_number),
        )
        for allocation in attendee_allocations:
            attendee_rows.append([
                attendee.attendee_id,
                attendee.name,
                allocation.race_number,
                allocation.race_name,
                allocation.sweep_label,
                allocation.horse_number,
                allocation.horse_name,
                allocation.barrier,
                allocation.jockey,
                allocation.trainer,
                allocation.odds,
                money(allocation.price_cents),
                allocation.result_position or "",
                allocation.allocation_type,
            ])
        html_name = f"{safe_filename(attendee.name.lower()) or attendee.attendee_id.lower()}_sheet.html"
        write_attendee_card_html(book, attendee.attendee_id, attendees_folder / html_name)
        attendee_links.append((attendee.name, f"attendees/{html_name}"))

    write_csv(
        folder / "attendee_horses.csv",
        ["Attendee ID", "Attendee", "Race", "Race Name", "Sweep", "Horse No", "Horse", "Barrier", "Jockey", "Trainer", "Odds", "Price", "Result", "Type"],
        attendee_rows,
    )

    race_links = []
    for race_no in sorted(book.races):
        html_name = f"race_{race_no:02d}_cards.html"
        write_race_card_html(book, race_no, folder / html_name)
        race_links.append((f"Race {race_no}", html_name))

    write_index_html(book, folder / "index.html", race_links, attendee_links)


def write_csv(path: Path, headers: List[str], rows: List[List[object]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)



def safe_filename(value: str) -> str:
    safe = []
    for char in str(value or "").lower():
        if char.isalnum():
            safe.append(char)
        elif char in {" ", "-", "_"}:
            safe.append("_")
    text = "".join(safe).strip("_")
    while "__" in text:
        text = text.replace("__", "_")
    return text[:80]


def write_attendee_card_html(book: SweepBook, attendee_id: str, path: Path) -> None:
    attendee = book.attendee_by_id(attendee_id)
    if attendee is None:
        return
    allocations = sorted(
        [a for a in book.allocations if a.attendee_id == attendee.attendee_id],
        key=lambda x: (x.race_number, x.sweep_label, x.horse_number),
    )
    amount_row = next((row for row in book.amount_owing_rows() if row.get("Attendee ID") == attendee.attendee_id), None)
    total_owing = money(amount_row["Total Owing"]) if amount_row else "$0.00"
    paid_text = bool_text(attendee.paid)

    grouped: dict[int, List[Allocation]] = {}
    for allocation in allocations:
        grouped.setdefault(allocation.race_number, []).append(allocation)

    sections: List[str] = []
    for race_no in sorted(book.races):
        race = book.races[race_no]
        race_allocations = grouped.get(race_no, [])
        if not race_allocations:
            rows = '<tr><td colspan="7" class="empty">No horse allocated for this race.</td></tr>'
        else:
            rows = "\n".join(
                f"<tr><td>{html.escape(a.sweep_label)}</td><td>{a.horse_number}</td><td>{html.escape(a.horse_name)}</td><td>{html.escape(a.barrier)}</td><td>{html.escape(a.jockey)}</td><td>{html.escape(str(a.odds or ''))}</td><td>{money(a.price_cents)}</td></tr>"
                for a in race_allocations
            )
        sections.append(
            f"""<section>
<h2>Race {race_no}: {html.escape(race.race_name)}</h2>
<table>
<thead><tr><th>Sweep</th><th>No</th><th>Horse</th><th>Barrier</th><th>Jockey</th><th>Odds</th><th>Cost</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</section>"""
        )

    body = "\n".join(sections) if sections else "<p>No races imported yet.</p>"
    path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(attendee.name)} - Sweep Sheet</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 20px; color: #111; }}
h1 {{ font-size: 34px; margin-bottom: 4px; }}
.summary {{ display: flex; gap: 12px; margin: 14px 0 22px; }}
.card {{ border: 2px solid #111827; border-radius: 10px; padding: 12px 16px; min-width: 150px; }}
.card strong {{ display: block; font-size: 13px; color: #555; }}
.card span {{ font-size: 26px; font-weight: 800; }}
h2 {{ margin-top: 24px; background: #111827; color: white; padding: 10px 12px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 14px; page-break-inside: avoid; }}
th, td {{ border: 1px solid #999; padding: 8px 10px; font-size: 18px; }}
th {{ background: #e5e7eb; text-align: left; }}
.empty {{ color: #666; font-style: italic; }}
@media print {{ button {{ display: none; }} h2 {{ break-after: avoid; }} }}
</style>
</head>
<body>
<button onclick="window.print()">Print this attendee sheet</button>
<h1>{html.escape(attendee.name)}</h1>
<div class="summary">
  <div class="card"><strong>TOTAL OWING</strong><span>{html.escape(total_owing)}</span></div>
  <div class="card"><strong>PAID</strong><span>{html.escape(paid_text)}</span></div>
  <div class="card"><strong>HORSES</strong><span>{len(allocations)}</span></div>
</div>
{body}
</body>
</html>""",
        encoding="utf-8",
    )

def write_index_html(book: SweepBook, path: Path, race_links: List[tuple[str, str]], attendee_links: List[tuple[str, str]]) -> None:
    race_cards = "\n".join(f'<li><a href="{html.escape(href)}">{html.escape(label)}</a></li>' for label, href in race_links)
    attendee_cards = "\n".join(f'<li><a href="{html.escape(href)}">{html.escape(label)}</a></li>' for label, href in attendee_links)
    path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Sweep Print Pack</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 32px; }}
h1 {{ margin-bottom: 4px; }}
li {{ margin: 10px 0; font-size: 18px; }}
.note {{ color: #555; }}
</style>
</head>
<body>
<h1>Sweep Print Pack</h1>
<p class="note">Generated {html.escape(datetime.now().strftime("%Y-%m-%d %H:%M"))}</p>
<h2>Race sheets</h2>
<ul>
{race_cards}
</ul>
<h2>Individual attendee sheets</h2>
<ul>
{attendee_cards}
</ul>
<p>CSV files included: all_allocations.csv, attendee_horses.csv, amounts_owing.csv, payouts.csv, audit_log.csv</p>
</body>
</html>""",
        encoding="utf-8",
    )


def write_race_card_html(book: SweepBook, race_no: int, path: Path) -> None:
    race = book.races.get(race_no)
    if not race:
        return
    race_allocations = [a for a in book.allocations if a.race_number == race_no]
    labels: List[str] = []
    for allocation in race_allocations:
        if allocation.sweep_label not in labels:
            labels.append(allocation.sweep_label)

    sections = []
    for label in labels:
        allocations = sorted([a for a in race_allocations if a.sweep_label == label], key=lambda x: x.horse_number)
        rows = "\n".join(
            f"<tr><td>{html.escape(a.attendee_name)}</td><td>{a.horse_number}</td><td>{html.escape(a.horse_name)}</td><td>{html.escape(a.barrier)}</td><td>{html.escape(a.jockey)}</td></tr>"
            for a in allocations
        )
        locked = " 🔒" if book.is_sweep_locked(race_no, label) else ""
        sections.append(
            f"""<section>
<h2>{html.escape(label)}{locked}</h2>
<table>
<thead><tr><th>Attendee</th><th>No</th><th>Horse</th><th>Barrier</th><th>Jockey</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</section>"""
        )

    body = "\n".join(sections) if sections else "<p>No sweeps generated yet.</p>"
    path.write_text(
        f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Race {race_no} - {html.escape(race.race_name)}</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 20px; color: #111; }}
h1 {{ font-size: 30px; margin-bottom: 4px; }}
h2 {{ margin-top: 26px; background: #111827; color: white; padding: 10px 12px; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 18px; page-break-inside: avoid; }}
th, td {{ border: 1px solid #999; padding: 8px 10px; font-size: 18px; }}
th {{ background: #e5e7eb; text-align: left; }}
@media print {{ button {{ display: none; }} h2 {{ break-after: avoid; }} }}
</style>
</head>
<body>
<button onclick="window.print()">Print this race</button>
<h1>Race {race_no}: {html.escape(race.race_name)}</h1>
<p>{len(race.runners)} runners</p>
{body}
</body>
</html>""",
        encoding="utf-8",
    )



def spoken_sweep_label(label: str) -> str:
    replacements = {
        "Cup $1": "Cup one dollar sweep",
        "Cup $2": "Cup two dollar sweep",
        "Cup $5": "Cup five dollar sweep",
    }
    return replacements.get(label, label.replace("$", "dollar "))


def cup_voice_instructions() -> str:
    return (
        "You are voicing a Melbourne Cup office sweep draw. "
        "Use an upbeat race-day announcer delivery with more energy than normal narration. "
        "Sound clear, lively and slightly dramatic, but not cartoonish. "
        "Use short pauses around the horse number, horse name and attendee name. "
        "Put extra emphasis on the sweep value and final reveal. "
        "Do not read these instructions aloud."
    )


def openai_env_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "sweeps.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def get_openai_api_key() -> str:
    # Normal environment variable wins. If that is not set, use the app-local
    # data\\sweeps.env file Brad is already using. Do not hard-code keys.
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"].strip()
    return read_env_file(openai_env_path()).get("OPENAI_API_KEY", "").strip()


TTS_LAST_STATUS = ""


def set_tts_runtime_status(message: str) -> None:
    global TTS_LAST_STATUS
    TTS_LAST_STATUS = str(message).strip()


def openai_tts_status_text() -> str:
    env_path = openai_env_path()
    if get_openai_api_key():
        return f"OpenAI live TTS ready. Key loaded from {env_path}."
    return f"OpenAI key not found at {env_path}; will fall back to Windows voice."


def tts_runtime_status_text() -> str:
    if TTS_LAST_STATUS:
        return TTS_LAST_STATUS
    return openai_tts_status_text()


def announce_text(
    text: str,
    *,
    engine: str = "openai",
    voice: str = "marin",
    instructions: str = "",
    speed: float = 1.0,
) -> None:
    """Speak text without blocking the Qt UI.

    OpenAI live mode calls /v1/audio/speech for each announcement and plays the
    returned WAV. Windows/offline mode keeps the old SAPI fallback.
    """
    text = " ".join(str(text).split())
    if not text:
        return

    worker = threading.Thread(
        target=_announce_text_worker,
        args=(text, engine, voice, instructions, speed),
        daemon=True,
    )
    worker.start()


def _announce_text_worker(text: str, engine: str, voice: str, instructions: str, speed: float) -> None:
    try:
        if engine == "openai":
            if not get_openai_api_key():
                raise RuntimeError("OPENAI_API_KEY was not found. Check data\\sweeps.env.")
            set_tts_runtime_status("OpenAI TTS: generating audio...")
            audio_path = generate_openai_tts_wav(text, voice=voice, instructions=instructions, speed=speed)
            size = audio_path.stat().st_size if audio_path.exists() else 0
            set_tts_runtime_status(f"OpenAI TTS: generated {size:,} bytes. Playing {audio_path.name}...")
            play_audio_file(audio_path)
            set_tts_runtime_status(f"OpenAI TTS: played successfully. Saved copy: {audio_path}")
            return
    except Exception as error:
        # Keep the draw moving, but expose the exact failure in the Cup screen.
        set_tts_runtime_status(f"OpenAI TTS failed: {error}. Falling back to Windows voice.")

    announce_text_windows_fallback(text)


def ensure_tts_cache_dir() -> Path:
    folder = Path(__file__).resolve().parent / "data" / "tts_cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def generate_openai_tts_wav(text: str, *, voice: str, instructions: str, speed: float) -> Path:
    api_key = get_openai_api_key()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY was not found.")

    payload = {
        "model": "gpt-4o-mini-tts",
        "voice": voice or "marin",
        "input": text,
        "instructions": instructions or cup_voice_instructions(),
        "response_format": "wav",
        "speed": float(speed or 1.0),
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    output_path = ensure_tts_cache_dir() / f"openai_tts_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.wav"

    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            if not body:
                raise RuntimeError("OpenAI returned an empty audio response.")
            output_path.write_bytes(body)
            repair_streaming_wav_header(output_path)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="ignore")
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"HTTP {error.code} {detail}") from error
    except Exception:
        output_path.unlink(missing_ok=True)
        raise

    return output_path


def repair_streaming_wav_header(path: Path) -> bool:
    """Fix OpenAI streaming WAV headers so Windows can play them.

    Some OpenAI WAV responses are valid streaming PCM but use 0xFFFFFFFF for
    the RIFF/data chunk lengths. Browsers/media players often tolerate that,
    while Windows winsound/System.Media.SoundPlayer can refuse to play it.
    This rewrites the two length fields to match the finished file.
    """
    path = Path(path)
    try:
        data = bytearray(path.read_bytes())
    except Exception:
        return False

    if len(data) < 44 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False

    changed = False
    file_size = len(data)

    # RIFF chunk size is file size minus the 8 bytes used by "RIFF" and size.
    riff_size = file_size - 8
    current_riff_size = int.from_bytes(data[4:8], "little", signed=False)
    if current_riff_size != riff_size and riff_size <= 0xFFFFFFFF:
        data[4:8] = riff_size.to_bytes(4, "little", signed=False)
        changed = True

    # Find the data chunk and write the actual remaining PCM byte count.
    data_pos = -1
    pos = 12
    while pos + 8 <= file_size:
        chunk_id = bytes(data[pos:pos + 4])
        chunk_size = int.from_bytes(data[pos + 4:pos + 8], "little", signed=False)
        if chunk_id == b"data":
            data_pos = pos
            break

        # A streaming/unknown chunk length makes normal walking impossible.
        # Fall back to a direct search for the next data marker.
        next_pos = pos + 8 + chunk_size + (chunk_size % 2)
        if chunk_size == 0xFFFFFFFF or next_pos <= pos or next_pos > file_size:
            data_pos = data.find(b"data", pos)
            break
        pos = next_pos

    if data_pos >= 0 and data_pos + 8 <= file_size:
        actual_data_size = file_size - (data_pos + 8)
        current_data_size = int.from_bytes(data[data_pos + 4:data_pos + 8], "little", signed=False)
        if current_data_size != actual_data_size and actual_data_size <= 0xFFFFFFFF:
            data[data_pos + 4:data_pos + 8] = actual_data_size.to_bytes(4, "little", signed=False)
            changed = True

    if changed:
        path.write_bytes(data)
    return changed


def play_audio_file(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Audio file does not exist: {path}")

    if sys.platform.startswith("win"):
        # OpenAI WAV files can be delivered with streaming placeholder lengths.
        # Repair them before handing the file to the Windows audio APIs.
        header_repaired = False
        if path.suffix.lower() == ".wav":
            header_repaired = repair_streaming_wav_header(path)
        # Prefer Python's native winsound for WAV. The earlier PowerShell-only
        # playback path could fail silently on some Windows installs.
        if path.suffix.lower() == ".wav":
            try:
                import winsound
                winsound.PlaySound(str(path), winsound.SND_FILENAME)
                if header_repaired:
                    set_tts_runtime_status(f"OpenAI TTS: WAV header repaired and played. Saved copy: {path}")
                return
            except Exception as error:
                first_error = error
        else:
            first_error = RuntimeError("Not a WAV file, skipping winsound.")

        safe_path = str(path).replace("'", "''")
        command = (
            f"$player = New-Object System.Media.SoundPlayer '{safe_path}'; "
            "$player.Load(); "
            "$player.PlaySync();"
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
            timeout=60,
            check=False,
            text=True,
        )
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout or str(first_error)).strip()
        raise RuntimeError(f"Windows could not play the OpenAI WAV file. {detail}")

    if sys.platform == "darwin":
        result = subprocess.run(["afplay", str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "afplay failed")
        return

    errors = []
    for player in (["paplay", str(path)], ["aplay", str(path)]):
        try:
            result = subprocess.run(player, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=False, text=True)
            if result.returncode == 0:
                return
            errors.append(result.stderr or result.stdout)
        except FileNotFoundError as error:
            errors.append(str(error))
    raise RuntimeError("No working audio player found. " + " | ".join(errors))


def announce_text_windows_fallback(text: str) -> None:
    try:
        if sys.platform.startswith("win"):
            safe_text = text.replace("'", "''")
            command = (
                "$voice = New-Object -ComObject SAPI.SpVoice; "
                "$voice.Rate = 0; "
                "$voice.Volume = 100; "
                f"$voice.Speak('{safe_text}') | Out-Null"
            )
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", command],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                timeout=60,
                check=False,
            )
        elif sys.platform == "darwin":
            subprocess.run(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
        else:
            subprocess.run(["spd-say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)
    except Exception:
        # Voice is a convenience feature. If the host PC has no speech engine,
        # the draw still works normally.
        return


def allocation_table(allocations: List[Allocation]) -> QTableWidget:
    headers = ["Horse No", "Horse", "Attendee", "Type", "Barrier", "Jockey", "Trainer", "Result"]
    table = QTableWidget(len(allocations), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    for row, allocation in enumerate(sorted(allocations, key=lambda a: a.horse_number)):
        values = [
            allocation.horse_number,
            allocation.horse_name,
            allocation.attendee_name,
            allocation.allocation_type,
            allocation.barrier,
            allocation.jockey,
            allocation.trainer,
            allocation.result_position or "",
        ]
        for col, value in enumerate(values):
            table.setItem(row, col, table_item(value))
    table.resizeColumnsToContents()
    table.setSortingEnabled(True)
    return table


def payout_table(rows) -> QTableWidget:
    headers = ["Race", "Sweep", "Place", "Horse No", "Horse", "Attendee", "Payout", "Eligible", "Note"]
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    for row_index, row in enumerate(rows):
        values = [
            row.race_number,
            row.sweep_label,
            row.placing,
            row.horse_number,
            row.horse_name,
            row.attendee_name,
            money(row.payout_cents),
            "Yes" if row.eligible else "No",
            row.note,
        ]
        for col, value in enumerate(values):
            table.setItem(row_index, col, table_item(value))
    table.resizeColumnsToContents()
    table.setSortingEnabled(True)
    return table


def empty_message_table(message: str) -> QTableWidget:
    table = QTableWidget(1, 1)
    table.setHorizontalHeaderLabels(["Status"])
    table.setItem(0, 0, table_item(message))
    table.resizeColumnsToContents()
    return table


def table_item(value) -> QTableWidgetItem:
    item = QTableWidgetItem(str(value))
    item.setFlags(item.flags() | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
    return item


def cell_text(table: QTableWidget, row: int, col: int) -> str:
    item = table.item(row, col)
    return item.text() if item else ""


def bool_text(value: bool) -> str:
    return "Yes" if value else "No"


def parse_bool_text(value: str) -> bool:
    return str(value).strip().lower() in {"yes", "y", "true", "1", "paid", "active", "eligible"}


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()
        if widget:
            widget.deleteLater()
        if child_layout:
            clear_layout(child_layout)


def main() -> int:
    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
