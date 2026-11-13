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
