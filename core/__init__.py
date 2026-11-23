"""应用级公共工具：运行环境判断与目录解析。"""

import os
import sys


def is_frozen():
    """返回当前是否运行在 PyInstaller 打包环境中。"""
    return getattr(sys, "frozen", False)


def get_app_dir():
    """返回应用根目录。

    打包后为 exe 所在目录，源码运行时为项目根目录。注意该目录在
    安装到 ``Program Files`` 等受保护路径时不可写。
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
