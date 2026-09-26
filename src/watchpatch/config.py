import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

REQUEST_TIMEOUT = 20
MAX_RETRIES = 2
MIN_INTERVAL_MINUTES = 5
MAX_DIFF_LINES = 200


def database_path() -> Path:
    override = os.getenv("WATCHPATCH_DB")
    if override:
        return Path(override).expanduser().resolve()
    base = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local" / "share")))
    return base / "WatchPatch" / "watchpatch.db"


def log_path() -> Path:
    return database_path().with_name("watchpatch.log")


def setup_logging() -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("watchpatch")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    scheduler_logger = logging.getLogger("apscheduler")
    scheduler_logger.handlers = [handler]
    scheduler_logger.setLevel(logging.WARNING)
    scheduler_logger.propagate = False
