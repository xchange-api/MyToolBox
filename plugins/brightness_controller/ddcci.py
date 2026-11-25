# -*- coding: utf-8 -*-
"""显示器亮度控制（外接屏 DDC/CI + 笔记本内置屏 WMI）。

外接屏基于 ``dxva2.dll``（回退 ``gdi32.dll``）通过 VCP 通道读写物理
显示器的亮度与电源状态，并用注册表里的 EDID 解析出显示器真实型号；
笔记本内置屏走 ``wmi_brightness`` 模块。

主要出口：
    * ``enumerate_monitors()`` —— 枚举支持亮度控制的显示器（两种来源）；
    * ``get_brightness()`` / ``set_brightness()`` —— 读写亮度；
    * ``set_all_power()`` —— 批量下发 VCP 0xD6 电源指令（仅 DDC）；
    * ``*_async()`` —— 上述操作的线程包装，供 GUI 线程调用。

注意：``MonitorInfo`` 里持有 ``DestroyPhysicalMonitor`` 才能释放的句柄，
用完必须交给 ``cleanup()``；WMI 来源的条目没有句柄，``destroy()`` 会跳过。
"""

import ctypes
import threading
import time
import winreg
from ctypes import wintypes, Structure, POINTER, byref

from plugins.brightness_controller import wmi_brightness


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


class MONITORINFOEXW(Structure):
    r"""Win32 MONITORINFOEXW 结构体，用于读取显示器设备名（如 \\.\DISPLAY1）"""
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


class POINT(Structure):
    """Win32 POINT 结构体"""
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class DISPLAY_DEVICEW(Structure):
    """Win32 DISPLAY_DEVICEW 结构体，用于从设备名反查显示器 PnP ID"""
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class MonitorInfo:
    """封装单个显示器的句柄、名称和亮度范围。

    属性：
        hmonitor: ``HMONITOR``，用于和鼠标所在显示器做匹配。WMI 内置屏
            映射不到具体显示器时为 0。
        handle: ``GetPhysicalMonitorsFromHMONITOR`` 返回的物理显示器句柄，
            必须由 ``_DestroyPhysicalMonitor`` 释放；WMI 来源为 ``None``。
        name: DDC 返回的描述名（通常是 "Generic PnP Monitor"），或
            "内置显示器 (WMI)"。
        device: 设备名，如 ``\\\\.\\DISPLAY1``；WMI 映射失败时为空串。
        friendly: 从 EDID 解析出的真实型号，取不到时为空串。
        source: ``"ddc"`` 或 ``"wmi"``，决定读写走哪条通道。
        instance: WMI 的 ``InstanceName``，DDC 来源为空串。
    """

    __slots__ = (
        "hmonitor", "handle", "name", "device", "friendly", "source",
        "instance", "_min", "_max", "_current",
    )

    def __init__(self, hmonitor, handle, name, min_v, max_v, current,
                 device="", friendly="", source="ddc", instance=""):
        self.hmonitor = hmonitor     # HMONITOR 句柄；WMI 未映射时为 0
        self.handle = handle         # 物理显示器句柄；WMI 来源为 None
        self.name = name.strip("\x00").strip()  # DDC 返回的名称，常为 Generic PnP Monitor
        self.device = device.strip("\x00").strip()  # 设备名，如 \\.\DISPLAY1
        self.friendly = friendly.strip()  # EDID 中的真实型号，如 LG ULTRAWIDE
        self.source = source         # "ddc" | "wmi"
        self.instance = instance     # WMI InstanceName
        self._min = min_v
        self._max = max_v
        self._current = current

    @property
    def current(self):
        """最近一次读到的亮度值（枚举时即已填充）。"""
        return self._current

    @current.setter
    def current(self, value):
        self._current = value

    @property
    def range(self):
        """返回 ``(最小亮度, 最大亮度)``。"""
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


# ---- 鼠标所在显示器 ----

_MONITOR_DEFAULTTONEAREST = 2

