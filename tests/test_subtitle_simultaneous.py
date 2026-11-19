from core.config import SubtitleSettings
from core.timeline import build_timeline
from features.subtitles import Cue, build_events, spread_words


def _cue(start, end, text):
    return Cue(start, end, spread_words(start, end, text))


def _events(cues):
    return build_events(cues, build_timeline(0, 10, [], 1.0), SubtitleSettings(shorts_style=False))


def test_cues_with_same_start_are_merged():
    events = _events([_cue(1, 3, "first speaker"), _cue(1, 4, "second speaker")])
    assert [(e.start, e.end, e.text) for e in events] == [(1, 4, "first speaker second speaker")]


def test_sequential_cues_stay_separate():
    events = _events([_cue(1, 2, "one"), _cue(5, 6, "two")])
    assert [e.text for e in events] == ["one", "two"]
