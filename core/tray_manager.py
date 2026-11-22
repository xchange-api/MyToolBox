from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from core.logger import get_logger


def _create_default_icon():
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
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._log.warning("系统托盘不可用")
            return
        self._icon.show()

    def stop(self):
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
        self._sync_checks()

    def _rebuild_menu(self):
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
        for name, action in self._actions.items():
            action.setChecked(self._is_active(name))
        if self._autostart_action is not None:
            self._autostart_action.setChecked(self._app.autostart_enabled)

    def _on_toggle(self, name):
        self._app.toggle_plugin(name)

    def _is_active(self, name):
        return (name in self._app.active_plugins
                or name in self._app.active_helper_plugins)
