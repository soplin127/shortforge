from pathlib import Path

import pytest

from core.state import STATE_FILE, StateStore


@pytest.mark.parametrize("payload", [b"\xff\xfe{garbage", b"{not json", b"[1, 2]", b""])
def test_corrupt_manifest_is_ignored(tmp_path, payload):
    (tmp_path / STATE_FILE).write_bytes(payload)
    store = StateStore(tmp_path)
    assert store.finished_output(Path("a.mp4"), 1) is None


def test_corrupt_manifest_is_rewritten(tmp_path):
    (tmp_path / STATE_FILE).write_bytes(b"\xff\xfe{garbage")
    out = tmp_path / "out.mp4"
    out.write_bytes(b"x")
    StateStore(tmp_path).mark_done(Path("a.mp4"), 1, out)
    assert StateStore(tmp_path).finished_output(Path("a.mp4"), 1) == out
