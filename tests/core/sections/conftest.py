from __future__ import annotations

import logging

import pytest


@pytest.fixture
def ada_warnings():
    """Warnings from adapy's own logger. ``configure_logger`` turns propagation off, so ``caplog``
    -- which listens on the root -- never sees them; this attaches to the ``ada`` logger itself."""
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger("ada")
    handler = _Collector(level=logging.WARNING)
    previous = logger.level
    logger.addHandler(handler)
    logger.setLevel(min(previous or logging.WARNING, logging.WARNING))
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
