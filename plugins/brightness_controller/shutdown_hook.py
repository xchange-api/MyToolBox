# -*- coding: utf-8 -*-
"""
关机关屏钩子

Windows 关机时（不含注销/重启）在进程退出前对 DDC/CI 显示器发送 VCP 0xD6=4
软关机指令，让显示器进入深度关闭而不是无信号待机。

为什么区分关机与重启:
    早期尝试读事件日志 1074，但快速启动开启时重启经常不写 1074。
    内核的 Kernel-Power 109 更可靠（5=重启，6=关机断电），但它在
    WM_ENDSESSION 之后才写入，普通桌面进程来不及读取。
    因此改为读 User32 1074（在 WM_QUERYENDSESSION 时通常已写入），
    param5 为"重启"或"关闭电源"。1074 缺失时保守地不关屏。

注销:
    WM_ENDSESSION 的 lParam 带 ENDSESSION_LOGOFF 时是用户注销，直接跳过。
    SM_SHUTTINGDOWN 在部分系统上注销时也可能为非零，不能单独依赖。

组成:
    ShutdownHook - 子类化隐藏 Qt 顶层窗口的 WndProc 拦截 WM_ENDSESSION，
    在系统给出的数秒内同步完成 DDC 关屏。
"""

import ctypes
import time
import xml.etree.ElementTree as ET
from ctypes import (
    WINFUNCTYPE, c_ssize_t, c_void_p,
)
from ctypes import wintypes

from PySide6.QtCore import QTimer

from core.logger import get_logger
from plugins.brightness_controller import ddcci

WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
ENDSESSION_LOGOFF = 0x80000000
GWLP_WNDPROC = -4
GA_ROOT = 2
SM_SHUTTINGDOWN = 0x2000

_EVENT_NS = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}

