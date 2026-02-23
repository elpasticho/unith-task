# tests/

Test suite. Separated into unit tests (no external services) and integration tests (real Postgres + RabbitMQ via testcontainers).

```bash
# Unit tests only — fast, no dependencies
pytest tests/unit -v

# Integration tests — spins up containers automatically
pytest tests/integration -v --timeout=120

# Full suite with coverage
pytest tests/ --cov=app --cov-report=term-missing
```

## Sub-directories

- [`unit/`](unit/README.md) — pure logic tests, run in < 1 s
- [`integration/`](integration/README.md) — end-to-end tests against real infrastructure

## `conftest.py`

Root-level conftest. Configures structlog to use `ConsoleRenderer` during test runs so log output is readable.
