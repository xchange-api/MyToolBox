# -*- coding: utf-8 -*-
"""通过 WMI 读写笔记本内置屏亮度。

内置屏走的是 ACPI/`root\\wmi` 命名空间，而不是 DDC/CI，所以
``ddcci.py`` 枚举不到它。本模块只负责 WMI 这一层：

    * ``enumerate_monitors()`` —— 列出支持亮度读写的内置屏；
    * ``get_brightness()`` / ``set_brightness()`` —— 读写亮度；
    * ``set_brightness_async()`` —— 不阻塞调用方的写亮度。

所有 WMI 调用都串行跑在一个常驻后台线程里：COM 必须在线程内
``CoInitialize``，串行化也能避免连续拖动滑块时写入乱序。

写亮度走 ``IWbemServices::ExecMethod`` 的标准流程——先 ``Get`` 类定义，
再用 ``Methods_()`` 取入参模板，最后交给服务对象执行：

    in_params = cls.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
    in_params.Properties_.Item("Timeout").Value = 1
    in_params.Properties_.Item("Brightness").Value = 30
    out = service.ExecMethod(row.Path_.Path, "WmiSetBrightness", in_params)

直接调用实例方法 ``row.WmiSetBrightness(...)`` 时，pywin32 动态分发会把
Python 的 ``int`` 统一按 ``VT_I4`` 传进去，部分驱动会因此拒绝或忽略写入，
表现为“读正常、写没反应”。

注意：
    * 只有实现了 ``WmiMonitorBrightnessMethods`` 的机器才有内置屏可调，
      台式机/部分 OLED/纯独显直连机型会直接查询失败，此时所有接口
      都安静地返回空/False；
    * 亮度范围固定按 0~100 处理；
    * WMI 不提供软关屏能力，内置屏跟随系统电源管理。
"""

import logging
import queue
import threading

_logger = logging.getLogger("mytoolbox")

# 队列里的特殊标记：把某个合并键的最新任务取出来执行
_DRAIN = object()

# 同一个实例的同一条错误只记一次，避免拖动滑块时刷屏
_warn_lock = threading.Lock()
_warned = {}


def _warn(instance, message):
    """记录一次写入失败；同一实例的同一条错误只输出一次。"""
    with _warn_lock:
        if _warned.get(instance) == message:
            return
        _warned[instance] = message
    _logger.warning("内置屏亮度写入失败（%s）：%s", instance, message)


def _clear_warn(instance):
    """写入成功后清掉该实例的失败记录，将来出错还能再报一次。"""
    with _warn_lock:
        _warned.pop(instance, None)


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
        self._pending = {}          # 合并键 -> 该键最新一次任务
        self._queued_keys = set()   # 已经排进队列的合并键
        self._pending_lock = threading.Lock()

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
                if fn is _DRAIN:
                    self._drain(event)
                    continue
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

    def _drain(self, key):
        """执行某个合并键的最新任务；执行期间新提交的会再跑一轮。"""
        while True:
            with self._pending_lock:
                fn = self._pending.pop(key, None)
                if fn is None:
                    self._queued_keys.discard(key)
                    return
            try:
                fn(self._service)
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

    def submit(self, fn, key=None):
        """异步执行 ``fn(service)``，忽略结果和异常。

        Args:
            fn: 在 WMI 工作线程里执行的可调用对象，参数为服务对象。
            key: 合并键。同一个键连续提交时只保留最后一次，避免拖动
                滑块时把每个中间值都写给驱动。
        """
        try:
            self._ensure()
        except Exception:
            return
        if key is None:
            self._queue.put((fn, None, []))
            return
        with self._pending_lock:
            self._pending[key] = fn
            if key in self._queued_keys:
                return
            self._queued_keys.add(key)
        self._queue.put((_DRAIN, key, None))


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


def _find_method_row(service, instance):
    """找到指定实例的方法记录，优先返回 ``Active`` 的那条。

    找不到完全匹配的实例时返回 ``None``；匹配到但都不 Active 时退回
    第一条，避免个别机型 ``Active`` 标记不可靠时完全无法调节。
    """
    fallback = None
    for row in service.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods"):
        try:
            if row.InstanceName != instance:
                continue
            if bool(row.Active):
                return row
            if fallback is None:
                fallback = row
        except Exception:
            continue
    return fallback


def _write_brightness(service, instance, value):
    """用标准 ``ExecMethod`` 写入一次亮度，返回是否成功。"""
    try:
        row = _find_method_row(service, instance)
    except Exception as exc:
        _warn(instance, f"枚举亮度方法失败: {exc}")
        return False
    if row is None:
        _warn(instance, "找不到可写的内置屏实例（显示器可能已切换）")
        return False

    try:
        cls = service.Get("WmiMonitorBrightnessMethods")
        in_params = cls.Methods_(
            "WmiSetBrightness").InParameters.SpawnInstance_()
        # Timeout 是等待硬件生效的秒数，0 在部分机型上会被驱动忽略
        in_params.Properties_.Item("Timeout").Value = 1
        in_params.Properties_.Item("Brightness").Value = value
        out = service.ExecMethod(
            row.Path_.Path, "WmiSetBrightness", in_params)
        code = int(out.Properties_.Item("ReturnValue").Value)
    except Exception as exc:
        _warn(instance, f"调用 WmiSetBrightness 失败: {exc}")
        return False

    if code != 0:
        _warn(instance,
              f"WmiSetBrightness 返回错误码 0x{code & 0xFFFFFFFF:08X}")
        return False
    _clear_warn(instance)
    return True


def _make_set_job(instance, value):
    value = max(0, min(100, int(value)))

    def _job(service):
        return _write_brightness(service, instance, value)

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
    """把写入请求排进 WMI 工作线程，调用方立即返回。

    同一块屏连续请求时只保留最新值，拖动滑块的中间值不会逐条写给驱动。
    """
    if not instance:
        return
    value = max(0, min(100, int(value)))
    _worker.submit(
        _make_set_job(instance, value), key=("brightness", instance))
