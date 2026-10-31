"""Application look: Fusion style, a light/dark palette that follows the OS, and a stylesheet.

Fusion renders the same on Windows, macOS and Linux, so one palette and one stylesheet
give a consistent look everywhere.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#6366F1"
ACCENT_HOVER = {False: "#4F46E5", True: "#818CF8"}
DANGER = "#E5484D"
SUCCESS = "#22A35A"
WARNING = "#D98E04"

LIGHT = {
    "window": "#F4F5F7",
    "sidebar": "#ECEDF1",
    "card": "#FFFFFF",
    "base": "#FFFFFF",
    "alt": "#F7F8FA",
    "text": "#1F2328",
    "muted": "#6B7080",
    "disabled": "#A3A7B3",
    "border": "#DCDFE5",
    "hover": "#E2E4EA",
    "track": "#E4E6EB",
    "accent_soft": "#E0E1FC",
}
DARK = {
    "window": "#1B1C20",
    "sidebar": "#151619",
    "card": "#232429",
    "base": "#2A2B31",
    "alt": "#26272C",
    "text": "#E7E8EB",
    "muted": "#9A9DA8",
    "disabled": "#5E616B",
    "border": "#383A41",
    "hover": "#30323A",
    "track": "#34363D",
    "accent_soft": "#34356B",
}


def colors(dark: bool) -> dict[str, str]:
    return DARK if dark else LIGHT


def is_dark(palette: QPalette) -> bool:
    return palette.color(QPalette.ColorRole.Window).lightness() < 128


def system_prefers_dark(app: QGuiApplication) -> bool:
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return is_dark(app.palette())


def build_palette(dark: bool) -> QPalette:
    c = colors(dark)
    role = QPalette.ColorRole
    palette = QPalette()
    for r, key in ((role.Window, "window"), (role.WindowText, "text"), (role.Base, "base"),
                   (role.AlternateBase, "alt"), (role.Text, "text"), (role.Button, "card"),
                   (role.ButtonText, "text"), (role.ToolTipBase, "card"),
                   (role.ToolTipText, "text"), (role.PlaceholderText, "muted"),
                   (role.Mid, "border"), (role.Midlight, "hover"), (role.Dark, "border"),
                   (role.Light, "card"), (role.Shadow, "border")):
        palette.setColor(r, QColor(c[key]))
    palette.setColor(role.Highlight, QColor(ACCENT))
    palette.setColor(role.HighlightedText, QColor("#FFFFFF"))
    palette.setColor(role.Link, QColor(ACCENT))
    disabled = QPalette.ColorGroup.Disabled
    for r in (role.Text, role.WindowText, role.ButtonText):
        palette.setColor(disabled, r, QColor(c["disabled"]))
    return palette


def build_stylesheet(dark: bool) -> str:
    c = {**colors(dark), "accent": ACCENT, "accent_hover": ACCENT_HOVER[dark],
         "danger": DANGER}
    return """
#sidebarPanel { background: %(sidebar)s; border-right: 1px solid %(border)s; }
#brand { color: %(text)s; font-size: 17px; font-weight: 700; padding: 18px 18px 2px 18px; }
#brandTagline { color: %(muted)s; font-size: 11px; padding: 0 18px 12px 18px; }
#sidebar { background: transparent; border: none; outline: 0; }
#sidebar::item { color: %(text)s; padding: 9px 14px; margin: 1px 10px; border-radius: 8px; }
#sidebar::item:hover { background: %(hover)s; }
#sidebar::item:selected { background: %(accent)s; color: #FFFFFF; }

#pageTitle { color: %(text)s; font-size: 22px; font-weight: 700; }
#pageSubtitle { color: %(muted)s; }
#hint { color: %(muted)s; font-size: 12px; }
#error { color: %(danger)s; }

#form, #formInner { background: transparent; border: none; }
QGroupBox { background: %(card)s; border: 1px solid %(border)s; border-radius: 10px;
            margin-top: 22px; padding: 14px 14px 10px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
                   left: 4px; top: 2px; color: %(text)s; }

QPushButton { background: %(card)s; color: %(text)s; border: 1px solid %(border)s;
              border-radius: 7px; padding: 6px 14px; }
QPushButton:hover { border-color: %(accent)s; }
QPushButton:pressed { background: %(hover)s; }
QPushButton:disabled { color: %(disabled)s; background: %(window)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: #FFFFFF; border-color: %(accent)s;
                      font-weight: 600; }
QPushButton#primary:hover { background: %(accent_hover)s; border-color: %(accent_hover)s; }
QPushButton#primary:disabled { background: %(track)s; border-color: %(track)s;
                               color: %(disabled)s; }
QPushButton#danger { color: %(danger)s; }
QPushButton#danger:hover { border-color: %(danger)s; }
QPushButton#danger:disabled { color: %(disabled)s; }

QProgressBar { background: %(track)s; border: none; border-radius: 4px;
               min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: %(accent)s; border-radius: 4px; }

QListWidget, QTableWidget, QPlainTextEdit { background: %(base)s; border: 1px solid %(border)s;
                                            border-radius: 8px; }
QListWidget { padding: 4px; }
QListWidget::item { padding: 5px 6px; border-radius: 5px; }
QListWidget::item:selected { background: %(accent_soft)s; color: %(text)s; }
QTableWidget { gridline-color: transparent; alternate-background-color: %(alt)s; }
QTableWidget::item { padding: 0 6px; }
QTableWidget::item:selected { background: %(accent_soft)s; color: %(text)s; }
QHeaderView::section { background: %(card)s; color: %(muted)s; border: none;
                       border-bottom: 1px solid %(border)s; padding: 6px 8px; font-weight: 600; }
QPlainTextEdit { padding: 6px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle { background: %(border)s; border-radius: 3px; }
QScrollBar::handle:vertical { min-height: 32px; }
QScrollBar::handle:horizontal { min-width: 32px; }
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }

QSplitter::handle { background: transparent; }
QSplitter::handle:vertical { height: 8px; }
QSplitter::handle:horizontal { width: 8px; }

QStatusBar { background: %(sidebar)s; color: %(muted)s; border-top: 1px solid %(border)s; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: %(muted)s; padding: 0 8px; }
QToolTip { background: %(card)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
""" % c


def apply_theme(app: QApplication, dark: bool | None = None) -> bool:
    """Apply the look to the whole application; returns whether the dark variant is used."""
    if dark is None:
        dark = system_prefers_dark(app)
    app.setStyle("Fusion")
    app.setPalette(build_palette(dark))
    app.setStyleSheet(build_stylesheet(dark))
    return dark


def install_theme(app: QApplication) -> None:
    """Apply the theme now and re-apply it when the OS switches between light and dark."""
    apply_theme(app)
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: apply_theme(app))
