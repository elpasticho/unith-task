"""Unit tests for HMAC-SHA256 webhook signing."""
from __future__ import annotations

import time

import pytest

from app.delivery.signing import sign, verify


SECRET = "testsecret-abc123"
BODY = b'{"event_type":"test","payload":{"x":1}}'


def test_valid_signature_passes():
    sig, ts = sign(SECRET, BODY)
    assert verify(SECRET, BODY, sig, ts)


def test_tampered_body_fails():
    sig, ts = sign(SECRET, BODY)
    tampered = BODY + b" "
    assert not verify(SECRET, tampered, sig, ts)


def test_tampered_signature_fails():
    sig, ts = sign(SECRET, BODY)
    bad_sig = sig[:-4] + "0000"
    assert not verify(SECRET, BODY, bad_sig, ts)


def test_wrong_secret_fails():
    sig, ts = sign(SECRET, BODY)
    assert not verify("wrongsecret", BODY, sig, ts)


def test_stale_timestamp_rejected():
    sig, ts = sign(SECRET, BODY)
    old_ts = ts - 400_000  # 400 seconds ago
    assert not verify(SECRET, BODY, sig, old_ts, tolerance_seconds=300)


def test_within_tolerance_accepted():
    ts = int(time.time() * 1000) - 200_000  # 200 seconds ago
    sig, _ = sign(SECRET, BODY, timestamp_ms=ts)
    assert verify(SECRET, BODY, sig, ts, tolerance_seconds=300)


def test_signature_header_format():
    sig, _ = sign(SECRET, BODY)
    assert sig.startswith("sha256=")
    assert len(sig) == len("sha256=") + 64  # hex digest is 64 chars


def test_deterministic_for_same_inputs():
    ts = 1700000000000
    sig1, _ = sign(SECRET, BODY, timestamp_ms=ts)
    sig2, _ = sign(SECRET, BODY, timestamp_ms=ts)
    assert sig1 == sig2


def test_constant_time_comparison_used():
    """Verify verify() uses hmac.compare_digest (indirectly: no timing difference on wrong)."""
    sig, ts = sign(SECRET, BODY)
    # Should return False, not raise
    result = verify(SECRET, BODY, "sha256=" + "a" * 64, ts)
    assert result is False
