import ctypes
import os
import sys
from ctypes import wintypes

MUTEX_NAME = "MyToolBox_SingleInstance"
_mutex_handle = None

kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

ERROR_ALREADY_EXISTS = 183


def _ensure_single_instance():
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
        sys.exit(0)


def main():
    args = sys.argv[1:]

    if "--helper" in args:
        _run_helper_mode(args)
        return

    _ensure_single_instance()
    _run_core()


def _run_helper_mode(args):
    try:
        helper_idx = args.index("--helper")
        plugin_name = args[helper_idx + 1]
    except (ValueError, IndexError):
        print("用法: MyToolBox.exe --helper <plugin_name> [--core-pid <pid>]")
        sys.exit(1)

    core_pid = None
    if "--core-pid" in args:
        try:
            pid_idx = args.index("--core-pid")
            core_pid = int(args[pid_idx + 1])
        except (ValueError, IndexError):
            pass

    _run_helper(plugin_name, core_pid)


def _run_core():
    from core.app import MyToolBoxApp

    app = MyToolBoxApp()
    try:
        app.initialize()
        app.run()
    except KeyboardInterrupt:
        app.shutdown()


def _run_helper(plugin_name, core_pid):
    if core_pid is None:
        core_pid = os.getppid()

    if plugin_name == "sguard_limiter":
        from plugins.sguard_limiter.plugin_helper import run_helper
        run_helper(core_pid)
    else:
        print(f"未知的 helper 插件: {plugin_name}")
        sys.exit(1)


if __name__ == "__main__":
    main()
