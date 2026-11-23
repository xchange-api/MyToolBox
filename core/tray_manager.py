"""系统托盘图标与菜单。

全应用只有一个托盘图标，菜单内容由插件列表加上「开机自启」「退出」
两个固定项构成。插件状态变化后调用 ``update_menu`` 刷新勾选状态。
"""

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.logger import get_logger


def _create_default_icon():
    """用 QPainter 现场画一个 64x64 的占位托盘图标。"""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 120, 215))
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 8, 8)

    font = QFont("Segoe UI")
    font.setPixelSize(24)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "TB")
    painter.end()

    return QIcon(pix)


class TrayManager(QObject):
    """托管托盘图标与右键菜单。"""

    def __init__(self, app_ref):
        super().__init__(app_ref)
        self._app = app_ref
        self._log = get_logger()
        self._icon = QSystemTrayIcon(_create_default_icon(), self)
        self._icon.setToolTip("MyToolBox")
        self._menu = QMenu()
        self._actions = {}
        self._autostart_action = None
        self._exit_action = None
        self._icon.setContextMenu(self._menu)
        self._rebuild_menu()

    def start(self):
        """显示托盘图标；系统不支持托盘时只记日志。"""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log.warning("系统托盘不可用")
            return
        self._icon.show()

    def stop(self):
        """隐藏并释放托盘图标与菜单。"""
        try:
            self._icon.hide()
            self._icon.setContextMenu(None)
            self._icon.deleteLater()
        except Exception:
            pass
        try:
            self._menu.deleteLater()
        except Exception:
            pass

    def update_menu(self):
        """刷新菜单项的勾选状态。

        插件列表在启动后不再变化，因此只需同步勾选，不必重建菜单。
        """
        self._sync_checks()

    def _rebuild_menu(self):
        """按「插件项 → 分隔符 → 开机自启 → 分隔符 → 退出」构建菜单。"""
        self._menu.clear()
        self._actions = {}

        for name, plugin in self._app.plugins.items():
            action = QAction(plugin.display_name, self._menu)
            action.setCheckable(True)
            action.setChecked(self._is_active(name))
            action.triggered.connect(
                lambda checked=False, n=name: self._on_toggle(n))
            self._menu.addAction(action)
            self._actions[name] = action

        self._menu.addSeparator()

        self._autostart_action = QAction("开机自启", self._menu)
        self._autostart_action.setCheckable(True)
        self._autostart_action.setChecked(self._app.autostart_enabled)
        self._autostart_action.triggered.connect(
            lambda checked=False: self._app.on_toggle_autostart())
        self._menu.addAction(self._autostart_action)

        self._menu.addSeparator()

        self._exit_action = QAction("退出", self._menu)
        self._exit_action.triggered.connect(
            lambda checked=False: self._app.on_exit())
        self._menu.addAction(self._exit_action)

    def _sync_checks(self):
        """把当前运行状态回写到各菜单项的勾选位。"""
        for name, action in self._actions.items():
            action.setChecked(self._is_active(name))
        if self._autostart_action is not None:
            self._autostart_action.setChecked(self._app.autostart_enabled)

    def _on_toggle(self, name):
        """菜单项被点击时把请求转交应用层处理。"""
        self._app.toggle_plugin(name)

    def _is_active(self, name):
        """插件是否处于运行中（含运行在独立 helper 进程里的插件）。"""
        return (name in self._app.active_plugins
                or name in self._app.active_helper_plugins)
