import random
from dataclasses import replace
from pathlib import Path

import pytest

from core.config import (
    AdSettings,
    AdSlot,
    ConfigError,
    MetadataSettings,
    MusicSettings,
    Preset,
    preset_from_dict,
)
from core.ffmpeg import Capabilities, FeatureUnavailable, FFmpegTools, MediaInfo
from core.filtergraph import FilterGraph, fmt
from core.pipeline import FREEZE_READ_SECONDS, JobContext, plan_job
from core.timeline import build_timeline
from features import ads as ads_mod
from features.ads import (
    AdError,
    OverlayAd,
    apply_overlays,
    banner_filter,
    key_color_from_pixels,
    overlay_source_filters,
    pick_file,
    position_xy,
    resolve_key_colors,
    roll_clips,
    roll_overlays,
)
from features.metadata import roll_metadata

ALL_FILTERS = frozenset({"ass", "drawtext", "chromakey"})


def _identity(t):
    return t


def _ads(*slots):
    return AdSettings(enabled=True, slots=tuple(slots))


def _fake_clip(monkeypatch, duration=2.0, key_color=""):
    info = MediaInfo(path="promo.mp4", duration=duration, has_video=True, width=640, height=360,
                     has_audio=True)
    monkeypatch.setattr(ads_mod, "media_info", lambda tools, path: replace(info, path=path))
    monkeypatch.setattr(ads_mod, "detect_key_color", lambda tools, path: key_color)
    return info


@pytest.mark.parametrize("position, expected", [
    ("top-left", ("80", "80")),
    ("bottom-right", ("W-w-80", "H-h-80")),
    ("center", ("(W-w)/2", "(H-h)/2")),
    ("top", ("(W-w)/2", "80")),
])
def test_position_expressions(position, expected):
    assert position_xy(position, 80, "w", "h") == expected


def test_pick_file_from_folder_and_missing_folder(tmp_path):
    for name in ("b.mp4", "a.MOV", "readme.txt", ".hidden.mp4"):
        (tmp_path / name).write_bytes(b"x")

    picked = pick_file(str(tmp_path), ads_mod.VIDEO_EXTENSIONS, random.Random(3))

    assert picked.name in {"a.MOV", "b.mp4"}
    with pytest.raises(AdError, match="Рекламный файл не найден"):
        pick_file(str(tmp_path / "nope"), ads_mod.VIDEO_EXTENSIONS, random.Random(3))


def test_roll_clips_resolves_percent_against_ad_free_duration(tmp_path, monkeypatch):
    (tmp_path / "promo.mp4").write_bytes(b"x")
    _fake_clip(monkeypatch)
    settings = _ads(AdSlot(mode="clip", path=str(tmp_path), at="50%"),
                    AdSlot(mode="overlay", text="hi"))

    clips = roll_clips(None, settings, random.Random(1), 10.0)

    assert len(clips) == 1
    assert clips[0].at == pytest.approx(5.0)
    assert clips[0].note == "clip:promo.mp4@5.0s"


def test_disabled_ads_roll_nothing(monkeypatch):
    settings = replace(_ads(AdSlot(mode="clip", path="missing")), enabled=False)

    assert roll_clips(None, settings, random.Random(1), 10.0) == []


def test_overlay_stops_where_a_spliced_clip_starts():
    timeline = build_timeline(0.0, 10.0, [(4.0, 2.0)], speed=1.0)  # ad occupies 4-6 s
    settings = _ads(AdSlot(mode="overlay", text="SALE", at="3", duration=5.0))

    [banner] = roll_overlays(settings, random.Random(1), timeline, _identity, 10.0)

    assert (banner.kind, banner.start, banner.end) == ("text", 3.0, 4.0)


