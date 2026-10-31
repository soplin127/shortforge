"""GUI entry point: python app.py"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 не установлен: pip install PySide6", file=sys.stderr)
        return 1
    from gui.main_window import MainWindow
    from gui.theme import install_theme

    app = QApplication(sys.argv)
    app.setApplicationName("ShortForge")
    install_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
