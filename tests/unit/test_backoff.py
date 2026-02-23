"""Unit tests for exponential backoff math."""
from __future__ import annotations

import statistics

import pytest

from app.delivery.worker import _backoff_seconds
from app.config import settings


def test_first_attempt_within_bounds():
    delays = [_backoff_seconds(1) for _ in range(200)]
    cap = min(settings.delivery_base_delay_seconds * 2, settings.delivery_max_delay_seconds)
    assert all(0 <= d <= cap for d in delays)


def test_delays_increase_with_attempts():
    """Median delay should grow with attempt count."""
    low_delays = [_backoff_seconds(1) for _ in range(500)]
    high_delays = [_backoff_seconds(6) for _ in range(500)]
    assert statistics.median(high_delays) > statistics.median(low_delays)


def test_capped_at_max_delay():
    delays = [_backoff_seconds(100) for _ in range(200)]
    assert all(d <= settings.delivery_max_delay_seconds for d in delays)


def test_jitter_varies():
    """Two calls at the same attempt should not always return the same value."""
    results = {_backoff_seconds(3) for _ in range(20)}
    assert len(results) > 1  # jitter produces distinct values


def test_non_negative():
    delays = [_backoff_seconds(i) for i in range(15)]
    assert all(d >= 0 for d in delays)
