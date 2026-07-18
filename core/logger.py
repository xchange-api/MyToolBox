import os
import sys
import logging
from logging.handlers import RotatingFileHandler


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_LOG_DIR = _app_dir()
_LOG_FILE = os.path.join(_LOG_DIR, "mytoolbox.log")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_level_map = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logger(level: str = "info"):
    level_num = _level_map.get(level, logging.INFO)
    logger = logging.getLogger("mytoolbox")
    logger.setLevel(level_num)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATE_FORMAT)

    stream = logging.StreamHandler(sys.stdout)
    stream.setLevel(level_num)
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    file_handler = RotatingFileHandler(_LOG_FILE, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setLevel(level_num)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def get_logger():
    return logging.getLogger("mytoolbox")
