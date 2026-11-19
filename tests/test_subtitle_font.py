import pytest

from core.config import ConfigError, SubtitleSettings
from features.subtitles import ass_header


@pytest.mark.parametrize("font", ["Arial, Bold", "Inter,sans-serif", "", "   "])
def test_bad_font_names_are_rejected(font):
    with pytest.raises(ConfigError, match="subtitles.font"):
        SubtitleSettings(font=font)


def test_style_line_keeps_field_count():
    header = ass_header(SubtitleSettings(font="Open Sans Bold"), 1080, 1920)
    style = next(line for line in header.splitlines() if line.startswith("Style:"))
    assert style.count(",") == 22
