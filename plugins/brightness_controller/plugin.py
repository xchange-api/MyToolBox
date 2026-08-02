import ctypes
import os
import sys
import threading
from ctypes import wintypes

from core import is_frozen
from plugins.base import Plugin

_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_MOD_CTRL = 0x0002
_MOD_ALT = 0x0001
_VK_UP = 0x26
_VK_DOWN = 0x28
_HOTKEY_UP = 1
_HOTKEY_DOWN = 2


def _get_icon_path():
    if is_frozen():
        return os.path.join(sys._MEIPASS, "plugins", "brightness_controller", "assets", "fluent.ico")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fluent.ico")


class BrightnessPlugin(Plugin):
    name = "brightness_controller"
    display_name = "亮度控制"
    description = "DDC/CI 显示器亮度调节，左键单击太阳图标弹出滑块，Ctrl+Alt+↑/↓ 调节亮度"
    category = "系统工具"
    needs_admin = False
    default_config = {
        "enabled": True,
    }

    def __init__(self):
        super().__init__()
        self._icon = None
        self._tray_thread = None
        self._flyout = None
        self._hotkey_thread = None
        self._hotkey_tid = 0

    def on_load(self, config):
        self._config = config
        self._icon_path = _get_icon_path()

    def on_start(self):
        from plugins.brightness_controller.flyout import FlyoutWindow, create_tray_icon

        self._flyout = FlyoutWindow(self.app, self.app.tk_root, self._icon_path)

        def on_activate(*args):
            self.app.schedule_ui(self._flyout.show)

        self._icon = create_tray_icon(self._icon_path, on_activate)
        self._tray_thread = threading.Thread(target=self._icon.run, daemon=True)
        self._tray_thread.start()
        self._start_hotkey()

    def on_stop(self):
        self._stop_hotkey()
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        if self._flyout:
            self.app.schedule_ui(self._flyout.destroy)
            self._flyout = None

    def _start_hotkey(self):
        self._hotkey_thread = threading.Thread(target=self._hotkey_loop, daemon=True)
        self._hotkey_thread.start()

    def _stop_hotkey(self):
        user32 = ctypes.windll.user32
        if self._hotkey_tid:
            user32.PostThreadMessageW(self._hotkey_tid, _WM_QUIT, 0, 0)
        if self._hotkey_thread:
            self._hotkey_thread.join(timeout=2)
            self._hotkey_thread = None
        self._hotkey_tid = 0

    def _hotkey_loop(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._hotkey_tid = kernel32.GetCurrentThreadId()

        ok_up = user32.RegisterHotKey(None, _HOTKEY_UP, _MOD_CTRL | _MOD_ALT, _VK_UP)
        ok_down = user32.RegisterHotKey(None, _HOTKEY_DOWN, _MOD_CTRL | _MOD_ALT, _VK_DOWN)
        if not ok_up or not ok_down:
            if ok_up:
                user32.UnregisterHotKey(None, _HOTKEY_UP)
            if ok_down:
                user32.UnregisterHotKey(None, _HOTKEY_DOWN)
            return

        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            if msg.message == _WM_HOTKEY:
                if msg.wParam == _HOTKEY_UP:
                    self.app.schedule_ui(self._flyout.brightness_up)
                elif msg.wParam == _HOTKEY_DOWN:
                    self.app.schedule_ui(self._flyout.brightness_down)

        user32.UnregisterHotKey(None, _HOTKEY_UP)
        user32.UnregisterHotKey(None, _HOTKEY_DOWN)

    def on_config_change(self, new_config):
        pass

    def get_menu_items(self):
        return []
