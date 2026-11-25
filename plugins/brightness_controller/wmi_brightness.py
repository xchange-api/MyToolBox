# -*- coding: utf-8 -*-
"""通过 WMI 读写笔记本内置屏亮度。

内置屏走的是 ACPI/`root\\wmi` 命名空间，而不是 DDC/CI，所以
``ddcci.py`` 枚举不到它。本模块只负责 WMI 这一层：

    * ``enumerate_monitors()`` —— 列出支持亮度读写的内置屏；
    * ``get_brightness()`` / ``set_brightness()`` —— 读写亮度；
    * ``set_brightness_async()`` —— 不阻塞调用方的写亮度。

所有 WMI 调用都串行跑在一个常驻后台线程里：COM 必须在线程内
``CoInitialize``，串行化也能避免连续拖动滑块时写入乱序。

注意：
    * 只有实现了 ``WmiMonitorBrightnessMethods`` 的机器才有内置屏可调，
      台式机/部分 OLED/纯独显直连机型会直接查询失败，此时所有接口
      都安静地返回空/False；
    * 亮度范围固定按 0~100 处理；
    * WMI 不提供软关屏能力，内置屏跟随系统电源管理。
"""

import queue
import threading


class WmiMonitor:
    """一条 WMI 亮度记录。

    属性：
        instance: WMI 的 ``InstanceName``，后续读写都用它定位；
        pnp_id: 从 ``InstanceName`` 拆出的 PnP 型号码（如 ``LGD05E5``）；
        current: 枚举时读到的亮度（0~100）。
    """

    __slots__ = ("instance", "pnp_id", "current")

    def __init__(self, instance, pnp_id, current):
        self.instance = instance
        self.pnp_id = pnp_id
        self.current = int(current)


def _pnp_id(instance):
    r"""从 ``DISPLAY\LGD05E5\4&...`` 这样的实例名里取 PnP 型号码。"""
    parts = (instance or "").split("\\")
    if len(parts) >= 2 and parts[0].upper() == "DISPLAY":
        return parts[1]
    return ""


def _create_service():
    """在工作线程内创建 WMI 服务对象。"""
    import win32com.client

    return win32com.client.GetObject("winmgmts:root/wmi")


class _WmiWorker:
    """常驻的 WMI 工作线程，所有调用按入队顺序串行执行。

    ``service_factory`` 只给测试用，正常情况无需传入。
    """

    def __init__(self, service_factory=None):
        self._service_factory = service_factory or _create_service
        self._queue = queue.Queue()
        self._start_lock = threading.Lock()
        self._ready = threading.Event()
        self._thread = None
        self._service = None
        self._init_error = None

    def _run(self):
        try:
            import pythoncom
        except Exception as exc:  # pragma: no cover - 环境缺 pywin32
            self._init_error = exc
            self._ready.set()
            return

        try:
            pythoncom.CoInitialize()
            self._service = self._service_factory()
        except Exception as exc:
            self._init_error = exc
            self._ready.set()
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
            return

        self._ready.set()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                fn, event, box = item
                try:
                    box.append((True, fn(self._service)))
                except Exception as exc:
                    box.append((False, exc))
                if event is not None:
                    event.set()
        finally:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def _ensure(self, timeout=3.0):
        with self._start_lock:
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="wmi-brightness", daemon=True)
                self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError("WMI 初始化超时")
        if self._init_error is not None:
            raise self._init_error
        if self._service is None:
            raise RuntimeError("WMI 服务不可用")

    def call(self, fn, timeout=3.0):
        """同步执行 ``fn(service)``，返回其结果；失败抛异常。"""
        self._ensure()
        event = threading.Event()
        box = []
        self._queue.put((fn, event, box))
        if not event.wait(timeout):
            raise TimeoutError("WMI 调用超时")
        ok, value = box[0]
        if not ok:
            raise value
        return value

    def submit(self, fn):
        """异步执行 ``fn(service)``，忽略结果和异常。"""
        try:
            self._ensure()
        except Exception:
            return
        self._queue.put((fn, None, []))


_worker = _WmiWorker()


def _query_method_instances(service):
    """返回可写的内置屏实例名集合；类不存在时返回空集合。"""
    try:
        rows = service.ExecQuery(
            "SELECT InstanceName FROM WmiMonitorBrightnessMethods")
        return {row.InstanceName for row in rows}
    except Exception:
        return set()


def _query_brightness_rows(service):
    """返回 WmiMonitorBrightness 记录；类不存在时返回空列表。"""
    try:
        return list(service.ExecQuery(
            "SELECT InstanceName, CurrentBrightness FROM WmiMonitorBrightness"))
    except Exception:
        return []


def _enumerate_job(service):
    method_names = _query_method_instances(service)
    if not method_names:
        return []

    result = []
    for row in _query_brightness_rows(service):
        try:
            name = row.InstanceName
            if name not in method_names:
                continue
            result.append(
                WmiMonitor(name, _pnp_id(name), int(row.CurrentBrightness)))
        except Exception:
            continue
    return result


def enumerate_monitors(timeout=3.0):
    """枚举支持 WMI 亮度控制的内置屏，失败返回空列表。"""
    try:
        return _worker.call(_enumerate_job, timeout=timeout)
    except Exception:
        return []


def _make_get_job(instance):
    def _job(service):
        for row in _query_brightness_rows(service):
            if row.InstanceName == instance:
                return int(row.CurrentBrightness)
        return None

    return _job


def get_brightness(instance, timeout=2.0):
    """读取指定内置屏的亮度；失败返回 ``None``。"""
    if not instance:
        return None
    try:
        return _worker.call(_make_get_job(instance), timeout=timeout)
    except Exception:
        return None


def _make_set_job(instance, value):
    value = max(0, min(100, int(value)))

    def _job(service):
        try:
            rows = service.ExecQuery(
                "SELECT * FROM WmiMonitorBrightnessMethods")
        except Exception:
            return False
        for row in rows:
            if row.InstanceName == instance:
                # Timeout=0 表示立即生效，拖动滑块时没有额外延迟
                code = row.WmiSetBrightness(0, value)
                return code == 0
        return False

    return _job

    return _job


def set_brightness(instance, value, timeout=3.0):
    """同步写入内置屏亮度，成功返回 ``True``。"""
    if not instance:
        return False
    try:
        return bool(_worker.call(
            _make_set_job(instance, value), timeout=timeout))
    except Exception:
        return False


def set_brightness_async(instance, value):
    """把写入请求排进 WMI 工作线程，调用方立即返回。"""
    if not instance:
        return
    _worker.submit(_make_set_job(instance, value))
