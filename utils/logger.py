"""Structured and secure logging configuration for algorithmic trading operations.

Provides formatted console and file logging with automatic redaction of sensitive
credentials, such as API keys and secrets, preventing leakage into log streams.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import ClassVar


class SensitiveDataFilter(logging.Filter):
    """Filter that masks sensitive tokens, API keys, and secret values in log messages."""

    # Patterns matching potential credentials, secrets, or keys
    PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"(?i)(api[_-]?key\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-]{8,})(['\"]?)"),
        re.compile(r"(?i)(api[_-]?secret\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-]{8,})(['\"]?)"),
        re.compile(r"(?i)(secret\s*[:=]\s*['\"]?)([a-zA-Z0-9_\-]{8,})(['\"]?)"),
        re.compile(r"(?i)(authorization\s*[:=]\s*['\"]?Bearer\s+)([a-zA-Z0-9_\-\.]{8,})(['\"]?)"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._redact_value(v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._redact_value(v) for v in record.args)
        return True

    @classmethod
    def redact(cls, text: str) -> str:
        for pattern in cls.PATTERNS:
            text = pattern.sub(r"\1********\3", text)
        return text

    @classmethod
    def _redact_value(cls, val: object) -> object:
        if isinstance(val, str):
            return cls.redact(val)
        return val


class ColorFormatter(logging.Formatter):
    """ANSI color-enabled formatter for enhanced console readability."""

    GREY = "\033[38;20m"
    BLUE = "\033[34;20m"
    GREEN = "\033[32;20m"
    YELLOW = "\033[33;20m"
    RED = "\033[31;20m"
    BOLD_RED = "\033[31;1m"
    RESET = "\033[0m"

    FORMAT_STR = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    LEVEL_COLORS: ClassVar[dict[int, str]] = {
        logging.DEBUG: GREY,
        logging.INFO: GREEN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: BOLD_RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        formatter = logging.Formatter(
            fmt=f"{color}%(asctime)s [{record.levelname:>5s}]{self.RESET} %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        return formatter.format(record)


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Initialize root and application loggers with secure formatting.

    Parameters
    ----------
    log_level : str
        Desired logging level: DEBUG, INFO, WARNING, ERROR.

    Returns
    -------
    logging.Logger
        Configured root logger.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Avoid duplicate handlers on re-initialization
    if not root_logger.handlers:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(numeric_level)

        if sys.stdout.isatty():
            console_handler.setFormatter(ColorFormatter())
        else:
            standard_formatter = logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            console_handler.setFormatter(standard_formatter)

        console_handler.addFilter(SensitiveDataFilter())
        root_logger.addHandler(console_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Retrieve a child logger with the application logging format.

    Parameters
    ----------
    name : str
        Logger component identifier.

    Returns
    -------
    logging.Logger
        Child logger instance.
    """
    return logging.getLogger(name)
