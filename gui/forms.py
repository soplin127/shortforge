"""Declarative forms: groups of fields mapped onto preset sections."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config import (
    HW_ACCEL_CHOICES,
    SUB_POSITIONS,
    WHISPER_MODELS,
    X264_PRESETS,
    ConfigError,
    Preset,
)
from gui.fields import (
    CheckField,
    ChoiceField,
    ColorField,
    Field,
    FloatField,
    IntField,
    ListField,
    PathField,
    RangeField,
    TextField,
)

Spec = tuple[str, str, Field]  # (dataclass attribute, label, field)

AUDIO_FILTER = "Аудио (*.mp3 *.m4a *.aac *.wav *.ogg *.flac);;Все файлы (*)"
FFMPEG_FILTER = "ffmpeg (ffmpeg ffmpeg.exe);;Все файлы (*)"
MAX_WORKERS = 64
LABEL_MIN_WIDTH = 200
FORM_H_SPACING = 18
FORM_V_SPACING = 8
FORM_MAX_WIDTH = 860
FORM_GROUP_SPACING = 4


class FieldGroup:
    """Titled set of fields bound to one dataclass (a preset section or an AdSlot).

    ``toggles`` maps a CheckField attribute to the attributes it switches on; dependent
    rows are disabled while their toggle is off. List outer toggles before nested ones.
    """

    def __init__(self, title: str, section: str, specs: list[Spec],
                 toggles: dict[str, tuple[str, ...]] | None = None, hint: str = ""):
        self.title = title
        self.section = section
        self.specs = specs
        self.fields = {attr: field for attr, _, field in specs}
        self.toggles = toggles or {}
        self.hint = hint
        self._labels: dict[str, QLabel] = {}

    def build(self) -> QGroupBox:
        box = QGroupBox(self.title)
        layout = QFormLayout(box)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        layout.setHorizontalSpacing(FORM_H_SPACING)
        layout.setVerticalSpacing(FORM_V_SPACING)
        if self.hint:
            hint = QLabel(self.hint)
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            layout.addRow(hint)
        for attr, text, field in self.specs:
            label = QLabel(text)
            label.setMinimumWidth(LABEL_MIN_WIDTH)
            self._labels[attr] = label
            layout.addRow(label, field.widget)
        for attr in self.toggles:
            self.fields[attr].connect(self.sync_toggles)
        self.sync_toggles()
        return box

    def sync_toggles(self) -> None:
        enabled: dict[str, bool] = {}
        for toggle, dependents in self.toggles.items():
            on = enabled.get(toggle, True) and bool(self.fields[toggle].get())
            for attr in dependents:
                enabled[attr] = enabled.get(attr, True) and on
        for attr, state in enabled.items():
            self.fields[attr].widget.setEnabled(state)
            if attr in self._labels:
                self._labels[attr].setEnabled(state)

    def load(self, obj) -> None:
        for attr, field in self.fields.items():
            field.set(getattr(obj, attr))
        self.sync_toggles()

    def values(self) -> dict:
        try:
            return {attr: field.get() for attr, field in self.fields.items()}
        except ConfigError as exc:
            raise ConfigError(f"{self.title}: {exc}") from exc

    def connect(self, callback) -> None:
        for field in self.fields.values():
            field.connect(callback)


class SectionForm(QScrollArea):
    """Scrollable stack of field groups; ``apply`` returns an updated preset copy."""

    changed = Signal()

    def __init__(self, groups: list[FieldGroup], parent: QWidget | None = None):
        super().__init__(parent)
        self.groups = groups
        self.setObjectName("form")
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("formInner")
        inner.setMaximumWidth(FORM_MAX_WIDTH)
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 8)
        layout.setSpacing(FORM_GROUP_SPACING)
        for group in groups:
            layout.addWidget(group.build())
            group.connect(self.changed.emit)
        layout.addStretch(1)
        self.setWidget(inner)
        self.setWidgetResizable(True)

    def field(self, section: str, attr: str) -> Field:
        for group in self.groups:
            if group.section == section and attr in group.fields:
                return group.fields[attr]
        raise KeyError(f"{section}.{attr}")

    def load(self, obj) -> None:
        """Fill from a preset, or from the object itself for section-less groups."""
        for group in self.groups:
            group.load(getattr(obj, group.section) if group.section else obj)

    def apply(self, preset: Preset) -> Preset:
        sections: dict = {}
        for group in self.groups:
            current = sections.get(group.section, getattr(preset, group.section))
            values = group.values()
            try:
                sections[group.section] = replace(current, **values)
            except ConfigError as exc:
                raise ConfigError(f"{group.title}: {exc}") from exc
        return replace(preset, **sections)


# ------------------------------------------------------------------ specs


def _percent(lo: float = 0.0, hi: float = 20.0) -> RangeField:
    return RangeField(lo, hi, 0.5, 1, " %")


def _db(lo: float = -10.0, hi: float = 10.0) -> RangeField:
    return RangeField(lo, hi, 0.5, 1, " дБ")


def _factor() -> RangeField:
    return RangeField(0.5, 1.5, 0.01, 2)


def _seconds(hi: float = 10.0) -> FloatField:
    return FloatField(0.0, hi, 0.1, 1, " с")


def _even_size() -> IntField:
    field = IntField(64, 4096)
    field.widget.setSingleStep(2)
    return field


def uniq_groups() -> list[FieldGroup]:
    return [
        FieldGroup("Разрешение и кадрирование", "uniq", [
            ("resolution_enabled", "Включено", CheckField()),
            ("target_width", "Ширина", _even_size()),
            ("target_height", "Высота", _even_size()),
            ("scale_pct", "Масштаб", _percent()),
            ("zoom_pct", "Кроп-зум", _percent()),
        ], toggles={"resolution_enabled": ("target_width", "target_height", "scale_pct",
                                           "zoom_pct")}),
        FieldGroup("Цвет", "uniq", [
            ("color_enabled", "Включено", CheckField()),
            ("brightness", "Яркость", RangeField(-0.2, 0.2, 0.005, 3)),
            ("contrast", "Контраст", _factor()),
            ("saturation", "Насыщенность", _factor()),
            ("gamma", "Гамма", _factor()),
            ("hue_deg", "Оттенок", RangeField(-30.0, 30.0, 0.5, 1, "°")),
            ("noise_enabled", "Шум", CheckField()),
            ("noise_strength", "Сила шума", RangeField(0.0, 30.0, 0.5, 1)),
            ("vignette_enabled", "Виньетка", CheckField()),
            ("vignette_angle", "Угол виньетки", RangeField(0.0, 1.2, 0.01, 2)),
        ], toggles={
            "color_enabled": ("brightness", "contrast", "saturation", "gamma", "hue_deg",
                              "noise_enabled", "noise_strength", "vignette_enabled",
                              "vignette_angle"),
            "noise_enabled": ("noise_strength",),
            "vignette_enabled": ("vignette_angle",),
        }),
        FieldGroup("Звук", "uniq", [
            ("audio_enabled", "Громкость", CheckField()),
            ("volume_db", "Изменение громкости", _db()),
            ("eq_enabled", "Эквалайзер", CheckField()),
            ("eq_gain_db", "Усиление полосы", _db()),
            ("pitch_enabled", "Тон", CheckField()),
            ("pitch_pct", "Сдвиг тона", RangeField(-10.0, 10.0, 0.1, 1, " %")),
        ], toggles={"audio_enabled": ("volume_db",), "eq_enabled": ("eq_gain_db",),
                    "pitch_enabled": ("pitch_pct",)}),
        FieldGroup("Время", "uniq", [
            ("time_enabled", "Включено", CheckField()),
            ("speed", "Скорость", RangeField(0.8, 1.2, 0.005, 3, "×")),
            ("trim_start", "Обрезка начала", RangeField(0.0, 3.0, 0.05, 2, " с")),
            ("trim_end", "Обрезка конца", RangeField(0.0, 3.0, 0.05, 2, " с")),
            ("fps_enabled", "Смена FPS", CheckField()),
            ("fps_choices", "Варианты FPS", ListField(float, "30; 29.97; 60")),
        ], toggles={"time_enabled": ("speed", "trim_start", "trim_end"),
                    "fps_enabled": ("fps_choices",)}),
        FieldGroup("Отражение", "uniq", [
            ("flip_enabled", "Зеркалить по горизонтали", CheckField()),
            ("flip_probability", "Вероятность", FloatField(0.0, 1.0, 0.05, 2)),
        ], toggles={"flip_enabled": ("flip_probability",)}),
        FieldGroup("Кодирование", "encoder", [
            ("crf", "CRF", RangeField(0.0, 51.0, 1.0, 0)),
            ("preset", "Пресет x264", ChoiceField(X264_PRESETS)),
            ("audio_bitrate_k", "Битрейт AAC", RangeField(32.0, 512.0, 8.0, 0, " кбит/с")),
            ("hwaccel", "Аппаратный энкодер", ChoiceField(HW_ACCEL_CHOICES)),
            ("strip_encoder_sei", "Убирать подпись x264", CheckField()),
        ]),
    ]


def metadata_groups() -> list[FieldGroup]:
    return [
        FieldGroup("Метаданные", "metadata", [
            ("date_from", "Дата съёмки от", TextField("ГГГГ-ММ-ДД, пусто = год назад")),
            ("date_to", "Дата съёмки до", TextField("ГГГГ-ММ-ДД, пусто = сегодня")),
            ("title_mode", "Название", ChoiceField([("empty", "Пустое"),
                                                    ("neutral", "Нейтральное из списка")])),
            ("neutral_titles", "Нейтральные названия", ListField(str, "video; clip; IMG")),
            ("verify", "Проверять через ffprobe", CheckField()),
        ]),
    ]


def music_groups() -> list[FieldGroup]:
    return [
        FieldGroup("Фоновая музыка", "music", [
            ("enabled", "Включено", CheckField()),
            ("path", "Трек или папка", PathField(("file", "dir"), AUDIO_FILTER)),
            ("volume_db", "Громкость музыки", FloatField(-40.0, 10.0, 0.5, 1, " дБ")),
            ("original_volume_db", "Громкость оригинала", FloatField(-40.0, 10.0, 0.5, 1, " дБ")),
            ("replace_audio", "Заменить оригинальный звук", CheckField()),
            ("random_start", "Случайное место в треке", CheckField()),
            ("fade_in", "Нарастание", _seconds()),
            ("fade_out", "Затухание", _seconds()),
        ], toggles={"enabled": ("path", "volume_db", "original_volume_db", "replace_audio",
                                "random_start", "fade_in", "fade_out")}),
        FieldGroup("Приглушение под голосом", "music", [
            ("ducking", "Включено", CheckField()),
            ("duck_ratio", "Степень", FloatField(1.0, 20.0, 0.5, 1)),
            ("duck_threshold", "Порог", FloatField(0.001, 1.0, 0.005, 3)),
        ], toggles={"ducking": ("duck_ratio", "duck_threshold")},
           hint="Музыка становится тише, когда в оригинале звучит голос."),
    ]


def subtitle_groups() -> list[FieldGroup]:
    return [
        FieldGroup("Источник", "subtitles", [
            ("enabled", "Включено", CheckField()),
            ("source", "Откуда брать", ChoiceField([("file", "Файл .srt/.ass рядом с видео"),
                                                     ("auto", "Распознать речь (faster-whisper)")])),
            ("whisper_model", "Модель whisper", ChoiceField(WHISPER_MODELS)),
            ("language", "Язык", TextField("ru, en, … пусто = автоопределение")),
            ("save_srt", "Сохранять .srt рядом с результатом", CheckField()),
        ], toggles={"enabled": ("source", "whisper_model", "language", "save_srt")},
           hint="Для распознавания речи нужен пакет faster-whisper."),
        FieldGroup("Стиль", "subtitles", [
            ("font", "Шрифт", TextField()),
            ("fonts_dir", "Папка шрифтов", PathField(("dir",))),
            ("font_size", "Размер", IntField(8, 300)),
            ("bold", "Жирный", CheckField()),
            ("uppercase", "ЗАГЛАВНЫЕ", CheckField()),
            ("primary_color", "Цвет текста", ColorField()),
            ("highlight_color", "Цвет подсветки", ColorField()),
            ("outline_color", "Цвет обводки", ColorField()),
            ("outline", "Обводка", FloatField(0.0, 20.0, 0.5, 1)),
            ("shadow", "Тень", FloatField(0.0, 20.0, 0.5, 1)),
            ("position", "Позиция", ChoiceField([(p, label) for p, label in
                                                  zip(SUB_POSITIONS, ("Снизу", "По центру", "Сверху"))])),
            ("margin_v", "Отступ по вертикали", IntField(0, 1900)),
        ]),
        FieldGroup("Стиль Shorts", "subtitles", [
            ("shorts_style", "По несколько слов", CheckField()),
            ("max_words", "Слов на экране", IntField(1, 10)),
            ("highlight_current_word", "Подсвечивать текущее слово", CheckField()),
        ], toggles={"shorts_style": ("max_words", "highlight_current_word")}),
    ]


def output_groups() -> list[FieldGroup]:
    return [
        FieldGroup("Результат", "output", [
            ("recursive", "Искать во вложенных папках", CheckField()),
            ("output_dir", "Папка результата", PathField(("dir",))),
            ("name_template", "Шаблон имени", TextField("IMG_{random4}_{date}")),
            ("variants", "Вариантов на видео", IntField(1, 100)),
        ], hint="Токены шаблона: {random4} {date} {time} {uuid} {stem} {variant} {index}"),
    ]


def batch_groups() -> list[FieldGroup]:
    workers = IntField(0, MAX_WORKERS)
    workers.widget.setSpecialValueText("авто")
    workers.widget.setMinimumWidth(workers.widget.fontMetrics().horizontalAdvance("авто") * 2)
    return [
        FieldGroup("Очередь", "batch", [
            ("workers", "Параллельных задач", workers),
            ("seed", "Сид", TextField("пусто = случайно")),
            ("skip_existing", "Пропускать готовые", CheckField()),
            ("preview_seconds", "Длина превью", FloatField(1.0, 120.0, 1.0, 0, " с")),
            ("ffmpeg_path", "Путь к ffmpeg", PathField(("file", "dir"), FFMPEG_FILTER,
                                                       "пусто = из PATH")),
        ], hint="«авто» = половина ядер процессора."),
    ]
