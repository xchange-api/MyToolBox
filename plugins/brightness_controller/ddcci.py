# -*- coding: utf-8 -*-
"""
DDC/CI 显示器亮度控制模块
基于 Windows dxva2.dll / gdi32.dll 实现物理显示器亮度读写
"""

import ctypes
import threading
import time
from ctypes import wintypes, Structure, POINTER, byref


PHYSICAL_MONITOR_DESCRIPTION_SIZE = 128

VCP_POWER_MODE = 0xD6
POWER_ON = 1
POWER_OFF = 4


class PHYSICAL_MONITOR(Structure):
    """Win32 PHYSICAL_MONITOR 结构体"""
    _fields_ = [
        ("hPhysicalMonitor", wintypes.HANDLE),                                   # 物理显示器句柄
        ("szPhysicalMonitorDescription", wintypes.WCHAR * PHYSICAL_MONITOR_DESCRIPTION_SIZE),  # 显示器名称
    ]


class MonitorInfo:
    """封装单个显示器的句柄、名称和亮度范围"""
    __slots__ = ("hmonitor", "handle", "name", "_min", "_max", "_current")

    def __init__(self, hmonitor, handle, name, min_v, max_v, current):
        self.hmonitor = hmonitor     # HMONITOR 句柄
        self.handle = handle         # 物理显示器句柄
        self.name = name.strip("\x00").strip()  # 显示器名称
        self._min = min_v
        self._max = max_v
        self._current = current

    @property
    def current(self):
        return self._current

    @current.setter
    def current(self, value):
        self._current = value

    @property
    def range(self):
        return self._min, self._max


def _load_dll():
    """加载 DDC/CI 通信 DLL，优先 dxva2，回退 gdi32"""
    try:
        return ctypes.windll.dxva2
    except Exception:
        return ctypes.windll.gdi32


_dll = _load_dll()

# ---- Win32 API 函数绑定 ----

_GetNumberOfPhysicalMonitorsFromHMONITOR = _dll.GetNumberOfPhysicalMonitorsFromHMONITOR
_GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, POINTER(wintypes.DWORD)]
_GetNumberOfPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL

_GetPhysicalMonitorsFromHMONITOR = _dll.GetPhysicalMonitorsFromHMONITOR
_GetPhysicalMonitorsFromHMONITOR.argtypes = [
    wintypes.HMONITOR, wintypes.DWORD, POINTER(PHYSICAL_MONITOR)
]
_GetPhysicalMonitorsFromHMONITOR.restype = wintypes.BOOL

_DestroyPhysicalMonitor = _dll.DestroyPhysicalMonitor
_DestroyPhysicalMonitor.argtypes = [wintypes.HANDLE]
_DestroyPhysicalMonitor.restype = wintypes.BOOL

_GetMonitorBrightness = _dll.GetMonitorBrightness
_GetMonitorBrightness.argtypes = [
    wintypes.HANDLE,
    POINTER(wintypes.DWORD),
    POINTER(wintypes.DWORD),
    POINTER(wintypes.DWORD),
]
_GetMonitorBrightness.restype = wintypes.BOOL

_SetMonitorBrightness = _dll.SetMonitorBrightness
_SetMonitorBrightness.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_SetMonitorBrightness.restype = wintypes.BOOL

_SetVCPFeature = _dll.SetVCPFeature
_SetVCPFeature.argtypes = [wintypes.HANDLE, wintypes.BYTE, wintypes.DWORD]
_SetVCPFeature.restype = wintypes.BOOL

# ---- 显示器枚举回调 ----

_MonitorEnumProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HMONITOR,
    wintypes.HDC,
    POINTER(wintypes.RECT),
    wintypes.LPARAM,
)

_enum_list = []


def _enum_proc(hmonitor, hdc, rect, lparam):
    """EnumDisplayMonitors 回调：收集所有显示器 HMONITOR"""
    lst = getattr(_thread_local, 'enum_list', None)
    if lst is not None:
        lst.append(hmonitor)
    return True


_EnumDisplayMonitors = ctypes.windll.user32.EnumDisplayMonitors
_EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    POINTER(wintypes.RECT),
    _MonitorEnumProc,
    wintypes.LPARAM,
]
_EnumDisplayMonitors.restype = wintypes.BOOL