def test_overlay_at_insertion_point_lands_after_the_clip():
    timeline = build_timeline(0.0, 10.0, [(4.0, 2.0)], speed=1.0)
    settings = _ads(AdSlot(mode="overlay", text="SALE", at="4", duration=3.0))

    [banner] = roll_overlays(settings, random.Random(1), timeline, _identity, 10.0)

    assert (banner.start, banner.end) == (6.0, 9.0)


def test_video_overlay_filters_key_scale_fade_and_shift():
    slot = AdSlot(mode="overlay", path="x.mp4", scale=0.5, fade=0.4)
    ov = OverlayAd(slot, "video", "x.mp4", start=2.0, end=5.0, key_color="#00FF00")

    filters = overlay_source_filters(ov, 1080)

    assert filters[0] == "chromakey=0x00FF00:0.15:0.05"
    assert "scale=540:-2:flags=bicubic" in filters
    assert "fade=t=out:st=2.6:d=0.4:alpha=1" in filters
    assert filters[-1] == "setpts=PTS-STARTPTS+2/TB"


def test_banner_reads_text_from_file_and_is_time_gated():
    ov = OverlayAd(AdSlot(mode="overlay", text="50% OFF: today"), "text", "", 1.0, 3.0)

    text = banner_filter(ov, "banner_0.txt", None)

    assert "textfile=banner_0.txt:expansion=none" in text
    assert "font=Sans" in text
    assert "enable='between(t,1,3)'" in text
    assert "50%" not in text  # the user text never enters the filter string


def test_apply_overlays_chains_image_and_writes_banner_file(tmp_path):
    graph, inputs = FilterGraph(), []
    image = OverlayAd(AdSlot(mode="overlay", path="logo.png", position="top"), "image", "logo.png", 1.0, 2.0)
    banner = OverlayAd(AdSlot(mode="overlay", text="Привет"), "text", "", 1.0, 2.0)

    def add_input(*args):
        inputs.append(args)
        return len(inputs)

    out = apply_overlays(graph, add_input, "v0", [image, banner], 1080, "30", tmp_path)
    rendered = graph.render()

    assert inputs == [("-loop", "1", "-framerate", "30", "-t", "1", "-i", "logo.png")]
    assert "[1:v:0]" in rendered and "overlay=x=(W-w)/2:y=80:eof_action=pass" in rendered
    assert (tmp_path / "banner_1.txt").read_text(encoding="utf-8") == "Привет"
    assert rendered.endswith(f"[{out}]")


def _ctx(tmp_path, ads, filters=ALL_FILTERS):
    caps = Capabilities(version="7", filters=filters, encoders=frozenset({"libx264"}), bsfs=frozenset())
    return JobContext(FFmpegTools("ffmpeg", "ffprobe"), caps, replace(Preset(), ads=ads), "libx264", tmp_path)


def test_plan_job_splices_clip_and_grows_duration(tmp_path, monkeypatch):
    (tmp_path / "promo.mp4").write_bytes(b"x")
    _fake_clip(monkeypatch, duration=2.0)
    source = MediaInfo(path="src.mp4", duration=8.0, has_video=True, width=720, height=1280,
                       has_audio=True)
    rng = random.Random(5)
    meta = roll_metadata(MetadataSettings(), random.Random(5))
    plain = plan_job(_ctx(tmp_path, AdSettings()), source, random.Random(5), tmp_path / "a.mp4", meta)

    slot = AdSlot(mode="clip", path=str(tmp_path), at="2", backdrop="black")
    plan = plan_job(_ctx(tmp_path, _ads(slot)), source, rng, tmp_path / "b.mp4", meta)
    graph = plan.command[plan.command.index("-filter_complex") + 1]

    assert plan.expected_duration == pytest.approx(plain.expected_duration + 2.0)
    assert "concat=n=3:v=1:a=1" in graph
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black" in graph
    assert plan.notes["ads"].startswith("clip:promo.mp4@2.0s")


