"""Collecting input videos from folders, file lists and drag-and-drop."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv"})


def is_video(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and not path.name.startswith(".")


def _file_key(path: Path) -> tuple:
    """Same file, same key: also for two spellings of one name on a case-insensitive disk.

    Paths are not lowercased, so "A.mp4" and "a.mp4" stay two files where the disk
    is case-sensitive. A zero inode (some network shares) falls back to the path.
    """
    st = path.stat()
    return (st.st_dev, st.st_ino) if st.st_ino else ("path", str(path))


def _folder_videos(folder: Path, recursive: bool, skipped: list[Path]) -> Iterable[Path]:
    """Videos in ``folder``, leaving out anything under a skipped subfolder."""
    root = folder.resolve()
    # A skipped folder equal to the scanned one would hide everything; only nested ones count.
    nested = [s for s in skipped if s != root and s.is_relative_to(root)]
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    for item in candidates:
        if is_video(item) and not any(item.resolve().is_relative_to(s) for s in nested):
            yield item.resolve()


def collect_sources(
    paths: Iterable[str | Path],
    recursive: bool = False,
    exclude: Iterable[str | Path] = (),
) -> list[Path]:
    """Expand files/folders into a sorted, de-duplicated list of video files.

    Folders in ``exclude`` (the output folder) are skipped while expanding an input folder,
    so a re-run does not pick up its own results.
    """
    skipped = [Path(raw).expanduser().resolve() for raw in exclude if raw]
    found: dict[tuple, Path] = {}
    for raw in paths:
        path = Path(raw).expanduser()
        if path.is_dir():
            for item in _folder_videos(path, recursive, skipped):
                found.setdefault(_file_key(item), item)
        elif is_video(path):
            found.setdefault(_file_key(path), path.resolve())
    return sorted(found.values(), key=lambda p: (str(p).lower(), str(p)))
