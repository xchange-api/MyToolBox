# -*- coding: utf-8 -*-
"""多显示器亮度浮窗的本地 Mock。

用途：没有第二台显示器时验证“选择显示器”浮窗的布局和交互。
它只替换显示器枚举与读写函数，界面用的是真实的 BrightnessFlyout。

运行:
    .\\.venv\\Scripts\\python.exe tools\\mock_brightness_flyout.py
    .\\.venv\\Scripts\\python.exe tools\\mock_brightness_flyout.py --selftest

正常模式会在托盘放一个带红点的太阳图标：
    - 左键点击：显示亮度浮窗
    - 右键菜单：显示浮窗 / 重置亮度 / 退出 Mock
"""

import os
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QMenu, QSystemTrayIcon,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.brightness_controller import flyout as flyout_module
from plugins.brightness_controller.ddcci import MonitorInfo
from plugins.brightness_controller.flyout import (
    BrightnessFlyout, _make_sun_pixmap,
)

MOCK_DATA = [
    ("LG ULTRAWIDE", r"\\.\DISPLAY1", 80),
    ("HKC 27E1Q", r"\\.\DISPLAY2", 35),
    ("DELL U2419H", r"\\.\DISPLAY3", 60),
    ("AOC 24G2", r"\\.\DISPLAY4", 45),
    ("SAMSUNG S24R350", r"\\.\DISPLAY5", 55),
    ("", r"\\.\DISPLAY6", 70),  # 模拟 EDID 读不到型号的显示器
]


def make_monitors():
    return [
        MonitorInfo(
            i + 1, i + 1, "Generic PnP Monitor", 0, 100, value, device, name,
        )
        for i, (name, device, value) in enumerate(MOCK_DATA)
    ]


class UiBridge(QObject):
    """模拟 core.app.schedule_ui 的跨线程 UI 调度"""

    _sig = Signal(object)

    def __init__(self):
        super().__init__()
        self._sig.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def schedule_ui(self, fn):
        self._sig.emit(fn)

    def _run(self, fn):
        fn()


def install_mocks(monitors):
    """把浮窗模块里的系统调用替换成假数据"""
    state = {"monitors": monitors}

    def fake_enumerate_async(callback):
        callback(list(state["monitors"]))

    def fake_get(monitor):
        return monitor.current

    def fake_set(monitor, value):
        monitor.current = max(monitor.range[0], min(monitor.range[1], value))

    def fake_set_async(monitor, value):
        threading.Thread(
            target=fake_set, args=(monitor, value), daemon=True).start()

    flyout_module.enumerate_monitors_async = fake_enumerate_async
    flyout_module.cursor_monitor = lambda: 1
    flyout_module.get_brightness = fake_get
    flyout_module.set_brightness = fake_set
    flyout_module.set_brightness_async = fake_set_async
    flyout_module.cleanup = lambda monitors: None
    flyout_module.AUTO_MS = 60_000  # 方便观察，免得 2.5 秒就自动关掉
    return state


def mock_icon():
    """太阳图标加一个红点，方便和正式托盘图标区分"""
    pix = _make_sun_pixmap(64)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#e81123"))
    painter.drawEllipse(44, 44, 18, 18)
    painter.end()
    return QIcon(pix)


def run_selftest(app, flyout):
    """验证 Mock 路径能正常创建浮窗、展开并切换显示器"""
    def check():
        print("monitors =", len(flyout._monitors))
        print("titles =", flyout._titles)
        print("selected =", flyout._selected.friendly or "(fallback)")
        print("collapsed size =", flyout.width(), flyout.height())
        flyout._set_expanded(True)
        print("expanded size =", flyout.width(), flyout.height())
        flyout._select_monitor(flyout._monitors[1])
        print("after select =", flyout._selected.friendly,
              "value =", flyout._value_label.text())

        flyout._set_expanded(True)
        pix = flyout.grab()
        path = os.path.join(
            os.environ.get("TEMP", "."), "mock_flyout_selftest.png")
        pix.save(path)
        print("screenshot =", path)

        flyout.destroy()
        app.quit()

    flyout.show()
    QTimer.singleShot(1200, check)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("BrightnessMock")
    app.setQuitOnLastWindowClosed(False)

    monitors = make_monitors()
    install_mocks(monitors)

    bridge = UiBridge()
    flyout = BrightnessFlyout(bridge, _icon_path())

    if "--selftest" in sys.argv:
        run_selftest(app, flyout)
        return app.exec()

    tray = QSystemTrayIcon(mock_icon(), app)
    tray.setToolTip("亮度浮窗 Mock（6 台模拟显示器）")

    menu = QMenu()
    show_action = menu.addAction("显示亮度浮窗")
    show_action.triggered.connect(flyout.show)
    reset_action = menu.addAction("重置模拟亮度")
    expand_action = menu.addAction("展开显示器列表")

    def reset():
        for i, (_, _, value) in enumerate(MOCK_DATA):
            monitors[i].current = value
        flyout._close()
        flyout.show()

    def expand():
        flyout.show()
        QTimer.singleShot(700, lambda: flyout._set_expanded(True))

    reset_action.triggered.connect(reset)
    expand_action.triggered.connect(expand)
    menu.addSeparator()
    quit_action = menu.addAction("退出 Mock")
    quit_action.triggered.connect(lambda: (flyout.destroy(), app.quit()))

    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: flyout.show()
        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()

    # 启动后直接展示一次，并自动展开列表
    QTimer.singleShot(500, flyout.show)
    QTimer.singleShot(1500, lambda: flyout._set_expanded(True))

    # 保持菜单和托盘引用，避免被回收
    app._mock_tray = tray
    app._mock_menu = menu

    print("Mock 已启动：托盘图标是带红点的太阳，左键显示浮窗。")
    return app.exec()


def _icon_path():
    return os.path.join(
        ROOT, "plugins", "brightness_controller", "assets", "fluent.ico")


if __name__ == "__main__":
    raise SystemExit(main())
