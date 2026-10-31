import pytest

from core.batch import EtaEstimator, format_eta


def test_eta_unknown_before_minimum_progress():
    eta = EtaEstimator(min_progress=0.05)

    assert eta.update(0.0, 1.0) is None
    assert eta.update(0.01, 2.0) is None


def test_eta_is_zero_when_complete():
    eta = EtaEstimator()

    assert eta.update(1.0, 10.0) == 0.0


def test_eta_first_estimate_is_linear_extrapolation():
    eta = EtaEstimator(min_progress=0.05)

    assert eta.update(0.25, 10.0) == pytest.approx(30.0)


def test_eta_stays_known_once_estimated():
    eta = EtaEstimator(min_progress=0.05)
    eta.update(0.5, 10.0)

    assert eta.update(0.5, 11.0) is not None
    assert eta.update(0.99, 20.0) is not None
    assert eta.update(1.0, 21.0) == 0.0


def test_eta_smooths_sudden_jumps():
    eta = EtaEstimator(min_progress=0.05, smoothing=0.3)
    first = eta.update(0.5, 10.0)  # raw 10 s left

    second = eta.update(0.55, 30.0)  # raw ~24.5 s left: a slow stretch

    raw = 30.0 / 0.55 * 0.45
    assert first < second < raw


def test_format_eta_shows_hours_for_long_runs():
    assert format_eta(3725) == "1:02:05"
    assert format_eta(65) == "01:05"
    assert format_eta(None) == "--:--"
