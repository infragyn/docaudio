"""Configuracao de logs em arquivo rotativo."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from docaudio.core import paths

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, log_dir: Path | None = None) -> Path:
    """Ativa o log em arquivo e devolve o caminho do arquivo."""
    log_dir = log_dir or paths.logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "docaudio.log"

    root = logging.getLogger()
    root.setLevel(level)
    if not any(getattr(h, "baseFilename", None) == str(log_file) for h in root.handlers):
        handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(handler)
    return log_file
