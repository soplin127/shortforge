import pytest

from features.subtitles import SubtitleError, parse_ass

HEAD = "[Events]\nFormat: Layer, Start, End, Text\n"


def test_truncated_dialogue_raises_subtitle_error_with_line():
    with pytest.raises(SubtitleError, match="строка 3"):
        parse_ass(HEAD + "Dialogue: 0,0:00:01.00\n")


@pytest.mark.parametrize("stamp", ["bad", "1:2", "0:aa:01.00"])
def test_bad_timestamp_raises_subtitle_error(stamp):
    with pytest.raises(SubtitleError, match="строка 3"):
        parse_ass(HEAD + f"Dialogue: 0,{stamp},0:00:02.00,Hi\n")


def test_valid_dialogue_still_parsed():
    cues = parse_ass(HEAD + "Dialogue: 0,0:00:01.00,0:00:02.50,{\\i1}Hi\\Nthere\n")
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 2.5, "Hi there")]
