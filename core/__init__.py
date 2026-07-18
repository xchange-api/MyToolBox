import os
import sys


def is_frozen():
    return getattr(sys, "frozen", False)


def get_app_dir():
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
