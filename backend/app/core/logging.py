from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler

from app.core.config import settings

_CONFIGURED = False


def setup_logging() -> None:
    """Configure console + daily rotating file logging for the backend."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = settings.log_dir
    os.makedirs(log_dir, exist_ok=True)

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(log_level)

    # Avoid duplicate handlers when uvicorn reloads modules.
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(log_level)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        filename=os.path.join(log_dir, "backend.log"),
        when="midnight",
        interval=1,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)
    root.addHandler(file_handler)

    # Third-party libraries can be noisy; keep them useful but not spammy.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
