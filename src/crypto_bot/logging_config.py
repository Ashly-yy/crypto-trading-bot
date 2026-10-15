"""Centralized console and rotating-file logging configuration."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from crypto_bot.config.settings import Settings

_HANDLER_MARKER = "_crypto_bot_managed_handler"


def configure_logging(settings: Settings, log_dir: Path | None = None) -> Path:
    """Configure idempotent console and UTF-8 rotating-file logging.

    Log messages must never include API keys, tokens, complete environment settings,
    or other secrets. The caller is responsible for logging only safe fields.
    """
    directory = log_dir if log_dir is not None else Path("logs")
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "crypto_bot.log"

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            root.removeHandler(handler)
            handler.close()

    level = getattr(logging, settings.log_level)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    setattr(console_handler, _HANDLER_MARKER, True)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    setattr(file_handler, _HANDLER_MARKER, True)
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    root.setLevel(level)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
    return log_path