_GetCursorPos = ctypes.windll.user32.GetCursorPos
_GetCursorPos.argtypes = [POINTER(POINT)]
_GetCursorPos.restype = wintypes.BOOL

_MonitorFromPoint = ctypes.windll.user32.MonitorFromPoint
_MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
_MonitorFromPoint.restype = wintypes.HMONITOR

_GetMonitorInfoW = ctypes.windll.user32.GetMonitorInfoW
_GetMonitorInfoW.argtypes = [wintypes.HMONITOR, POINTER(MONITORINFOEXW)]
_GetMonitorInfoW.restype = wintypes.BOOL

_EnumDisplayDevicesW = ctypes.windll.user32.EnumDisplayDevicesW
_EnumDisplayDevicesW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    POINTER(DISPLAY_DEVICEW),
    wintypes.DWORD,
]
_EnumDisplayDevicesW.restype = wintypes.BOOL


def cursor_monitor():
    """返回鼠标当前所在显示器的 HMONITOR（按最近原则），失败返回 0"""
    pt = POINT()
    if not _GetCursorPos(byref(pt)):
        return 0
    return _MonitorFromPoint(pt, _MONITOR_DEFAULTTONEAREST)


def get_device_name(hmonitor):
    r"""读取 HMONITOR 对应的设备名（如 \\.\DISPLAY1）"""
    info = MONITORINFOEXW()
    info.cbSize = ctypes.sizeof(MONITORINFOEXW)
    if _GetMonitorInfoW(hmonitor, byref(info)):
        return info.szDevice.strip("\x00").strip()
    return ""


# ---- 从 EDID 读取显示器真实型号 ----

_edid_name_cache = {}


def _parse_edid_name(edid):
    """解析 EDID 的 0xFC 描述块，返回型号名（如 LG ULTRAWIDE）"""
    for offset in range(54, 126, 18):
        block = edid[offset:offset + 18]
        if len(block) < 18:
            break
        if block[0:2] != b"\x00\x00" or block[2] != 0 or block[3] != 0xFC:
            continue
        raw = block[5:18].split(b"\x0a")[0]
        return raw.decode("ascii", "ignore").strip()
    return ""


def _pnp_id_from_device(device_name):
    r"""由 \\.\DISPLAY1 得到 MONITOR\\GSM59F1\\... 中的 GSM59F1"""
    info = DISPLAY_DEVICEW()
    info.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    if not _EnumDisplayDevicesW(device_name, 0, byref(info), 0):
        return ""
    parts = info.DeviceID.strip("\x00").strip().split("\\")
    return parts[1] if len(parts) >= 2 else ""


def _edid_friendly_name(pnp_id):
    """在注册表 Enum\\DISPLAY\\<PnP ID> 下找到 EDID 并解析型号名"""
    if not pnp_id:
        return ""
    if pnp_id in _edid_name_cache:
        return _edid_name_cache[pnp_id]

    name = ""
    try:
        root = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            f"SYSTEM\\CurrentControlSet\\Enum\\DISPLAY\\{pnp_id}",
        )
    except OSError:
        _edid_name_cache[pnp_id] = ""
        return ""

    with root:
        index = 0
        while not name:
            try:
                subkey = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(
                        root, subkey + "\\Device Parameters") as params:
                    edid, _ = winreg.QueryValueEx(params, "EDID")
            except OSError:
                continue
            name = _parse_edid_name(edid)

    _edid_name_cache[pnp_id] = name
    return name


def get_friendly_name(device_name):
    """读取显示器真实型号，失败返回空字符串"""
    try:
        return _edid_friendly_name(_pnp_id_from_device(device_name))
    except Exception:
        return ""


