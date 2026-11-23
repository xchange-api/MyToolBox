"""统一日志配置。

整个应用共用名为 ``mytoolbox`` 的 logger，同时输出到标准输出和
按大小滚动的日志文件。
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from core import get_app_dir

# 日志文件与 exe 同目录，受保护路径下可能不可写，详见审计报告 P0-2。
_LOG_FILE = os.path.join(get_app_dir(), "mytoolbox.log")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_level_map = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def setup_logger(level: str = "info"):
    """初始化全局 logger，重复调用不会重复挂载 handler。

    Args:
        level: 日志级别名，取值 ``debug`` / ``info`` / ``warning`` /
            ``error``，无法识别时回退到 ``info``。

    Returns:
        配置完成的 ``mytoolbox`` logger。
    """
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
    """返回全局 logger，不触发初始化。"""
    return logging.getLogger("mytoolbox")