_LRESULT = c_ssize_t
_WNDPROC = WINFUNCTYPE(
    _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

_user32 = ctypes.windll.user32

_CallWindowProcW = _user32.CallWindowProcW
_CallWindowProcW.argtypes = [
    c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
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


def _query_latest_1074():
    """返回最近的 User32 1074 事件 (param5, EventRecordID)。

    param5 是本地化字符串：中文为"重启"/"关闭电源"，
    英文为 "restart"/"shutdown"。查询失败时返回 (None, 0)。
    """
    try:
        import win32evtlog

        query = win32evtlog.EvtQuery(
            "System",
            win32evtlog.EvtQueryReverseDirection,
            "*[System[Provider[@Name='User32'] and (EventID=1074)]]",
        )
        events = win32evtlog.EvtNext(query, 3)
    except Exception:
        return None, 0

    for event in events:
        try:
            xml_text = win32evtlog.EvtRender(
                event, win32evtlog.EvtRenderEventXml)
            root = ET.fromstring(xml_text)
            record_node = root.find("e:System/e:EventRecordID", _EVENT_NS)
            record_id = int(record_node.text) if record_node is not None else 0
            data = {
                node.get("Name"): (node.text or "")
                for node in root.findall(".//e:Data", _EVENT_NS)
            }
            return data.get("param5") or "", record_id
        except Exception:
            continue
    return None, 0


def _is_restart_reason(text):
    """判断 1074 事件的 param5 是否表示"重启"。

    兼容中文（"重启"/"重新启动"）与英文（restart/reboot）系统的本地化文案。
    """
    lowered = text.lower()
    return (
        "重启" in text
        or "重新启动" in text
        or "restart" in lowered
        or "reboot" in lowered
    )


def _is_shutdown_reason(text):
    """判断 1074 事件的 param5 是否表示"关机"。

    注意"关机"是"关闭电源"的子串，两条都要保留以覆盖不同系统的措辞。
    """
    lowered = text.lower()
    return (
        "关闭电源" in text
        or "关机" in text
        or "shutdown" in lowered
        or "shut down" in lowered
        or "power off" in lowered
        or "poweroff" in lowered
    )


class ShutdownHook:
    """拦截 WM_ENDSESSION，在 Qt 退出进程前关闭显示器。

    Args:
        app: 提供 ``native_window`` 的应用对象，子类化作用在该隐藏窗口上。
        enabled: 是否启用；为 ``False`` 时 ``start()`` 直接返回。
    """

    def __init__(self, app, enabled=True):
        self._window = app.native_window
        self._enabled = enabled
        self._log = get_logger()
        self._started = False
        self._installed = False
        self._install_retries = 0
        self._hwnd = 0
        self._old_proc = 0
        self._proc_cb = None

        # 用 EventRecordID 判断 1074 是否是本次会话产生的新事件，
        # 避免把上一次关机/重启的记录误当成当前动作。
        _, self._last_1074_id = _query_latest_1074()
        self._pending_reason = None
        self._pending_record_id = 0

    def start(self):
        """启用并安装窗口过程钩子。"""
        if not self._enabled:
            self._log.info("关机关屏功能未启用 (power_off_on_shutdown=false)")
            return
        self._install_retries = 0
        self._started = True
        self._install_wndproc()

    def stop(self):
        """停用并还原原始窗口过程。"""
        self._started = False
        self._uninstall_wndproc()

    def is_active(self):
        """返回是否处于启用状态。"""
        return self._started

    def _install_wndproc(self):
        """把隐藏窗口的 WndProc 换成 ``self._wndproc``。

        原生句柄尚未创建时（Qt 还没实例化窗口）安排 200ms 后重试，
        最多 50 次（约 10 秒）。
        """
        if not self._started:
            return
        try:
            hwnd = int(self._window.winId())
            if not hwnd:
                self._retry_install("隐藏窗口尚未创建原生句柄")
                return

            # 隐藏 QWidget 本身就是顶层窗口；GetAncestor 通常返回它自己。
            hwnd_top = _GetAncestor(hwnd, GA_ROOT) or hwnd

            self._proc_cb = _WNDPROC(self._wndproc)
            proc_addr = ctypes.cast(self._proc_cb, c_void_p).value
            old = _SetWindowLongPtrW(hwnd_top, GWLP_WNDPROC, proc_addr)
            if not old:
                self._log.error(
                    f"子类化 Qt 窗口失败: {ctypes.GetLastError()}")
                self._proc_cb = None
                return
            self._hwnd = hwnd_top
            self._old_proc = old
            self._installed = True
            self._log.info(
                f"关机关屏钩子已安装 (hwnd=0x{hwnd_top:X})")
        except Exception as e:
            self._log.error(f"安装关机关屏钩子失败: {e}")

    def _retry_install(self, reason):
        """稍后重试安装，超过次数上限后放弃。"""
        self._install_retries += 1
        if self._install_retries < 50:
            self._log.debug(f"{reason}，稍后重试")
            QTimer.singleShot(200, self._install_wndproc)
            return
        self._log.error(f"{reason}，关机关屏功能停用")

    def _uninstall_wndproc(self):
        """把窗口过程还原成安装前保存的地址。"""
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
        """替换后的窗口过程。

        只关心会话结束相关的两条消息，其余原样透传给 Qt 的窗口过程。
        ``WM_ENDSESSION`` 的 ``wparam`` 为 0 表示会话结束被取消。
        """
        if msg == WM_QUERYENDSESSION:
            # 1074 通常在这时已经写入；先缓存，避免 WM_ENDSESSION 时
            # 事件日志服务已经停止。
            self._capture_shutdown_reason(lparam)
        elif msg == WM_ENDSESSION:
            if wparam:
                self._handle_session_end(lparam)
            else:
                self._pending_reason = None
                self._pending_record_id = 0
        return _CallWindowProcW(
            self._old_proc, hwnd, msg, wparam, lparam)

    def _capture_shutdown_reason(self, lparam):
        """在 ``WM_QUERYENDSESSION`` 阶段抓住本次关机的原因。

        Args:
            lparam: 会话结束标志，带 ``ENDSESSION_LOGOFF`` 时表示注销。

        Note:
            这里会阻塞最多 2 秒轮询事件日志，而 ``WM_QUERYENDSESSION``
            的超时窗口有限，过长会让系统提示"应用阻止关机"
            （审计报告 P2-2）。
        """
        try:
            if lparam & ENDSESSION_LOGOFF:
                self._pending_reason = "logoff"
                self._pending_record_id = 0
                return

            # 1074 由 User32 在关机发起时写入，但实测事件日志有约
            # 1 秒缓冲；在 WM_QUERYENDSESSION 阶段重试 2 秒，避免到
            # WM_ENDSESSION 时事件日志服务已经停止。
            reason = None
            record_id = 0
            for _ in range(20):
                reason, record_id = _query_latest_1074()
                if reason is not None and record_id > self._last_1074_id:
                    self._pending_reason = reason
                    self._pending_record_id = record_id
                    self._log.info(
                        f"检测到会话结束请求: {reason} (event {record_id})")
                    return
                time.sleep(0.1)

            self._log.info(
                f"WM_QUERYENDSESSION 未找到新 1074 "
                f"(latest={reason!r}, id={record_id}, "
                f"baseline={self._last_1074_id})")
        except Exception as e:
            self._log.warning(f"读取 1074 事件失败: {e}")

    def _handle_session_end(self, lparam):
        """确认是"关机"（而非注销/重启）后关闭所有显示器。

        依次排除：用户注销、非系统关机、重启、无法识别的关机类型。
        只有确认为关机时才发送 VCP 0xD6=4。
        """
        try:
            # 注销时 lParam 带 ENDSESSION_LOGOFF；部分系统上此时
            # SM_SHUTTINGDOWN 也会非零，所以必须先看这个标志。
            if lparam & ENDSESSION_LOGOFF:
                self._log.info("检测到用户注销，跳过关屏")
                return
            if not _GetSystemMetrics(SM_SHUTTINGDOWN):
                self._log.info("会话结束但非系统关机，跳过关屏")
                return

            reason = None
            record_id = 0
            if (self._pending_reason
                    and self._pending_record_id > self._last_1074_id):
                reason = self._pending_reason
                record_id = self._pending_record_id
            else:
                for _ in range(10):
                    reason, record_id = _query_latest_1074()
                    if reason is not None and record_id > self._last_1074_id:
                        break
                    time.sleep(0.1)

            if reason is None or record_id <= self._last_1074_id:
                self._log.info(
                    f"未找到本次关机动作类型 "
                    f"(latest={reason!r}, id={record_id}, "
                    f"baseline={self._last_1074_id})，跳过关屏")
                return

            self._last_1074_id = record_id

            if _is_restart_reason(reason):
                self._log.info(f"检测到重启 ({reason})，跳过关屏")
                return
            if not _is_shutdown_reason(reason):
                self._log.info(f"未识别的关机类型 ({reason})，跳过关屏")
                return

            self._log.info(f"检测到关机 ({reason})，正在关闭显示器...")
            ok, total = ddcci.set_all_power(ddcci.POWER_OFF)
            self._log.info(f"显示器软关机指令完成: {ok}/{total} 台成功")
        except Exception as e:
            self._log.error(f"关机关屏处理失败: {e}")
        finally:
            self._pending_reason = None
            self._pending_record_id = 0
