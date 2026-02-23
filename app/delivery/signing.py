"""HMAC-SHA256 webhook signing and verification (Stripe model)."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional


def sign(secret: str, body: bytes, timestamp_ms: Optional[int] = None) -> tuple[str, int]:
    """Return (signature_header_value, timestamp_ms).

    canonical_msg = f"{timestamp_ms}.{body_bytes}"
    signature    = HMAC-SHA256(secret, canonical_msg)
    header value = "sha256=<hex>"
    """
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)

    canonical = f"{timestamp_ms}.".encode() + body
    sig = hmac.new(secret.encode(), canonical, hashlib.sha256).hexdigest()
    return f"sha256={sig}", timestamp_ms


def verify(
    secret: str,
    body: bytes,
    signature_header: str,
    timestamp_ms: int,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify a webhook signature.

    Returns False (never raises) so callers can log and continue.
    """
    # Replay protection
    age_ms = abs(int(time.time() * 1000) - timestamp_ms)
    if age_ms > tolerance_seconds * 1000:
        return False

    expected_sig, _ = sign(secret, body, timestamp_ms)
    # Constant-time comparison
    return hmac.compare_digest(expected_sig.encode(), signature_header.encode())
