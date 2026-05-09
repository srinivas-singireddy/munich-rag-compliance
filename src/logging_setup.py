"""Structured logging setup. Outputs JSON in production, pretty in dev."""

import logging
import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog to output pretty colored logs to console."""
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper()),
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
