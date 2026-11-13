import re
from dataclasses import replace
from pathlib import Path

import pytest

from core.config import (
    ConfigError,
    EncoderSettings,
    Preset,
    Range,
    load_preset,
    preset_from_dict,
    preset_to_dict,
    save_preset,
)


def test_default_preset_round_trips_through_json(tmp_path):
    preset = replace(Preset(), encoder=EncoderSettings(crf=Range(20, 24), hwaccel="none"))
    path = tmp_path / "p.json"

    save_preset(preset, path)

    assert load_preset(path) == preset


def test_range_accepts_list_and_object_forms():
    data = {"encoder": {"crf": [19, 21], "audio_bitrate_k": {"min": 96, "max": 128}}}

    preset = preset_from_dict(data)

    assert preset.encoder.crf == Range(19.0, 21.0)
    assert preset.encoder.audio_bitrate_k == Range(96.0, 128.0)


def test_partial_preset_keeps_defaults_for_missing_sections():
    preset = preset_from_dict({"output": {"variants": 3}})

    assert preset.output.variants == 3
    assert preset.uniq == Preset().uniq


@pytest.mark.parametrize("data, fragment", [
    ({"encoder": {"crf": [30, 20]}}, "greater than max"),
    ({"encoder": {"crf": [0, 60]}}, "CRF"),
    ({"encoder": {"hwaccel": "gpu"}}, "hwaccel"),
    ({"output": {"variants": 0}}, "variants"),
    ({"output": {"variants": 1.5}}, "integer"),
    ({"batch": {"skip_existing": "yes"}}, "true/false"),
    ({"bogus": {}}, "unknown keys"),
])
def test_invalid_values_raise_config_error(data, fragment):
    with pytest.raises(ConfigError, match=fragment):
        preset_from_dict(data)


def test_unreadable_preset_file_raises_config_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ConfigError, match="Cannot read preset"):
        load_preset(path)


def test_shipped_default_preset_matches_code_defaults():
    preset = load_preset(Path(__file__).parent.parent / "presets" / "default.json")

    assert preset_to_dict(preset) == preset_to_dict(Preset())


@pytest.mark.parametrize("text", [
    '{"uniq": {"speed": [NaN, 1.0]}}',
    '{"encoder": {"crf": [18, Infinity]}}',
    '{"output": {"variants": Infinity}}',
    '{"music": {"volume_db": -Infinity}}',
    '{"uniq": {"flip_probability": NaN}}',
])
def test_preset_rejects_non_finite_numbers(tmp_path, text):
    path = tmp_path / "p.json"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ConfigError, match="finite"):
        load_preset(path)


def test_range_rejects_nan_when_built_directly():
    with pytest.raises(ConfigError, match="finite"):
        Range(float("nan"), 1.0)


@pytest.mark.parametrize("value, where", [
    (["fast", 1.0], "preset.uniq.speed[0]"),
    ([0.9, None], "preset.uniq.speed[1]"),
    ([True, 1.0], "preset.uniq.speed[0]"),
    ({"min": 0.9, "max": "1.1"}, "preset.uniq.speed.max"),
])
def test_range_bounds_must_be_numbers(value, where):
    with pytest.raises(ConfigError, match=re.escape(f"{where}: expected a number")):
        preset_from_dict({"uniq": {"speed": value}})


@pytest.mark.parametrize("field", ["primary_color", "highlight_color", "outline_color"])
@pytest.mark.parametrize("value", ["white", "#FFF", "FFFFFF", "#GGGGGG", "#FFFFFF@0.5"])
def test_subtitle_colours_must_be_hex(field, value):
    with pytest.raises(ConfigError, match=f"subtitles.{field}"):
        preset_from_dict({"subtitles": {field: value}})


def _banner(**overrides):
    return {"ads": {"slots": [{"mode": "overlay", "text": "hi", **overrides}]}}


@pytest.mark.parametrize("value", ["#FFF", "#FFFFFF@2", "#FFFFFF@", "0xFFFFFF", "red;x", ""])
def test_banner_colours_reject_values_ffmpeg_cannot_parse(value):
    with pytest.raises(ConfigError, match="ad.text_color"):
        preset_from_dict(_banner(text_color=value))


@pytest.mark.parametrize("value", ["#000000@0.6", "#ffcc00", "white", "black@0.5", "#FFFFFF@1", "#000000@.25"])
def test_banner_colours_accept_hex_names_and_alpha(value):
    slot = preset_from_dict(_banner(box_color=value)).ads.slots[0]

    assert slot.box_color == value


@pytest.mark.parametrize("field, value", [
    ("date_from", "2025-13-01"),
    ("date_from", "yesterday"),
    ("date_to", "2025-02-30"),
])
def test_metadata_dates_must_be_iso(field, value):
    with pytest.raises(ConfigError, match=f"metadata.{field}"):
        preset_from_dict({"metadata": {field: value}})


def test_metadata_date_range_must_not_be_reversed():
    with pytest.raises(ConfigError, match="after"):
        preset_from_dict({"metadata": {"date_from": "2025-06-01", "date_to": "2025-01-01"}})


@pytest.mark.parametrize("dates", [
    {},
    {"date_from": "2025-01-01"},
    {"date_to": "2025-01-01"},
    {"date_from": " 2025-01-01 ", "date_to": "2025-01-01"},
])
def test_metadata_dates_accept_empty_and_ordered_values(dates):
    assert preset_from_dict({"metadata": dates}).metadata.date_from == dates.get("date_from", "")


def test_save_preset_keeps_original_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    save_preset(Preset(), path)
    original = path.read_text(encoding="utf-8")

    def fail(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("core.config.os.replace", fail)
    with pytest.raises(OSError, match="disk full"):
        save_preset(replace(Preset(), encoder=EncoderSettings(hwaccel="none")), path)

    assert path.read_text(encoding="utf-8") == original
    assert [p.name for p in tmp_path.iterdir()] == ["p.json"]


def test_save_preset_overwrites_existing_file_without_leftovers(tmp_path):
    path = tmp_path / "p.json"
    path.write_text("{ truncated", encoding="utf-8")
    preset = replace(Preset(), encoder=EncoderSettings(hwaccel="none"))

    save_preset(preset, path)

    assert load_preset(path) == preset
    assert [p.name for p in tmp_path.iterdir()] == ["p.json"]