_enum_callback = _MonitorEnumProc(_enum_proc)

_thread_local = threading.local()


def _get_monitor_handles():
    """获取所有显示器的 HMONITOR 句柄列表"""
    _thread_local.enum_list = []
    if not _EnumDisplayMonitors(None, None, _enum_callback, 0):
        raise ctypes.WinError()
    return list(_thread_local.enum_list)


def enumerate_monitors():
    """遍历所有显示器，返回支持 DDC/CI 的 MonitorInfo 列表"""
    try:
        hmonitors = _get_monitor_handles()
    except Exception:
        return []

    result = []
    for hmon in hmonitors:
        count = wintypes.DWORD(0)
        if not _GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, byref(count)):
            continue
        if count.value == 0:
            continue

        arr = (PHYSICAL_MONITOR * count.value)()
        if not _GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
            continue

        for i in range(count.value):
            pm = arr[i]
            min_v = wintypes.DWORD(0)
            cur_v = wintypes.DWORD(0)
            max_v = wintypes.DWORD(0)

            if _GetMonitorBrightness(pm.hPhysicalMonitor, byref(min_v), byref(cur_v), byref(max_v)):
                result.append(MonitorInfo(
                    hmon,
                    pm.hPhysicalMonitor,
                    pm.szPhysicalMonitorDescription,
                    min_v.value,
                    max_v.value,
                    cur_v.value,
                ))
    return result


def get_brightness(monitor):
    """读取指定显示器的当前亮度值"""
    min_v = wintypes.DWORD(0)
    cur_v = wintypes.DWORD(0)
    max_v = wintypes.DWORD(0)
    if _GetMonitorBrightness(monitor.handle, byref(min_v), byref(cur_v), byref(max_v)):
        monitor._current = cur_v.value
        return cur_v.value
    return monitor._current


def set_brightness(monitor, value):
    """设置指定显示器的亮度值（自动限制在有效范围内）"""
    v = max(monitor._min, min(monitor._max, value))
    if _SetMonitorBrightness(monitor.handle, v):
        monitor._current = v


def set_power(handle, mode, retries=3):
    """设置显示器电源模式（VCP 0xD6: 1=开 4=软关），I2C 偶发抖动自动重试"""
    for _ in range(retries):
        if _SetVCPFeature(handle, VCP_POWER_MODE, mode):
            return True
        time.sleep(0.2)
    return False


def enumerate_physical_handles():
    """枚举所有物理显示器句柄，返回 [(handle, name)]，调用方负责 DestroyPhysicalMonitor"""
    try:
        hmonitors = _get_monitor_handles()
    except Exception:
        return []
    result = []
    for hmon in hmonitors:
        count = wintypes.DWORD(0)
        if not _GetNumberOfPhysicalMonitorsFromHMONITOR(hmon, byref(count)) or count.value == 0:
            continue
        arr = (PHYSICAL_MONITOR * count.value)()
        if not _GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
            continue
        for i in range(count.value):
            result.append((
                arr[i].hPhysicalMonitor,
                arr[i].szPhysicalMonitorDescription.strip("\x00").strip(),
            ))
    return result


def set_all_power(mode, retries=3):
    """对所有 DDC 显示器发送电源模式指令，返回 (成功数, 总数)"""
    monitors = enumerate_physical_handles()
    success = 0
    for handle, _name in monitors:
        if set_power(handle, mode, retries):
            success += 1
    for handle, _name in monitors:
        _DestroyPhysicalMonitor(handle)
    return success, len(monitors)


def destroy(monitor):
    """释放单个物理显示器句柄"""
    try:
        _DestroyPhysicalMonitor(monitor.handle)
    except Exception:
        pass


def cleanup(monitors):
    """释放所有显示器句柄并清空列表"""
    for m in monitors:
        destroy(m)
    monitors.clear()


def enumerate_monitors_async(callback):
    """在后台线程枚举显示器，完成后在主线程执行 callback(monitors)"""
    def _worker():
        result = enumerate_monitors()
        callback(result)
    threading.Thread(target=_worker, daemon=True).start()


def set_brightness_async(monitor, value):
    """在后台线程设置显示器亮度"""
    threading.Thread(target=set_brightness, args=(monitor, value), daemon=True).start()
