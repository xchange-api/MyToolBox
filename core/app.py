"""应用主体：串联配置、插件、托盘、IPC 与 UI 调度。

``MyToolBoxApp`` 持有一切跨模块的共享状态（插件表、运行中的插件集合、
helper 进程 PID 等），是托盘菜单、插件与 IPC 之间的中枢。
"""

import os
import sys

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

from core import is_frozen
from core.config_manager import ConfigManager
from core.ipc_server import IpcServer
from core.logger import setup_logger, get_logger
from core.plugin_manager import PluginManager
from core.tray_manager import TrayManager
from core.ui_dispatcher import UiDispatcher


def _get_exe_path():
    """返回当前可执行文件路径。

    打包后为 exe 自身；源码运行时为 Python 解释器路径，调用方需要
    在非打包场景下自行拼接脚本参数。
    """
    return sys.executable


class MyToolBoxApp(QObject):
    """应用主对象。

    插件分为两类：普通插件在 Core 进程内直接运行（记录在
    ``_active_plugins``）；需要管理员权限的插件以 ``--helper`` 子进程
    运行，通过 IPC 通信（同时记录在 ``_active_helper_plugins``）。
    """

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self._qt_app = qt_app
        self._qt_app.setQuitOnLastWindowClosed(False)

        self.config = ConfigManager()
        self.plugin_mgr = PluginManager(self.config)
        self.ipc = IpcServer()
        self.tray = None

        self._dispatcher = UiDispatcher(self)
        self._qt_app.aboutToQuit.connect(self.shutdown)

        # 供关机关屏钩子子类化用的隐藏顶层窗口。必须在 GUI 线程创建，
        # 并且整个生命周期内保持同一个 HWND。
        self._native_window = QWidget()
        self._native_window.setWindowFlags(Qt.WindowType.Tool)
        self._native_window.setAttribute(
            Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        self._native_window.winId()

        self._active_plugins = set()
        self._active_helper_plugins = set()
        self._helper_statuses = {}
        self._helper_pids = {}
        self._autostart_enabled = False

        self._log = None
        self._shutting_down = False

    @property
    def qt_app(self):
        """底层 ``QApplication``。"""
        return self._qt_app

    @property
    def native_window(self):
        """供 WndProc 子类化使用的隐藏窗口，整个进程生命周期不变。"""
        return self._native_window

    def schedule_ui(self, callback):
        """把 ``callback`` 投递到 GUI 线程执行，可从任意线程调用。"""
        self._dispatcher.post(callback)

    def initialize(self):
        """初始化日志、插件与托盘，并按配置启动已启用的插件。

        步骤：发现插件 → 加载配置 → 初始化日志 → 读取自启状态 →
        逐插件 ``on_load`` → 注入 ``app`` 引用 → 启动已启用插件 →
        创建托盘。

        Note:
            日志初始化放在配置加载之后，因此配置读写阶段尚无日志可用。
        """
        self.plugin_mgr.discover()
        self.config.load(self.plugin_mgr.plugins)
        log_level = self.config.get_general().get("log_level", "info")
        setup_logger(log_level)
        self._log = get_logger()

        self._autostart_enabled = self._check_autostart()
        self.plugin_mgr.load_all()

        for name, plugin in self.plugin_mgr.plugins.items():
            plugin.app = self

        self.ipc.set_message_callback(self._on_ipc_message)

        for name, plugin in self.plugin_mgr.plugins.items():
            cfg = self.config.get_plugin_config(name)
            if cfg.get("enabled"):
                self._start_plugin(name)

        self.tray = TrayManager(self)

    def run(self):
        """显示托盘图标并进入 Qt 事件循环。

        Returns:
            Qt 事件循环的退出码。
        """
        self.tray.start()
        return self._qt_app.exec()

    def shutdown(self):
        """关闭应用：先让 helper 退出，再停插件、托盘与调度器。

        幂等，``aboutToQuit`` 与托盘「退出」都会走到这里。
        """
        if self._shutting_down:
            return
        self._shutting_down = True

        if self._log:
            self._log.info("正在关闭 MyToolBox...")

        # 先通知 admin helper 退出，再关闭管道
        for name in list(self._active_helper_plugins):
            self._stop_helper_plugin(name)

        self.ipc.stop_all()
        self.plugin_mgr.stop_all()

        if self.tray:
            self.tray.stop()

        self._dispatcher.stop()

        try:
            self._native_window.close()
            self._native_window.deleteLater()
        except Exception:
            pass

        self._qt_app.quit()

    def toggle_plugin(self, name):
        """按当前状态启动或停止指定插件，供托盘菜单调用。"""
        if name in self._active_plugins or name in self._active_helper_plugins:
            self._stop_plugin(name)
        else:
            self._start_plugin(name)

    def _start_plugin(self, name):
        """启动插件；``needs_admin`` 的走提权 helper 分支。"""
        if name in self._active_plugins or name in self._active_helper_plugins:
            return
        plugin = self.plugin_mgr.plugins.get(name)
        if not plugin:
            return
        if plugin.needs_admin:
            self._start_helper_plugin(name, plugin)
        else:
            self.plugin_mgr.start_plugin(name)
            self._active_plugins.add(name)
        self._update_menu()

    def _stop_plugin(self, name):
        """停止插件并清理运行状态。"""
        plugin = self.plugin_mgr.plugins.get(name)
        if not plugin:
            return
        if plugin.needs_admin:
            self._stop_helper_plugin(name)
            self._active_plugins.discard(name)
        else:
            self.plugin_mgr.stop_plugin(name)
            self._active_plugins.discard(name)
        self._active_helper_plugins.discard(name)
        self._update_menu()

    def _start_helper_plugin(self, name, plugin):
        """开管道监听并通过 UAC 拉起 ``--helper`` 子进程。

        Note:
            ``ShellExecuteW`` 的返回值（小于 32 表示失败，例如用户在 UAC
            弹窗点「否」）未被检查，插件仍会被标记为已启用；也没有连接
            超时回滚（审计报告 P1-4）。
        """
        self.ipc.listen(name)
        exe = _get_exe_path()
        if is_frozen():
            args = f'--helper {name} --core-pid {os.getpid()}'
        else:
            script = os.path.abspath(sys.argv[0])
            args = f'"{script}" --helper {name} --core-pid {os.getpid()}'
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 0)
        self._active_helper_plugins.add(name)
        self._helper_statuses[name] = {"running": False, "pids": []}

    def _stop_helper_plugin(self, name):
        """向 helper 发送停止指令并清理本地状态。

        Note:
            只发信不等确认，也不按 PID 兜底终止；若 helper 当时尚未连上
            管道，消息会被静默丢弃而进程继续存活（审计报告 P1-5）。
        """
        self.ipc.send(name, {"action": "stop"})
        self._active_helper_plugins.discard(name)
        self._helper_statuses.pop(name, None)

    def _on_ipc_message(self, plugin_name, msg):
        """处理 helper 上报的消息。

        在 IPC 读取线程上执行，因此涉及 UI 的更新都通过
        ``schedule_ui`` 转回主线程。

        Args:
            plugin_name: 消息来源插件名。
            msg: 已解析的消息字典，按 ``type`` 分发。
        """
        if msg.get("type") == "handshake":
            self._helper_pids[plugin_name] = msg.get("pid")
            self._helper_statuses.setdefault(plugin_name, {})["running"] = True
            self._active_plugins.add(plugin_name)
            if self._log:
                self._log.info(
                    f"Helper 已连接: {plugin_name} (PID: {msg.get('pid')})")
            self.schedule_ui(self._update_menu)
        elif msg.get("type") == "status":
            self._helper_statuses[plugin_name] = {
                "running": msg.get("running", False),
                "pids": msg.get("pids", []),
            }
            self.schedule_ui(self._update_menu)
        elif msg.get("type") == "log":
            level = msg.get("level", "info")
            text = msg.get("message", "")
            if self._log:
                getattr(self._log, level, self._log.info)(
                    f"[{plugin_name}] {text}")

    def _update_menu(self):
        """刷新托盘菜单的勾选状态（托盘尚未创建时忽略）。"""
        if self.tray:
            self.tray.update_menu()

    def _check_autostart(self):
        """检查启动目录下是否已存在自启快捷方式。

        Returns:
            存在返回 ``True``；COM 调用异常时保守返回 ``False``。
        """
        import win32com.client
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            startup = shell.SpecialFolders("Startup")
            return os.path.exists(os.path.join(startup, "MyToolBox.lnk"))
        except Exception:
            return False

    def on_toggle_autostart(self):
        """在启动目录创建或删除自启快捷方式，并刷新菜单。"""
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            startup = shell.SpecialFolders("Startup")
            lnk_path = os.path.join(startup, "MyToolBox.lnk")
            if os.path.exists(lnk_path):
                os.remove(lnk_path)
                self._autostart_enabled = False
            else:
                shortcut = shell.CreateShortCut(lnk_path)
                shortcut.TargetPath = _get_exe_path()
                shortcut.WorkingDirectory = os.path.dirname(_get_exe_path())
                shortcut.Save()
                self._autostart_enabled = True
        except Exception as e:
            if self._log:
                self._log.error(f"切换开机自启失败: {e}")
        self._update_menu()

    def on_exit(self):
        """托盘「退出」菜单的入口。"""
        self.shutdown()

    @property
    def plugins(self):
        """插件名到插件实例的映射。"""
        return self.plugin_mgr.plugins

    @property
    def active_plugins(self):
        """当前在 Core 进程内运行、或已握手成功的插件名集合。"""
        return self._active_plugins

    @property
    def active_helper_plugins(self):
        """已拉起 helper 子进程的插件名集合（未必已完成握手）。"""
        return self._active_helper_plugins

    @property
    def helper_statuses(self):
        """helper 上报的运行状态，结构为 ``{插件名: {running, pids}}``。"""
        return self._helper_statuses

    @property
    def autostart_enabled(self):
        """启动目录中是否存在自启快捷方式。"""
        return self._autostart_enabled
