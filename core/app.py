import os
import sys
import threading

from core.logger import setup_logger, get_logger
from core.config_manager import ConfigManager
from core.plugin_manager import PluginManager
from core.tray_manager import TrayManager
from core.ipc_server import IpcServer


def _is_frozen():
    return getattr(sys, "frozen", False)


def _get_exe_path():
    return sys.executable


class MyToolBoxApp:
    def __init__(self):
        self.config = ConfigManager()
        self.plugin_mgr = PluginManager(self.config)
        self.ipc = IpcServer()
        self.tray = None

        self._active_plugins = set()
        self._active_helper_plugins = set()
        self._helper_statuses = {}
        self._helper_pids = {}
        self._autostart_enabled = False

        self._exit_event = threading.Event()
        self._log = None

    def initialize(self):
        self.plugin_mgr.discover()
        self.config.load(self.plugin_mgr.plugins)
        log_level = self.config.get_general().get("log_level", "info")
        setup_logger(log_level)
        self._log = get_logger()

        self._autostart_enabled = self._check_autostart()
        self.plugin_mgr.load_all()

        self.ipc.set_message_callback(self._on_ipc_message)

        for name, plugin in self.plugin_mgr.plugins.items():
            cfg = self.config.get_plugin_config(name)
            if cfg.get("enabled"):
                self._start_plugin(name)

        self.tray = TrayManager(self)

    def run(self):
        self.tray.start()

    def shutdown(self):
        self._log.info("正在关闭 MyToolBox...")
        self.ipc.stop_all()
        self.plugin_mgr.stop_all()
        if self.tray:
            self.tray.stop()

    def toggle_plugin(self, name):
        if name in self._active_plugins or name in self._active_helper_plugins:
            self._stop_plugin(name)
        else:
            self._start_plugin(name)

    def _start_plugin(self, name):
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
        self.ipc.listen(name)
        exe = _get_exe_path()
        if _is_frozen():
            args = f'--helper {name} --core-pid {os.getpid()}'
        else:
            script = os.path.abspath(sys.argv[0])
            args = f'"{script}" --helper {name} --core-pid {os.getpid()}'
        import ctypes
        ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 0)
        self._active_helper_plugins.add(name)
        self._helper_statuses[name] = {"running": False, "pids": []}

    def _stop_helper_plugin(self, name):
        self.ipc.send(name, {"action": "stop"})
        self._active_helper_plugins.discard(name)
        self._helper_statuses.pop(name, None)

    def _on_ipc_message(self, plugin_name, msg):
        if msg.get("type") == "handshake":
            self._helper_pids[plugin_name] = msg.get("pid")
            self._helper_statuses.setdefault(plugin_name, {})["running"] = True
            self._active_plugins.add(plugin_name)
            self._log.info(f"Helper 已连接: {plugin_name} (PID: {msg.get('pid')})")
            self._update_menu()
        elif msg.get("type") == "status":
            self._helper_statuses[plugin_name] = {
                "running": msg.get("running", False),
                "pids": msg.get("pids", []),
            }
            self._update_menu()
        elif msg.get("type") == "log":
            level = msg.get("level", "info")
            text = msg.get("message", "")
            getattr(self._log, level, self._log.info)(f"[{plugin_name}] {text}")

    def _update_menu(self):
        if self.tray:
            self.tray.update_menu()

    def _check_autostart(self):
        import win32com.client
        try:
            shell = win32com.client.Dispatch("WScript.Shell")
            startup = shell.SpecialFolders("Startup")
            return os.path.exists(os.path.join(startup, "MyToolBox.lnk"))
        except Exception:
            return False

    def on_toggle_autostart(self):
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
        self._update_menu()

    def on_exit(self):
        self.shutdown()

    @property
    def plugins(self):
        return self.plugin_mgr.plugins

    @property
    def active_plugins(self):
        return self._active_plugins

    @property
    def active_helper_plugins(self):
        return self._active_helper_plugins

    @property
    def helper_statuses(self):
        return self._helper_statuses

    @property
    def autostart_enabled(self):
        return self._autostart_enabled
