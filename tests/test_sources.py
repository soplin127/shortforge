import os

import pytest

from core.sources import collect_sources


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def test_recursive_scan_skips_output_folder_inside_input(tmp_path):
    clip = _touch(tmp_path / "in" / "clip.mp4")
    _touch(tmp_path / "in" / "out" / "IMG_1234.mp4")
    nested = _touch(tmp_path / "in" / "sub" / "other.mov")

    found = collect_sources([tmp_path / "in"], recursive=True, exclude=[tmp_path / "in" / "out"])

    assert found == sorted([clip.resolve(), nested.resolve()], key=lambda p: str(p).lower())


def test_output_folder_equal_to_input_folder_does_not_hide_inputs(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")

    assert collect_sources([tmp_path], exclude=[tmp_path]) == [clip.resolve()]


def test_empty_exclude_entry_is_ignored(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")

    assert collect_sources([tmp_path], recursive=True, exclude=[""]) == [clip.resolve()]


def test_explicit_file_is_kept_even_inside_excluded_folder(tmp_path):
    clip = _touch(tmp_path / "out" / "IMG_1.mp4")

    assert collect_sources([clip], exclude=[tmp_path / "out"]) == [clip.resolve()]


def test_non_videos_and_hidden_files_are_skipped(tmp_path):
    clip = _touch(tmp_path / "a.MP4")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / ".a.part.mp4")

    assert collect_sources([tmp_path]) == [clip.resolve()]


def test_same_file_given_twice_is_listed_once(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")

    found = collect_sources([tmp_path, clip, tmp_path / "." / "clip.mp4"], recursive=True)

    assert found == [clip.resolve()]


def test_hard_link_is_the_same_source(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")
    os.link(clip, tmp_path / "link.mp4")

    assert len(collect_sources([tmp_path])) == 1


def test_names_differing_only_in_case_are_distinct_on_case_sensitive_disk(tmp_path):
    upper = _touch(tmp_path / "Clip.mp4")
    lower = tmp_path / "clip.mp4"
    if lower.exists():
        pytest.skip("case-insensitive file system")
    _touch(lower)

    assert collect_sources([tmp_path]) == [upper.resolve(), lower.resolve()]


def test_other_spelling_on_case_insensitive_disk_is_listed_once(tmp_path):
    clip = _touch(tmp_path / "clip.mp4")
    if not (tmp_path / "CLIP.mp4").exists():
        pytest.skip("case-sensitive file system")

    assert len(collect_sources([tmp_path, tmp_path / "CLIP.mp4"])) == 1
