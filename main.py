"""MyToolBox 入口。

同一个 exe 承担两种角色：

* 不带参数时以 Core 身份运行（托盘常驻，普通权限）；
* 带 ``--helper <plugin> --core-pid <pid>`` 时以提权子进程身份运行，
  只跑对应插件的逻辑并通过命名管道与 Core 通信。
"""

import ctypes
import os
import sys
from ctypes import wintypes

MUTEX_NAME = "MyToolBox_SingleInstance"
# 必须保持全局引用，否则句柄被回收后单实例约束就失效了
_mutex_handle = None

kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.argtypes = [wintypes.LPCVOID, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE

ERROR_ALREADY_EXISTS = 183


def _ensure_single_instance():
    """通过命名互斥体保证只有一份 Core 实例在运行。

    已有实例时直接以状态码 0 退出，用户看到的效果是"点了没反应"。
    """
    global _mutex_handle
    _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.GetLastError() == ERROR_ALREADY_EXISTS:
        sys.exit(0)


def main():
    """按命令行参数分派到 Core 模式或 helper 模式。"""
    args = sys.argv[1:]

    if "--helper" in args:
        _run_helper_mode(args)
        return

    _ensure_single_instance()
    _run_core()


def _run_helper_mode(args):
    """解析 ``--helper`` 与 ``--core-pid`` 参数并启动对应 helper。

    缺少插件名时打印用法并以状态码 1 退出。
    """
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
    """创建 QApplication、初始化应用并进入事件循环。

    初始化失败时会先执行一次 ``shutdown`` 再向上抛出，避免留下半初始化的
    资源。
    """
    from PySide6.QtWidgets import QApplication

    from core.app import MyToolBoxApp

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("MyToolBox")
    qt_app.setQuitOnLastWindowClosed(False)

    app = MyToolBoxApp(qt_app)
    try:
        app.initialize()
    except Exception:
        app.shutdown()
        raise

    try:
        exit_code = app.run()
    except KeyboardInterrupt:
        app.shutdown()
        exit_code = 0

    raise SystemExit(exit_code)


def _run_helper(plugin_name, core_pid):
    """把请求转给具体插件的 helper 实现。

    Args:
        plugin_name: 需要以管理员权限运行的插件名。
        core_pid: Core 进程 PID；为空时退回父进程 PID（仅在直接双击 exe
            时合理，正常路径由 Core 显式传入）。
    """
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