def _plan_with_clip(tmp_path, monkeypatch, backdrop, key_color="", filters=ALL_FILTERS, **slot_fields):
    (tmp_path / "promo.mp4").write_bytes(b"x")
    _fake_clip(monkeypatch, duration=2.0, key_color=key_color)
    source = MediaInfo(path="src.mp4", duration=8.0, has_video=True, width=720, height=1280,
                       has_audio=True)
    slot = AdSlot(mode="clip", path=str(tmp_path), at="2", backdrop=backdrop, **slot_fields)
    plan = plan_job(_ctx(tmp_path, _ads(slot), filters), source, random.Random(5), tmp_path / "o.mp4",
                    roll_metadata(MetadataSettings(), random.Random(5)))
    return plan, plan.command[plan.command.index("-filter_complex") + 1]


def _freeze_input_positions(command):
    """Indices of src.mp4 inputs read as a short still (``-ss T -t 0.5 -i src.mp4``)."""
    return [i for i, arg in enumerate(command)
            if arg == "src.mp4" and command[i - 3:i] == ["-t", fmt(FREEZE_READ_SECONDS, 3), "-i"]]


def test_freeze_backdrop_holds_the_paused_source_frame_behind_the_clip(tmp_path, monkeypatch):
    plan, graph = _plan_with_clip(tmp_path, monkeypatch, "freeze")

    [ad] = [s for s in plan.timeline.segments if s.kind == "ad"]
    [still] = _freeze_input_positions(plan.command)
    assert plan.command.count("src.mp4") == 3  # two source pieces + the frozen frame
    assert plan.command[still - 5:still - 3] == ["-ss", fmt(ad.src_start, 4)]
    assert "trim=end_frame=1" in graph
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in graph
    assert "pad=1080:1920" not in graph
    assert "boxblur" not in graph


def test_blur_backdrop_blurs_the_frozen_frame(tmp_path, monkeypatch):
    _, graph = _plan_with_clip(tmp_path, monkeypatch, "blur")

    assert "trim=end_frame=1,boxblur=" in graph


def test_black_backdrop_letterboxes_without_reading_the_source_again(tmp_path, monkeypatch):
    plan, graph = _plan_with_clip(tmp_path, monkeypatch, "black")

    assert plan.command.count("src.mp4") == 2
    assert "trim=end_frame=1" not in graph


def test_music_is_silenced_during_the_spliced_clip(tmp_path, monkeypatch):
    (tmp_path / "promo.mp4").write_bytes(b"x")
    (tmp_path / "song.mp3").write_bytes(b"x")
    _fake_clip(monkeypatch, duration=2.0)
    monkeypatch.setattr("core.pipeline.track_duration", lambda tools, track: 60.0)
    ctx = _ctx(tmp_path, _ads(AdSlot(mode="clip", path=str(tmp_path / "promo.mp4"), at="2")))
    music = MusicSettings(enabled=True, path=str(tmp_path / "song.mp3"))
    ctx = replace(ctx, preset=replace(ctx.preset, music=music))
    source = MediaInfo(path="src.mp4", duration=8.0, has_video=True, width=720, height=1280,
                       has_audio=True)

    plan = plan_job(ctx, source, random.Random(5), tmp_path / "m.mp4",
                    roll_metadata(MetadataSettings(), random.Random(5)))
    graph = plan.command[plan.command.index("-filter_complex") + 1]

    [ad] = [s for s in plan.timeline.segments if s.kind == "ad"]
    window = f"between(t,{fmt(ad.out_start, 3)},{fmt(ad.out_end, 3)})"
    assert f"volume=0:enable='{window}'" in graph


def test_unknown_backdrop_is_rejected():
    with pytest.raises(ConfigError, match="backdrop"):
        AdSlot(path="a.mp4", backdrop="rainbow")


