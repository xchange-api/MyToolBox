import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon

from core import is_frozen
from core.logger import get_logger
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
    description = "DDC/CI 显示器亮度调节，左键单击太阳图标弹出滑块，Ctrl+Alt+↑/↓ 调节亮度，可选 Windows 关机时软关显示器"
    category = "系统工具"
    needs_admin = False
    default_config = {
        "enabled": True,
        "power_off_on_shutdown": False,
    }

    def __init__(self):
        super().__init__()
        self._log = get_logger()
        self._icon = None
        self._flyout = None
        self._hotkey_thread = None
        self._hotkey_tid = 0
        self._shutdown_hook = None

    def on_load(self, config):
        self._config = config
        self._icon_path = _get_icon_path()

    def on_start(self):
        from plugins.brightness_controller.flyout import BrightnessFlyout

        enabled = bool(self._config.get("power_off_on_shutdown", False))
        if enabled:
            self._wake_screen()
        self._flyout = BrightnessFlyout(self.app, self._icon_path)

        icon = QIcon(self._icon_path)
        if icon.isNull():
            from plugins.brightness_controller.flyout import _make_sun_pixmap
            icon = QIcon(_make_sun_pixmap(64))
        self._icon = QSystemTrayIcon(icon, self.app)
        self._icon.setToolTip("亮度控制")
        self._icon.activated.connect(self._on_icon_activated)
        self._icon.show()

        self._start_hotkey()
        if bool(self._config.get("power_off_on_shutdown", False)):
            self._start_shutdown_hook()

    def _on_icon_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self._flyout:
            self.app.schedule_ui(self._flyout.show)

    def _wake_screen(self):
        """启动时点亮显示器：深度软关（0xD6=4）跨断电保存，重启时的视频信号
        唤不醒显示器，必须由 DDC 指令恢复，否则开机后一直黑屏。"""
        def _worker():
            from plugins.brightness_controller import ddcci
            for attempt in range(4):
                try:
                    ok, total = ddcci.set_all_power(ddcci.POWER_ON)
                    if total and ok == total:
                        self._log.info(f"启动点亮显示器: {ok}/{total} 台成功")
                        return
                    self._log.warning(f"启动点亮显示器失败: {ok}/{total} (第 {attempt + 1}/4 次)")
                except Exception as e:
                    self._log.warning(f"启动点亮显示器异常: {e}")
                time.sleep(1.5)
        threading.Thread(target=_worker, daemon=True).start()

    def _start_shutdown_hook(self):
        from plugins.brightness_controller.shutdown_hook import ShutdownHook

        enabled = bool(self._config.get("power_off_on_shutdown", False))
        self._shutdown_hook = ShutdownHook(self.app, enabled)
        self._shutdown_hook.start()

    def on_stop(self):
        self._stop_hotkey()
        if self._shutdown_hook:
            self._shutdown_hook.stop()
            self._shutdown_hook = None
        if self._icon:
            try:
                self._icon.hide()
                self._icon.deleteLater()
            except Exception:
                pass
            self._icon = None
        if self._flyout:
            self._flyout.destroy()
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
                    self.app.schedule_ui(self._hotkey_up)
                elif msg.wParam == _HOTKEY_DOWN:
                    self.app.schedule_ui(self._hotkey_down)

        user32.UnregisterHotKey(None, _HOTKEY_UP)
        user32.UnregisterHotKey(None, _HOTKEY_DOWN)

    def _hotkey_up(self):
        # 在 GUI 线程执行，避免插件停止后访问已销毁的浮窗。
        if self._flyout is not None:
            self._flyout.brightness_up()

    def _hotkey_down(self):
        if self._flyout is not None:
            self._flyout.brightness_down()

    def on_config_change(self, new_config):
        self._config = new_config
        if not self.app:
            return
        want = bool(new_config.get("power_off_on_shutdown", False))
        # 不能用 bool(self._shutdown_hook)：对象可能在功能未启用时就被创建，
        # 那样「启动时关闭、运行中直接开启」会进入不了任何分支。
        active = bool(self._shutdown_hook and self._shutdown_hook.is_active())
        if want and not active and self._flyout:
            self._start_shutdown_hook()
        elif not want and self._shutdown_hook:
            self._shutdown_hook.stop()
            self._shutdown_hook = None

    def get_menu_items(self):
        return []
