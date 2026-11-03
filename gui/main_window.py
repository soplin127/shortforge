"""Main window: sidebar navigation over the preset sections, preset menu and persisted state."""
from __future__ import annotations

import json
from pathlib import Path

from collections import Counter

from PySide6.QtCore import QByteArray, QSettings, QSize, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import (
    ConfigError,
    Preset,
    load_preset,
    preset_from_dict,
    preset_to_dict,
    save_preset,
)
from core.ffmpeg import FFmpegNotFound, locate_tools
from gui.ads_tab import AdsTab
from gui.files_tab import FilesTab
from gui.forms import SectionForm, metadata_groups, music_groups, subtitle_groups, uniq_groups
from gui.queue_tab import STATUS_LABELS, JobRequest, QueueTab

APP_TITLE = "ShortForge"
PRESET_FILTER = "Пресеты (*.json);;Все файлы (*)"
APP_TAGLINE = "уникализация Shorts и TikTok"
# Pre-rename identifier, kept so saved settings survive the rename to ShortForge.
SETTINGS_ID = "shorts-uniq"
KEY_PRESET, KEY_FILES, KEY_GEOMETRY, KEY_PAGE = "preset_json", "files", "geometry", "page"
DEFAULT_SIZE = (1180, 820)
MIN_SIZE = (900, 600)
SIDEBAR_WIDTH = 210
NAV_ITEM_HEIGHT = 38
PAGE_MARGINS = (28, 22, 28, 16)
TOOLS_RECHECK_MS = 600
STATUS_MESSAGE_MS = 15_000
PAGES = (
    ("files", "Файлы", "Исходные видео и куда сохранять результат"),
    ("uniq", "Уникализация", "Что меняется в картинке, звуке и времени"),
    ("meta", "Метаданные", "Дата съёмки и название в контейнере"),
    ("music", "Музыка", "Фоновый трек поверх оригинала"),
    ("subs", "Субтитры", "Готовые файлы или распознавание речи"),
    ("ads", "Реклама", "Ролики, оверлеи и баннеры"),
    ("queue", "Очередь", "Запуск, прогресс и журнал"),
)