def test_plan_job_requires_drawtext_for_banners(tmp_path):
    source = MediaInfo(path="src.mp4", duration=8.0, has_video=True, width=720, height=1280)
    ctx = _ctx(tmp_path, _ads(AdSlot(mode="overlay", text="hi", at="1")), filters=frozenset())

    with pytest.raises(FeatureUnavailable, match="drawtext"):
        plan_job(ctx, source, random.Random(1), Path("o.mp4"),
                 roll_metadata(MetadataSettings(), random.Random(1)))


# ------------------------------------------------------------- chroma key

GREEN_SCREEN = (0, 177, 64)
BLUE_SCREEN = (0, 71, 187)
SUBJECT = (200, 60, 40)


def _frame(size, border, subject=SUBJECT, noise=0, subject_rows=None):
    """rgb24 bytes: ``border`` colour around a subject in the middle of the frame."""
    rng = random.Random(0)
    lo, hi = size // 4, size * 3 // 4
    rows = subject_rows or range(lo, hi)
    pixels = bytearray()
    for y in range(size):
        for x in range(size):
            colour = subject if lo <= x < hi and y in rows else border
            pixels += bytes(min(255, max(0, c + rng.randint(-noise, noise))) for c in colour)
    return bytes(pixels)


def test_green_screen_border_becomes_the_key_color():
    assert key_color_from_pixels(_frame(16, GREEN_SCREEN), 16, 16) == "#00B140"


def test_blue_screen_border_becomes_the_key_color():
    assert key_color_from_pixels(_frame(16, BLUE_SCREEN), 16, 16) == "#0047BB"


def test_noisy_screen_and_a_subject_touching_the_edge_still_key():
    frame = _frame(32, GREEN_SCREEN, noise=12, subject_rows=range(8, 32))

    color = key_color_from_pixels(frame, 32, 32)

    assert color
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    assert abs(r - 0) <= 12 and abs(g - 177) <= 12 and abs(b - 64) <= 12


@pytest.mark.parametrize("border", [(0, 0, 0), (240, 240, 240), (180, 40, 40)])
def test_uniform_border_that_is_not_a_screen_colour_is_not_keyed(border):
    assert key_color_from_pixels(_frame(16, border), 16, 16) == ""


def test_ordinary_footage_with_a_busy_border_is_not_keyed():
    rng = random.Random(1)
    frame = bytes(rng.randrange(256) for _ in range(16 * 16 * 3))

    assert key_color_from_pixels(frame, 16, 16) == ""


def test_truncated_frame_is_not_keyed():
    assert key_color_from_pixels(b"\x00\xb1\x40" * 10, 16, 16) == ""


def _video_overlay(**slot_fields):
    slot = AdSlot(mode="overlay", path="g.mp4", **slot_fields)
    return OverlayAd(slot, "video", "g.mp4", 1.0, 3.0)


def test_auto_chroma_keys_the_detected_background(monkeypatch):
    monkeypatch.setattr(ads_mod, "detect_key_color", lambda tools, path: "#00B140")

    [keyed] = resolve_key_colors(None, [_video_overlay()])

    assert keyed.key_color == "#00B140"
    assert overlay_source_filters(keyed, 1080)[0] == "chromakey=0x00B140:0.15:0.05"


def test_auto_chroma_leaves_footage_without_a_screen_untouched(monkeypatch):
    monkeypatch.setattr(ads_mod, "detect_key_color", lambda tools, path: "")

    [plain] = resolve_key_colors(None, [_video_overlay()])

    assert not any(f.startswith("chromakey") for f in overlay_source_filters(plain, 1080))


def test_chroma_off_fixed_colour_and_images_skip_detection(monkeypatch):
    monkeypatch.setattr(ads_mod, "detect_key_color",
                        lambda tools, path: pytest.fail("detection must not run"))
    image = OverlayAd(AdSlot(mode="overlay", path="logo.png"), "image", "logo.png", 1.0, 3.0)
    overlays = [_video_overlay(chroma="off"),
                _video_overlay(chroma="color", chroma_key="#0000FF"), image]

    assert [ov.key_color for ov in resolve_key_colors(None, overlays)] == ["", "#0000FF", ""]


