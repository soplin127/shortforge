import pytest

from features.subtitles import SubtitleError, read_text

SRT = "1\n00:00:01,000 --> 00:00:02,000\nПривет\n"


@pytest.mark.parametrize("codec", ["utf-16", "utf-16-le", "utf-16-be"])
def test_utf16_with_bom_is_decoded(tmp_path, codec):
    path = tmp_path / "a.srt"
    bom = {"utf-16-le": b"\xff\xfe", "utf-16-be": b"\xfe\xff"}.get(codec, b"")
    path.write_bytes(bom + SRT.encode(codec))
    assert read_text(path) == SRT


def test_truncated_utf16_raises_subtitle_error(tmp_path):
    path = tmp_path / "a.srt"
    path.write_bytes(b"\xff\xfe" + SRT.encode("utf-16-le")[:-1])
    with pytest.raises(SubtitleError):
        read_text(path)


def test_cp1251_still_supported(tmp_path):
    path = tmp_path / "a.srt"
    path.write_bytes(SRT.encode("cp1251"))
    assert read_text(path) == SRT