class MainWindow(QMainWindow):
    def __init__(self, settings: QSettings | None = None, check_tools: bool = True,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings or QSettings(SETTINGS_ID, SETTINGS_ID)
        self.setWindowTitle(APP_TITLE)
        self.resize(*DEFAULT_SIZE)
        self.setMinimumSize(*MIN_SIZE)
        self._base = Preset()  # keeps preset fields that no tab edits

        self.files = FilesTab()
        self.uniq = SectionForm(uniq_groups())
        self.meta = SectionForm(metadata_groups())
        self.music = SectionForm(music_groups())
        self.subs = SectionForm(subtitle_groups())
        self.ads = AdsTab()
        self.queue = QueueTab(self.request_job)
        self._editors = (self.files, self.uniq, self.meta, self.music, self.subs, self.ads,
                         self.queue)

        self._build_layout()
        self._build_status_bar()
        self._build_menu()
        self._build_shortcuts()
        self._restore_state()
        self.queue.batch_finished.connect(self._on_batch_finished)
        if check_tools:
            QTimer.singleShot(0, self._check_tools)
            self._tools_timer = QTimer(self, singleShot=True, interval=TOOLS_RECHECK_MS)
            self._tools_timer.timeout.connect(lambda: self._check_tools(quiet=True))
            self.queue.form.field("batch", "ffmpeg_path").connect(self._tools_timer.start)

    # ---------------------------------------------------------------- layout

    def _build_layout(self) -> None:
        self.nav = QListWidget()
        self.nav.setObjectName("sidebar")
        self.pages = QStackedWidget()
        for attr, title, _ in PAGES:
            self.nav.addItem(title)
            self.nav.item(self.nav.count() - 1).setSizeHint(QSize(0, NAV_ITEM_HEIGHT))
            self.pages.addWidget(getattr(self, attr))
        self.page_title = QLabel()
        self.page_title.setObjectName("pageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setObjectName("pageSubtitle")
        self.nav.currentRowChanged.connect(self._show_page)

        brand = QLabel(APP_TITLE)
        brand.setObjectName("brand")
        tagline = QLabel(APP_TAGLINE)
        tagline.setObjectName("brandTagline")
        sidebar = QFrame()
        sidebar.setObjectName("sidebarPanel")
        sidebar.setFixedWidth(SIDEBAR_WIDTH)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 12)
        side_layout.setSpacing(0)
        side_layout.addWidget(brand)
        side_layout.addWidget(tagline)
        side_layout.addWidget(self.nav, 1)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(*PAGE_MARGINS)
        content_layout.setSpacing(2)
        content_layout.addWidget(self.page_title)
        content_layout.addWidget(self.page_subtitle)
        content_layout.addSpacing(14)
        content_layout.addWidget(self.pages, 1)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sidebar)
        layout.addWidget(content, 1)
        self.setCentralWidget(central)
        self.nav.setCurrentRow(0)

    def _show_page(self, row: int) -> None:
        if not 0 <= row < len(PAGES):
            return
        _, title, subtitle = PAGES[row]
        self.pages.setCurrentIndex(row)
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)

    def _build_status_bar(self) -> None:
        self.tools_label = QLabel("ffmpeg: проверка…")
        self.statusBar().addPermanentWidget(self.tools_label)
        self.statusBar().setSizeGripEnabled(False)

    def _build_shortcuts(self) -> None:
        for row in range(len(PAGES)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{row + 1}"), self)
            shortcut.activated.connect(lambda r=row: self.nav.setCurrentRow(r))

    # ---------------------------------------------------------------- preset

    def load_preset(self, preset: Preset) -> None:
        self._base = preset
        for editor in self._editors:
            editor.load(preset)

    def current_preset(self) -> Preset:
        preset = self._base
        for editor in self._editors:
            preset = editor.apply(preset)
        return preset

    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("Пресет")
        menu.addAction("Загрузить…", QKeySequence.StandardKey.Open, self._open_preset)
        menu.addAction("Сохранить как…", QKeySequence.StandardKey.Save, self._save_preset)
        menu.addSeparator()
        menu.addAction("Сбросить", lambda: self.load_preset(Preset()))

    def _open_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить пресет", "", PRESET_FILTER)
        if not path:
            return
        try:
            self.load_preset(load_preset(path))
        except (ConfigError, OSError, ValueError) as exc:
            self.show_error(f"Не удалось загрузить пресет:\n{exc}")

    def _save_preset(self) -> None:
        try:
            preset = self.current_preset()
        except ConfigError as exc:
            self.show_error(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить пресет", "preset.json",
                                              PRESET_FILTER)
        if not path:
            return
        try:
            save_preset(preset, path)
        except OSError as exc:
            self.show_error(f"Не удалось сохранить пресет:\n{exc}")

    # ------------------------------------------------------------------ jobs

    def show_error(self, message: str) -> None:
        QMessageBox.warning(self, APP_TITLE, message)

    def request_job(self, preview: bool) -> JobRequest | None:
        try:
            preset = self.current_preset()
        except ConfigError as exc:
            self.show_error(str(exc))
            return None
        if not preset.output.output_dir:
            self.show_error("Укажите папку результата на вкладке «Файлы».")
            return None
        sources = self.files.sources(preset.output.recursive, preset.output.output_dir)
        if not sources:
            self.show_error("Нет видео для обработки: добавьте файлы или папки на вкладке «Файлы».")
            return None
        if preview:
            sources = sources[:1]
        try:
            tools = locate_tools(preset.batch.ffmpeg_path)
        except FFmpegNotFound as exc:
            self.show_error(str(exc))
            return None
        return tools, preset, sources, Path(preset.output.output_dir)

    def _check_tools(self, quiet: bool = False) -> None:
        """Show which ffmpeg will be used; a dialog only on the first (non-quiet) check."""
        try:
            tools = locate_tools(self.queue.form.field("batch", "ffmpeg_path").get())
        except FFmpegNotFound as exc:
            self.tools_label.setText("ffmpeg не найден — укажите путь на странице «Очередь»")
            if not quiet:
                self.show_error(str(exc))
            return
        self.tools_label.setText(f"ffmpeg: {tools.ffmpeg}")

    def _on_batch_finished(self, results) -> None:
        if not results:
            return
        counts = Counter(result.status for result in results)
        summary = ", ".join(f"{STATUS_LABELS.get(status, status).lower()}: {n}"
                            for status, n in counts.items())
        self.statusBar().showMessage(f"Обработка завершена — {summary}", STATUS_MESSAGE_MS)

    # ------------------------------------------------------------ persistence

    def _restore_state(self) -> None:
        geometry = self.settings.value(KEY_GEOMETRY)
        if isinstance(geometry, QByteArray):
            self.restoreGeometry(geometry)
        preset = Preset()
        raw = self.settings.value(KEY_PRESET)
        if raw:
            try:
                preset = preset_from_dict(json.loads(raw))
            except (ConfigError, ValueError, TypeError):
                preset = Preset()
        self.load_preset(preset)
        files = self.settings.value(KEY_FILES) or []
        self.files.add_paths([files] if isinstance(files, str) else list(files))
        try:
            page = int(self.settings.value(KEY_PAGE, 0))
        except (TypeError, ValueError):
            page = 0
        self.nav.setCurrentRow(page if 0 <= page < len(PAGES) else 0)

    def _save_state(self) -> None:
        self.settings.setValue(KEY_GEOMETRY, self.saveGeometry())
        self.settings.setValue(KEY_FILES, self.files.paths())
        self.settings.setValue(KEY_PAGE, self.nav.currentRow())
        try:
            preset = self.current_preset()
        except ConfigError:
            preset = None  # keep the last valid preset rather than storing a broken one
        if preset is not None:
            self.settings.setValue(KEY_PRESET, json.dumps(preset_to_dict(preset),
                                                          ensure_ascii=False))
        self.settings.sync()

    def closeEvent(self, event) -> None:
        if self.queue.running:
            answer = QMessageBox.question(self, APP_TITLE,
                                          "Обработка ещё идёт. Остановить и выйти?")
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.queue.stop_and_wait()
        self._save_state()
        super().closeEvent(event)
