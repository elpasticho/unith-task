"""Root conftest — configures structlog for tests."""
from __future__ import annotations

import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