def test_chroma_settings_are_validated():
    with pytest.raises(ConfigError, match="chroma"):
        AdSlot(path="a.mp4", chroma="magic")
    with pytest.raises(ConfigError, match="chroma_key"):
        AdSlot(path="a.mp4", chroma="color", chroma_key="green")


def test_presets_saved_before_auto_chroma_switch_to_auto():
    data = {"ads": {"enabled": True, "slots": [
        {"mode": "overlay", "path": "g.mp4", "chroma_key": ""}]}}

    assert preset_from_dict(data).ads.slots[0].chroma == "auto"


def _plan_with_video_overlay(tmp_path, monkeypatch, filters=ALL_FILTERS):
    (tmp_path / "g.mp4").write_bytes(b"x")
    monkeypatch.setattr(ads_mod, "detect_key_color", lambda tools, path: "#00B140")
    source = MediaInfo(path="src.mp4", duration=8.0, has_video=True, width=720, height=1280)
    slot = AdSlot(mode="overlay", path=str(tmp_path / "g.mp4"), at="1", duration=2.0)
    plan = plan_job(_ctx(tmp_path, _ads(slot), filters), source, random.Random(1),
                    tmp_path / "o.mp4", roll_metadata(MetadataSettings(), random.Random(1)))
    return plan.command[plan.command.index("-filter_complex") + 1]


def test_plan_job_keys_a_green_screen_overlay_automatically(tmp_path, monkeypatch):
    assert "chromakey=0x00B140:0.15:0.05" in _plan_with_video_overlay(tmp_path, monkeypatch)


def test_plan_job_requires_chromakey_for_an_auto_keyed_overlay(tmp_path, monkeypatch):
    with pytest.raises(FeatureUnavailable, match="chromakey"):
        _plan_with_video_overlay(tmp_path, monkeypatch, filters=frozenset({"ass", "drawtext"}))


def test_roll_clips_detects_the_green_screen_of_a_spliced_clip(tmp_path, monkeypatch):
    (tmp_path / "promo.mp4").write_bytes(b"x")
    _fake_clip(monkeypatch, key_color="#00B140")
    slots = (AdSlot(mode="clip", path=str(tmp_path / "promo.mp4"), at="1"),
             AdSlot(mode="clip", path=str(tmp_path / "promo.mp4"), at="2", chroma="off"),
             AdSlot(mode="clip", path=str(tmp_path / "promo.mp4"), at="3",
                    chroma="color", chroma_key="#0000FF"))

    clips = roll_clips(None, _ads(*slots), random.Random(1), 10.0)

    assert [c.key_color for c in clips] == ["#00B140", "", "#0000FF"]


@pytest.mark.parametrize("backdrop", ["freeze", "blur", "black"])
def test_green_screen_clip_is_keyed_over_its_backdrop(tmp_path, monkeypatch, backdrop):
    _, graph = _plan_with_clip(tmp_path, monkeypatch, backdrop, key_color="#00B140")

    assert "chromakey=0x00B140:0.15:0.05" in graph
    assert "overlay=x=(W-w)/2:y=(H-h)/2" in graph
    assert ":(ow-iw)/2:(oh-ih)/2:color=black" not in graph  # letterboxing hides the backdrop
    if backdrop == "black":
        assert "color=c=black:s=1080x1920" in graph


def test_clip_without_a_screen_is_not_keyed(tmp_path, monkeypatch):
    _, graph = _plan_with_clip(tmp_path, monkeypatch, "freeze")

    assert "chromakey" not in graph


def test_keyed_clip_requires_chromakey(tmp_path, monkeypatch):
    with pytest.raises(FeatureUnavailable, match="chromakey"):
        _plan_with_clip(tmp_path, monkeypatch, "freeze", key_color="#00B140",
                        filters=frozenset({"ass", "drawtext"}))
