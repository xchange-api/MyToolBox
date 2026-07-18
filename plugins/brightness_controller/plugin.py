import os
import sys
import threading

import pystray
from PIL import Image

from plugins.base import Plugin
from core.logger import get_logger


def _is_frozen():
    return getattr(sys, "frozen", False)


def _get_icon_path():
    if _is_frozen():
        return os.path.join(sys._MEIPASS, "plugins", "brightness_controller", "assets", "fluent.ico")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fluent.ico")


class BrightnessPlugin(Plugin):
    name = "brightness_controller"
    display_name = "亮度控制"
    description = "DDC/CI 显示器亮度调节，左键单击太阳图标弹出滑块"
    category = "系统工具"
    needs_admin = False
    default_config = {
        "enabled": True,
    }

    def __init__(self):
        super().__init__()
        self._log = get_logger()
        self._icon = None
        self._tray_thread = None
        self._flyout = None

    def on_load(self, config):
        self._config = config
        self._icon_path = _get_icon_path()

    def on_start(self):
        from plugins.brightness_controller.flyout import FlyoutWindow, create_tray_icon

        self._flyout = FlyoutWindow(self.app.tk_root, self._icon_path)

        def on_activate(*args):
            self.app.schedule_ui(self._flyout.show)

        self._icon = create_tray_icon(self._icon_path, on_activate)
        self._tray_thread = threading.Thread(target=self._icon.run, daemon=True)
        self._tray_thread.start()
        self._log.info("亮度控制插件已启动")

    def on_stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        if self._flyout:
            self.app.schedule_ui(self._flyout.destroy)
            self._flyout = None
        self._log.info("亮度控制插件已停止")

    def on_config_change(self, new_config):
        pass

    def get_menu_items(self):
        return []
