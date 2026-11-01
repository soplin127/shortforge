"""Ads: spliced-in clips and timed overlays (image, chroma-keyed video, text banner).

Slot times ("7", "50%", "random:20%-60%") refer to the uniqualized video *without*
ads. Clip insertions are fed into the timeline; overlays are mapped through it, so
an overlay stays on the same moment of content after clips are spliced in.
"""
from __future__ import annotations

import os
import random
import statistics
import sys
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from core.config import AdSettings, AdSlot
from core.ffmpeg import FFmpegError, FFmpegTools, MediaInfo, grab_frame_rgb, probe
from core.filtergraph import AUDIO_FORMAT, FilterGraph, color_to_ffmpeg, even, fmt, quote_path
from core.sources import VIDEO_EXTENSIONS
from core.timeline import TimelineMap, parse_time_spec
from features.uniq import pad_audio_to

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MIN_AD_SECONDS = 0.3
CHROMA_BLEND = 0.05
# Automatic green/blue screen detection on a small thumbnail of the overlay video.
CHROMA_SAMPLE_SIZE = 32
CHROMA_SAMPLE_AT = (0.5, 0.0)  # seconds; the second try covers very short videos
CHROMA_UNIFORM_SHARE = 0.6  # border pixels that must match the background colour
CHROMA_UNIFORM_TOLERANCE = 40  # max per-channel difference from the median, 0-255
CHROMA_DOMINANCE = 50  # how much green/blue must exceed the other two channels
BANNER_PADDING = 24
FONT_CANDIDATES = {
    "win32": [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arial.ttf"],
    "darwin": [Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/Library/Fonts/Arial.ttf"),
               Path("/System/Library/Fonts/Helvetica.ttc")],
    "linux": [Path("/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf"),
              Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")],
}


class AdError(FFmpegError):
    pass


@dataclass(frozen=True)
class ClipAd:
    info: MediaInfo
    at: float  # seconds into the ad-free output
    backdrop: str = "freeze"  # see core.config.AD_BACKDROPS
    key_color: str = ""  # "#RRGGBB" removed from the clip so the backdrop shows through
    key_similarity: float = 0.15

    @property
    def note(self) -> str:
        return f"clip:{Path(self.info.path).name}@{self.at:.1f}s"


@dataclass(frozen=True)
class OverlayAd:
    slot: AdSlot
    kind: str  # "image" | "video" | "text"
    path: str
    start: float  # output time
    end: float
    key_color: str = ""  # "#RRGGBB" removed from a video overlay, see resolve_key_colors

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def fade(self) -> float:
        return min(self.slot.fade, self.duration / 2.0)

    @property
    def note(self) -> str:
        label = Path(self.path).name if self.path else "text"
        return f"{self.kind}:{label}@{self.start:.1f}s"


# ---------------------------------------------------------------- picking


def _media_files(path: str, extensions: frozenset[str]) -> list[Path]:
    root = Path(path).expanduser()
    if root.is_dir():
        return sorted((p for p in root.iterdir()
                       if p.is_file() and p.suffix.lower() in extensions and not p.name.startswith(".")),
                      key=lambda p: p.name.lower())
    return [root] if root.is_file() and root.suffix.lower() in extensions else []


def pick_file(path: str, extensions: frozenset[str], rng: random.Random) -> Path:
    files = _media_files(path, extensions)
    if not files:
        raise AdError(f"Рекламный файл не найден: {path} (нужны {', '.join(sorted(extensions))})")
    return rng.choice(files)


@lru_cache(maxsize=256)
def media_info(tools: FFmpegTools, path: str) -> MediaInfo:
    info = probe(tools, path)
    if not info.has_video:
        raise AdError(f"В рекламном файле нет видео: {path}")
    return info


def roll_clips(tools: FFmpegTools, ads: AdSettings, rng: random.Random, base_duration: float) -> list[ClipAd]:
    clips = []
    for slot in ads.slots if ads.enabled else ():
        if slot.mode != "clip":
            continue
        info = media_info(tools, str(pick_file(slot.path, VIDEO_EXTENSIONS, rng)))
        if info.duration < MIN_AD_SECONDS:
            raise AdError(f"Рекламный ролик слишком короткий: {info.path}")
        clips.append(ClipAd(info, parse_time_spec(slot.at, base_duration, rng), slot.backdrop,
                            slot_key_color(tools, slot, info.path), slot.chroma_similarity))
    return clips


def roll_overlays(
    ads: AdSettings,
    rng: random.Random,
    timeline: TimelineMap,
    base_to_source,  # callable: ad-free output seconds -> source seconds
    base_duration: float,
) -> list[OverlayAd]:
    overlays = []
    for slot in ads.slots if ads.enabled else ():
        if slot.mode != "overlay":
            continue
        if slot.path:
            path = pick_file(slot.path, IMAGE_EXTENSIONS | VIDEO_EXTENSIONS, rng)
            kind = "image" if path.suffix.lower() in IMAGE_EXTENSIONS else "video"
        else:  # config guarantees banner text when there is no file
            path, kind = None, "text"
        start = timeline.to_output(base_to_source(parse_time_spec(slot.at, base_duration, rng)))
        if start is None:
            continue
        # never run into a spliced clip or past the end of the video
        end = min(start + slot.duration, timeline.containing_segment_end(start))
        if end - start >= MIN_AD_SECONDS:
            overlays.append(OverlayAd(slot, kind, str(path) if path else "", start, end))
    return overlays


# ------------------------------------------------------------- chroma key


def _border_pixels(pixels: bytes, width: int, height: int) -> list[tuple[int, int, int]]:
    def at(x: int, y: int) -> tuple[int, int, int]:
        i = (y * width + x) * 3
        return pixels[i], pixels[i + 1], pixels[i + 2]

    rows = [at(x, y) for y in (0, height - 1) for x in range(width)]
    cols = [at(x, y) for x in (0, width - 1) for y in range(1, height - 1)]
    return rows + cols


def _is_screen_colour(r: int, g: int, b: int) -> bool:
    return g - max(r, b) >= CHROMA_DOMINANCE or b - max(r, g) >= CHROMA_DOMINANCE


def key_color_from_pixels(pixels: bytes, width: int, height: int) -> str:
    """The green/blue screen colour around the frame edges as "#RRGGBB", or "" if there is none.

    A chroma background fills the edges of the frame with one saturated green or blue,
    while the subject sits in the middle; ordinary footage has varied edges.
    """
    if width < 2 or height < 2 or len(pixels) < width * height * 3:
        return ""
    border = _border_pixels(pixels, width, height)
    median = tuple(statistics.median_low(p[c] for p in border) for c in range(3))
    matching = sum(1 for p in border
                   if max(abs(p[c] - median[c]) for c in range(3)) <= CHROMA_UNIFORM_TOLERANCE)
    if matching < CHROMA_UNIFORM_SHARE * len(border) or not _is_screen_colour(*median):
        return ""
    return "#{:02X}{:02X}{:02X}".format(*median)


@lru_cache(maxsize=256)
def detect_key_color(tools: FFmpegTools, path: str) -> str:
    frame_bytes = CHROMA_SAMPLE_SIZE * CHROMA_SAMPLE_SIZE * 3
    for at in CHROMA_SAMPLE_AT:
        pixels = grab_frame_rgb(tools, path, at, CHROMA_SAMPLE_SIZE)
        if len(pixels) >= frame_bytes:
            return key_color_from_pixels(pixels, CHROMA_SAMPLE_SIZE, CHROMA_SAMPLE_SIZE)
    raise AdError(f"Не удалось прочитать кадр из рекламного видео: {path}")


def slot_key_color(tools: FFmpegTools, slot: AdSlot, path: str) -> str:
    """The colour to key out of a slot's video ("" for none), detected in "auto" mode."""
    if slot.chroma == "off":
        return ""
    if slot.chroma == "color":
        return slot.chroma_key
    return detect_key_color(tools, path)


def resolve_key_colors(tools: FFmpegTools, overlays: list[OverlayAd]) -> list[OverlayAd]:
    """Decide which colour to key out of each video overlay."""
    return [replace(ov, key_color=slot_key_color(tools, ov.slot, ov.path) if ov.kind == "video" else "")
            for ov in overlays]


def chroma_key_filter(color: str, similarity: float) -> str:
    return f"chromakey={color_to_ffmpeg(color)}:{fmt(similarity, 3)}:{CHROMA_BLEND}"


# ------------------------------------------------------------ clip pieces


def clip_fit_filters(target: tuple[int, int], fps: str) -> list[str]:
    """Scale a clip to fit inside the frame, keeping its aspect ratio (no padding)."""
    w, h = target
    return [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=bicubic",
        "setsar=1",
        f"fps={fps}",
        "format=yuv420p",
    ]


def clip_keyed_filters(clip: ClipAd, target: tuple[int, int], fps: str) -> list[str]:
    """Fit a clip into the frame and make its green/blue screen transparent (if it has one)."""
    fit = clip_fit_filters(target, fps)
    if not clip.key_color:
        return fit
    return [*fit, chroma_key_filter(clip.key_color, clip.key_similarity), "format=yuva420p"]


def clip_video_filters(target: tuple[int, int], fps: str) -> list[str]:
    """Fit a clip into the frame and letterbox it with black bars."""
    w, h = target
    fit = clip_fit_filters(target, fps)
    return [fit[0], f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black", *fit[1:]]


def clip_audio_filters(duration: float) -> list[str]:
    return [AUDIO_FORMAT, *pad_audio_to(duration)]


# --------------------------------------------------------------- overlays


def position_xy(position: str, margin: int, obj_w: str, obj_h: str) -> tuple[str, str]:
    """Expressions for overlay/drawtext; W/H are the frame, obj_w/obj_h the object."""
    if position.endswith("left"):
        x = str(margin)
    elif position.endswith("right"):
        x = f"W-{obj_w}-{margin}"
    else:
        x = f"(W-{obj_w})/2"
    if position.startswith("top"):
        y = str(margin)
    elif position.startswith("bottom"):
        y = f"H-{obj_h}-{margin}"
    else:
        y = f"(H-{obj_h})/2"
    return x, y


def _enable(ov: OverlayAd) -> str:
    return f"enable='between(t,{fmt(ov.start, 3)},{fmt(ov.end, 3)})'"


def overlay_input_args(ov: OverlayAd, fps: str) -> list[str]:
    length = fmt(ov.duration, 3)
    if ov.kind == "image":
        return ["-loop", "1", "-framerate", fps, "-t", length, "-i", ov.path]
    return ["-stream_loop", "-1", "-t", length, "-i", ov.path]


def overlay_source_filters(ov: OverlayAd, frame_w: int) -> list[str]:
    filters = []
    if ov.key_color:
        filters.append(chroma_key_filter(ov.key_color, ov.slot.chroma_similarity))
    filters += [f"scale={even(frame_w * ov.slot.scale)}:-2:flags=bicubic", "format=yuva420p"]
    if ov.fade > 0:
        filters += [
            f"fade=t=in:st=0:d={fmt(ov.fade, 3)}:alpha=1",
            f"fade=t=out:st={fmt(ov.duration - ov.fade, 3)}:d={fmt(ov.fade, 3)}:alpha=1",
        ]
    filters.append(f"setpts=PTS-STARTPTS+{fmt(ov.start, 3)}/TB")
    return filters


def default_font_file() -> Path | None:
    key = "linux" if sys.platform.startswith("linux") else sys.platform
    return next((p for p in FONT_CANDIDATES.get(key, []) if p.is_file()), None)


def banner_filter(ov: OverlayAd, text_file: str, font_file: Path | None) -> str:
    slot = ov.slot
    x, y = position_xy(slot.position, slot.margin, "tw", "th")
    a, b, f = fmt(ov.start, 3), fmt(ov.end, 3), fmt(max(ov.fade, 0.001), 3)
    alpha = f"if(lt(t,{a}+{f}),(t-{a})/{f},if(gt(t,{b}-{f}),({b}-t)/{f},1))"
    font = f"fontfile={quote_path(str(font_file))}" if font_file else "font=Sans"
    return (
        f"drawtext={font}:textfile={text_file}:expansion=none"
        f":fontsize={slot.text_size}:fontcolor={color_to_ffmpeg(slot.text_color)}"
        f":box=1:boxcolor={color_to_ffmpeg(slot.box_color)}:boxborderw={BANNER_PADDING}"
        f":x={x}:y={y}:alpha='{alpha}':{_enable(ov)}"
    )


def apply_overlays(
    graph: FilterGraph,
    add_input,  # callable(*args) -> input index
    video: str,
    overlays: list[OverlayAd],
    frame_w: int,
    fps: str,
    work_dir: Path,
) -> str:
    """Chain every overlay onto ``video`` and return the resulting label."""
    font_file = default_font_file()
    for i, ov in enumerate(overlays):
        if ov.kind == "text":
            text_file = f"banner_{i}.txt"  # relative: ffmpeg runs with cwd=work_dir
            (work_dir / text_file).write_text(ov.slot.text, encoding="utf-8")
            video = graph.one([video], [banner_filter(ov, text_file, font_file)], "v")
            continue
        idx = add_input(*overlay_input_args(ov, fps))
        layer = graph.one([f"{idx}:v:0"], overlay_source_filters(ov, frame_w), "v")
        x, y = position_xy(ov.slot.position, ov.slot.margin, "w", "h")
        video = graph.one([video, layer],
                          [f"overlay=x={x}:y={y}:eof_action=pass:{_enable(ov)}"], "v")
    return video