def _enumerate_display_devices():
    r"""返回 ``[(GDI 设备名, 显示器 PnP ID)]``。

    用于把 WMI 的 ``DISPLAY\LGD05E5\...`` 实例映射回 ``\\.\DISPLAY1``，
    进而拿到 ``HMONITOR``。
    """
    result = []
    adapter = DISPLAY_DEVICEW()
    adapter.cb = ctypes.sizeof(DISPLAY_DEVICEW)
    index = 0
    while _EnumDisplayDevicesW(None, index, byref(adapter), 0):
        device_name = adapter.DeviceName.strip("\x00").strip()
        if device_name:
            monitor = DISPLAY_DEVICEW()
            monitor.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            sub = 0
            while _EnumDisplayDevicesW(device_name, sub, byref(monitor), 0):
                parts = monitor.DeviceID.strip("\x00").strip().split("\\")
                if len(parts) >= 2 and parts[1]:
                    result.append((device_name, parts[1]))
                monitor = DISPLAY_DEVICEW()
                monitor.cb = ctypes.sizeof(DISPLAY_DEVICEW)
                sub += 1
        adapter = DISPLAY_DEVICEW()
        adapter.cb = ctypes.sizeof(DISPLAY_DEVICEW)
        index += 1
    return result


def _wmi_monitor_info(entry, hmonitor, device):
    """把一条 WMI 记录包装成 ``MonitorInfo``。"""
    pnp_id = entry.pnp_id or ""
    friendly = _edid_friendly_name(pnp_id) if pnp_id else ""
    return MonitorInfo(
        hmonitor,
        None,
        "内置显示器 (WMI)",
        0,
        100,
        entry.current,
        device,
        friendly,
        source="wmi",
        instance=entry.instance,
    )


def _merge_wmi_monitors(ddc_monitors, unsupported, hmon_by_device,
                        wmi_entries, display_devices=None):
    """把 WMI 内置屏合并进 DDC 枚举结果，返回新增的条目。

    Args:
        ddc_monitors: 已经能通过 DDC 读到亮度的显示器。
        unsupported: ``[(HMONITOR, device)]``，拿不到 DDC 亮度的显示器。
        hmon_by_device: ``{GDI 设备名: HMONITOR}``。
        wmi_entries: ``wmi_brightness.WmiMonitor`` 列表。
        display_devices: ``[(GDI 设备名, PnP ID)]``，默认自动枚举。

    Returns:
        新增的 WMI ``MonitorInfo`` 列表。同一块屏如果 DDC 已经可用，
        则保留 DDC（还能软关屏），跳过对应的 WMI 条目。
    """
    if not wmi_entries:
        return []

    if display_devices is None:
        try:
            display_devices = _enumerate_display_devices()
        except Exception:
            display_devices = []

    ddc_devices = {m.device for m in ddc_monitors if m.device}
    ddc_hmonitors = {m.hmonitor for m in ddc_monitors if m.hmonitor}
    ddc_pnp = set()
    for monitor in ddc_monitors:
        pnp = _pnp_id_from_device(monitor.device).upper()
        if pnp:
            ddc_pnp.add(pnp)

    added = []
    used_hmonitors = set()
    unmapped = []

    for entry in wmi_entries:
        pnp = (entry.pnp_id or "").upper()
        if pnp and pnp in ddc_pnp:
            continue

        hmonitor = 0
        device = ""
        for gdi_device, gdi_pnp in display_devices:
            if pnp and gdi_pnp.upper() == pnp:
                device = gdi_device
                hmonitor = hmon_by_device.get(gdi_device, 0)
                break

        if hmonitor and (hmonitor in ddc_hmonitors or device in ddc_devices):
            continue

        if hmonitor:
            used_hmonitors.add(hmonitor)
            added.append(_wmi_monitor_info(entry, hmonitor, device))
        else:
            unmapped.append(entry)

    # 兜底映射：WMI 恰好一条，且恰好剩一个显示器 DDC 读不到亮度，
    # 那基本可以确定这块就是内置屏（PnP 对不上时也能用）。
    candidates = [
        (hmonitor, device) for hmonitor, device in unsupported
        if hmonitor not in used_hmonitors
    ]
    if len(unmapped) == 1 and len(candidates) == 1:
        hmonitor, device = candidates[0]
        used_hmonitors.add(hmonitor)
        added.append(_wmi_monitor_info(unmapped[0], hmonitor, device))
        unmapped = []

    # 仍映射不到 HMONITOR 的条目也保留：浮窗里可以手动选，
    # 热键会按"唯一未映射内置屏"兜底。
    for entry in unmapped:
        added.append(_wmi_monitor_info(entry, 0, ""))

    return added


