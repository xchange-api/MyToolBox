# -*- coding: utf-8 -*-
"""
关机关屏钩子

Windows 关机/重启时（不含注销）在进程退出前对 DDC/CI 显示器发送 VCP 0xD6=4
软关机指令，让显示器进入深度关闭而不是无信号待机。

为什么不再区分关机与重启:
    系统事件日志 1074 是唯一能读取动作类型（关机/重启/注销）的来源，但本机
    开启快速启动后，"关机"实为 注销 -> 休眠 -> 断电，事件日志服务常常来不及
    把 1074 落盘，实测 7 次关机只有 2 次有记录。而 WM_ENDSESSION 只能通过
    SM_SHUTTINGDOWN 区分"关机（含重启）"与"注销"，无法区分关机/重启。
    因此改为两者都关屏，由插件启动时发送 VCP 0xD6=1 自动点亮（实测深度关闭
    后重启时的视频信号唤不醒显示器，必须靠 DDC 指令或物理按键）。

组成:
    ShutdownHook - 子类化 Tk 顶层窗口 WndProc 拦截 WM_ENDSESSION，在系统
    给出的数秒内同步完成 DDC 关屏。
"""

import ctypes
import time
from ctypes import (
    WINFUNCTYPE, POINTER, byref, c_ssize_t, c_void_p,
)
from ctypes import wintypes

from core.logger import get_logger
from plugins.brightness_controller import ddcci

WM_ENDSESSION = 0x0016
GWLP_WNDPROC = -4
GA_ROOT = 2
SM_SHUTTINGDOWN = 0x2000

_LRESULT = c_ssize_t
_WNDPROC = WINFUNCTYPE(_LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.windll.user32

_CallWindowProcW = _user32.CallWindowProcW
_CallWindowProcW.argtypes = [c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_CallWindowProcW.restype = _LRESULT

_GetAncestor = _user32.GetAncestor
_GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_GetAncestor.restype = wintypes.HWND

_GetSystemMetrics = _user32.GetSystemMetrics
_GetSystemMetrics.argtypes = [wintypes.INT]
_GetSystemMetrics.restype = wintypes.BOOL

_SetWindowLongPtrW = _user32.SetWindowLongPtrW
_SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, c_ssize_t]
_SetWindowLongPtrW.restype = c_ssize_t


class ShutdownHook:
    """拦截 WM_ENDSESSION，在 Tk 退出进程前关闭显示器"""

    def __init__(self, app, tk_root, enabled=True):
        self._app = app
        self._tk_root = tk_root
        self._enabled = enabled
        self._log = get_logger()
        self._started = False
        self._installed = False
        self._install_retries = 0
        self._hwnd = 0
        self._old_proc = 0
        self._proc_cb = None

    def start(self):
        if not self._enabled:
            self._log.info("关机关屏功能未启用 (power_off_on_shutdown=false)")
            return
        self._started = True
        self._app.schedule_ui(self._install_wndproc)

    def stop(self):
        self._started = False
        if self._installed:
            self._app.schedule_ui(self._uninstall_wndproc)

    def is_active(self):
        return self._started

    def _install_wndproc(self):
        try:
            hwnd_child = self._tk_root.winfo_id()
            hwnd_top = _GetAncestor(hwnd_child, GA_ROOT)
            if not hwnd_top or hwnd_top == hwnd_child:
                # TkTopLevel 包装窗口在 mainloop 启动后才由 Tk 创建，稍后重试
                self._install_retries += 1
                if self._install_retries < 50:
                    self._tk_root.after(200, self._install_wndproc)
                    return
                self._log.error("Tk 顶层包装窗口未出现，关机关屏功能停用")
                return
            self._proc_cb = _WNDPROC(self._wndproc)
            proc_addr = ctypes.cast(self._proc_cb, c_void_p).value
            old = _SetWindowLongPtrW(hwnd_top, GWLP_WNDPROC, proc_addr)
            if not old:
                self._log.error(f"子类化 Tk 窗口失败: {ctypes.GetLastError()}")
                self._proc_cb = None
                return
            self._hwnd = hwnd_top
            self._old_proc = old
            self._installed = True
            self._log.info(f"关机关屏钩子已安装 (hwnd=0x{hwnd_top:X})")
        except Exception as e:
            self._log.error(f"安装关机关屏钩子失败: {e}")

    def _uninstall_wndproc(self):
        if not self._installed:
            return
        try:
            _SetWindowLongPtrW(self._hwnd, GWLP_WNDPROC, self._old_proc)
            self._installed = False
            self._hwnd = 0
            self._old_proc = 0
            self._proc_cb = None
            self._log.info("关机关屏钩子已卸载")
        except Exception as e:
            self._log.warning(f"卸载关机关屏钩子失败: {e}")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_ENDSESSION and wparam:
            self._handle_session_end()
        return _CallWindowProcW(self._old_proc, hwnd, msg, wparam, lparam)

    def _handle_session_end(self):
        try:
            if not _GetSystemMetrics(SM_SHUTTINGDOWN):
                self._log.info("会话结束但非系统关机（注销），跳过关屏")
                return
            self._log.info("检测到系统关机/重启，正在关闭显示器...")
            ok, total = ddcci.set_all_power(ddcci.POWER_OFF)
            self._log.info(f"显示器软关机指令完成: {ok}/{total} 台成功")
        except Exception as e:
            self._log.error(f"关机关屏处理失败: {e}")
