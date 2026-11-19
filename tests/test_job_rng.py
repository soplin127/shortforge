from pathlib import Path

from core.batch import Job, job_rng


def _draw(path, variant=1, seed="s"):
    return job_rng(seed, Job(0, Path(path), variant)).random()


def test_same_name_in_different_folders_differs():
    assert _draw("/x/day1/clip.mp4") != _draw("/x/day2/clip.mp4")


def test_same_path_is_reproducible():
    assert _draw("/x/day1/clip.mp4") == _draw("/x/day1/clip.mp4")


def test_variants_differ():
    assert _draw("/x/clip.mp4", 1) != _draw("/x/clip.mp4", 2)
