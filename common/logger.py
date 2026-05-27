from __future__ import annotations

import json
import logging
import time
from logging.handlers import RotatingFileHandler
from typing import Optional

from rich.logging import RichHandler


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def setup_logging(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None,
    log_format: str = "text",
) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    level_value = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(level_value)
    logger.propagate = False

    console_handler = RichHandler(rich_tracebacks=True, show_time=True, show_level=True)
    console_handler.setLevel(level_value)
    logger.addHandler(console_handler)

    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setLevel(level_value)
        if log_format == "json":
            file_handler.setFormatter(JsonFormatter())
        else:
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