def _get_monitor_handles():
    """获取所有显示器的 HMONITOR 句柄列表"""
    _thread_local.enum_list = []
    if not _EnumDisplayMonitors(None, None, _enum_callback, 0):
        raise ctypes.WinError()
    return list(_thread_local.enum_list)


def enumerate_monitors():
    """遍历所有显示器，返回支持亮度控制的条目（DDC + WMI 内置屏）。

    Returns:
        ``MonitorInfo`` 列表。DDC 条目持有需要 ``cleanup()`` 释放的
        物理显示器句柄，WMI 条目没有句柄。枚举失败时返回空列表。
    """
    try:
        hmonitors = _get_monitor_handles()
    except Exception:
        return []

    result = []
    unsupported = []
    hmon_by_device = {}

    for hmon in hmonitors:
        device = get_device_name(hmon)
        if device:
            hmon_by_device[device] = hmon

        count = wintypes.DWORD(0)
        if (not _GetNumberOfPhysicalMonitorsFromHMONITOR(
                hmon, byref(count)) or count.value == 0):
            # 笔记本内置 eDP 屏通常走到这里：没有 DDC 物理显示器
            unsupported.append((hmon, device))
            continue

        arr = (PHYSICAL_MONITOR * count.value)()
        if not _GetPhysicalMonitorsFromHMONITOR(hmon, count.value, arr):
            unsupported.append((hmon, device))
            continue

        friendly = get_friendly_name(device)
        found = False
        for i in range(count.value):
            pm = arr[i]
            min_v = wintypes.DWORD(0)
            cur_v = wintypes.DWORD(0)
            max_v = wintypes.DWORD(0)

            if _GetMonitorBrightness(
                    pm.hPhysicalMonitor, byref(min_v), byref(cur_v),
                    byref(max_v)):
                result.append(MonitorInfo(
                    hmon,
                    pm.hPhysicalMonitor,
                    pm.szPhysicalMonitorDescription,
                    min_v.value,
                    max_v.value,
                    cur_v.value,
                    device,
                    friendly,
                ))
                found = True
            else:
                # 读不到亮度就不会再用这个句柄，必须释放，否则泄漏
                _DestroyPhysicalMonitor(pm.hPhysicalMonitor)

        if not found:
            unsupported.append((hmon, device))

    try:
        wmi_entries = wmi_brightness.enumerate_monitors()
    except Exception:
        wmi_entries = []
    if wmi_entries:
        result.extend(_merge_wmi_monitors(
            result, unsupported, hmon_by_device, wmi_entries))

    return result


def get_brightness(monitor):
    """读取指定显示器的当前亮度值"""
    if monitor.source == "wmi":
        value = wmi_brightness.get_brightness(monitor.instance)
        if value is not None:
            monitor._current = value
        return monitor._current

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
    if monitor.source == "wmi":
        if wmi_brightness.set_brightness(monitor.instance, v):
            monitor._current = v
        return
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
    """释放单个物理显示器句柄（WMI 条目没有句柄，直接跳过）"""
    if monitor.handle is None:
        return
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
    """在后台线程设置显示器亮度。

    WMI 内置屏交给常驻工作线程串行写入；DDC 仍然每次新起线程。

    Note:
        DDC 分支每次调用都新起线程且没有串行化，连续调用时多个线程
        可能并发进入 DDC 通道，导致最终亮度与最后一次请求不一致
        （审计报告 P1-10）。
    """
    if monitor.source == "wmi":
        wmi_brightness.set_brightness_async(monitor.instance, value)
        return
    threading.Thread(target=set_brightness, args=(monitor, value), daemon=True).start()
