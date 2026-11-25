# -*- coding: utf-8 -*-
"""笔记本内置屏（WMI）亮度支持的单元测试。

不需要真实笔记本：WMI 服务用假对象替代，显示器映射用构造数据验证。
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from plugins.brightness_controller import ddcci, flyout, wmi_brightness
from plugins.brightness_controller.ddcci import MonitorInfo

INSTANCE = r"DISPLAY\LGD05E5\4&2a2d3c4d&0&UID4352_0"


class FakeBrightnessRow:
    """假的 WmiMonitorBrightness 记录。"""

    def __init__(self, instance, current):
        self.InstanceName = instance
        self.CurrentBrightness = current


class FakeMethodRow:
    """假的 WmiMonitorBrightnessMethods 记录。"""

    def __init__(self, instance, sink):
        self.InstanceName = instance
        self._sink = sink

    def WmiSetBrightness(self, timeout, value):
        """记录写入参数，返回 0 表示成功。"""
        self._sink.append((self.InstanceName, timeout, value))
        return 0


class FakeService:
    """假的 root\\wmi 服务对象。"""

    def __init__(self, instances, sink):
        self.instances = instances
        self.sink = sink
        self.current = {name: 50 for name in instances}

    def ExecQuery(self, query):
        """按查询字符串返回对应的假记录。"""
        if "WmiMonitorBrightnessMethods" in query:
            return [FakeMethodRow(name, self.sink) for name in self.instances]
        if "WmiMonitorBrightness" in query:
            return [FakeBrightnessRow(name, self.current[name])
                    for name in self.instances]
        return []


class WmiWorkerTest(unittest.TestCase):
    """WMI 工作线程的枚举、读写与串行化。"""

    def setUp(self):
        """把模块级 worker 换成连接假服务的实例。"""
        self.sink = []
        self.service = FakeService([INSTANCE], self.sink)
        self._original = wmi_brightness._worker
        wmi_brightness._worker = wmi_brightness._WmiWorker(
            service_factory=lambda: self.service)

    def tearDown(self):
        """恢复真实 worker。"""
        wmi_brightness._worker = self._original

    def test_enumerate_parses_pnp_id(self):
        """枚举结果应带 InstanceName 和解析出的 PnP ID。"""
        entries = wmi_brightness.enumerate_monitors()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].instance, INSTANCE)
        self.assertEqual(entries[0].pnp_id, "LGD05E5")
        self.assertEqual(entries[0].current, 50)

    def test_get_brightness(self):
        """应读到假服务里的当前亮度。"""
        self.assertEqual(wmi_brightness.get_brightness(INSTANCE), 50)
        self.assertIsNone(wmi_brightness.get_brightness("DISPLAY\\X\\0"))

    def test_set_brightness_clamps_value(self):
        """写入值应被夹在 0~100，并使用 Timeout=0。"""
        self.assertTrue(wmi_brightness.set_brightness(INSTANCE, 250))
        self.assertEqual(self.sink[-1], (INSTANCE, 0, 100))
        self.assertTrue(wmi_brightness.set_brightness(INSTANCE, -5))
        self.assertEqual(self.sink[-1], (INSTANCE, 0, 0))

    def test_async_writes_keep_order(self):
        """连续异步写入应按入队顺序落地。"""
        for value in (10, 20, 30, 40):
            wmi_brightness.set_brightness_async(INSTANCE, value)

        deadline = time.time() + 3
        while len(self.sink) < 4 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual([item[2] for item in self.sink], [10, 20, 30, 40])

    def test_unknown_instance_is_not_written(self):
        """找不到实例时写入应失败且不产生记录。"""
        self.assertFalse(wmi_brightness.set_brightness("DISPLAY\\X\\0", 50))
        self.assertEqual(self.sink, [])


class MergeTest(unittest.TestCase):
    """WMI 条目合并进 DDC 枚举结果的规则。"""

    def setUp(self):
        """把 PnP 查询和 EDID 型号读取换成固定值。"""
        self._pnp = ddcci._pnp_id_from_device
        self._edid = ddcci._edid_friendly_name
        ddcci._pnp_id_from_device = (
            lambda device: "LGD05E5" if device.endswith("DISPLAY1") else "")
        ddcci._edid_friendly_name = (
            lambda pnp: "LP140QH2" if pnp else "")

    def tearDown(self):
        """恢复被替换的函数。"""
        ddcci._pnp_id_from_device = self._pnp
        ddcci._edid_friendly_name = self._edid

    @staticmethod
    def _entry(pnp="LGD05E5", current=45):
        """造一条 WMI 枚举记录。"""
        return wmi_brightness.WmiMonitor(
            rf"DISPLAY\{pnp}\4&2a2d3c4d&0&UID4352_0", pnp, current)

    @staticmethod
    def _ddc(device, hmonitor=111):
        """造一条 DDC 显示器记录。"""
        return MonitorInfo(
            hmonitor, 1234, "Generic PnP Monitor", 0, 100, 50,
            device, "")

    def test_pnp_maps_to_hmonitor(self):
        """PnP ID 能对上 GDI 设备时应拿到 HMONITOR 和设备名。"""
        added = ddcci._merge_wmi_monitors(
            [],
            [(111, r"\\.\DISPLAY1")],
            {r"\\.\DISPLAY1": 111},
            [self._entry()],
            [(r"\\.\DISPLAY1", "LGD05E5")],
        )
        self.assertEqual(len(added), 1)
        monitor = added[0]
        self.assertEqual(monitor.hmonitor, 111)
        self.assertEqual(monitor.device, r"\\.\DISPLAY1")
        self.assertEqual(monitor.source, "wmi")
        self.assertIsNone(monitor.handle)
        self.assertEqual(monitor.friendly, "LP140QH2")
        self.assertEqual(monitor.current, 45)
        self.assertEqual(monitor.range, (0, 100))

    def test_ddc_available_wins(self):
        """DDC 已能控制的屏幕不应重复添加 WMI 条目。"""
        added = ddcci._merge_wmi_monitors(
            [self._ddc(r"\\.\DISPLAY1")],
            [],
            {r"\\.\DISPLAY1": 111},
            [self._entry()],
            [(r"\\.\DISPLAY1", "LGD05E5")],
        )
        self.assertEqual(added, [])

    def test_fallback_pairs_single_unsupported_monitor(self):
        """PnP 对不上时，唯一未映射 WMI 条目与唯一 DDC 失败屏 1:1 配对。"""
        added = ddcci._merge_wmi_monitors(
            [],
            [(111, r"\\.\DISPLAY1")],
            {r"\\.\DISPLAY1": 111},
            [self._entry("AUO1234")],
            [],
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].hmonitor, 111)
        self.assertEqual(added[0].device, r"\\.\DISPLAY1")

    def test_fallback_skips_ambiguous_candidates(self):
        """有多个候选屏幕时不猜，WMI 条目保留为未映射。"""
        added = ddcci._merge_wmi_monitors(
            [],
            [(111, r"\\.\DISPLAY1"), (222, r"\\.\DISPLAY2")],
            {r"\\.\DISPLAY1": 111, r"\\.\DISPLAY2": 222},
            [self._entry("AUO1234")],
            [],
        )
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].hmonitor, 0)

    def test_unmapped_entry_is_kept(self):
        """完全映射不到时也保留条目，只是没有 HMONITOR。"""
        added = ddcci._merge_wmi_monitors(
            [], [], {}, [self._entry("AUO1234")], [])
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].hmonitor, 0)
        self.assertEqual(added[0].device, "")


class DispatchTest(unittest.TestCase):
    """MonitorInfo 按来源分派读写与释放。"""

    def setUp(self):
        """构造一条 WMI 内置屏条目并备份真实函数。"""
        self.monitor = MonitorInfo(
            0, None, "内置显示器 (WMI)", 0, 100, 50,
            source="wmi", instance=INSTANCE,
        )
        self._get = wmi_brightness.get_brightness
        self._set = wmi_brightness.set_brightness
        self._async = wmi_brightness.set_brightness_async

    def tearDown(self):
        """恢复真实函数。"""
        wmi_brightness.get_brightness = self._get
        wmi_brightness.set_brightness = self._set
        wmi_brightness.set_brightness_async = self._async

    def test_get_reads_wmi(self):
        """读取应走 WMI 并更新缓存值。"""
        wmi_brightness.get_brightness = lambda instance: 66
        self.assertEqual(ddcci.get_brightness(self.monitor), 66)
        self.assertEqual(self.monitor.current, 66)

    def test_set_writes_wmi(self):
        """写入应走 WMI 并更新缓存值。"""
        calls = []
        wmi_brightness.set_brightness = (
            lambda instance, value: calls.append((instance, value)) or True)
        ddcci.set_brightness(self.monitor, 77)
        self.assertEqual(calls, [(INSTANCE, 77)])
        self.assertEqual(self.monitor.current, 77)

    def test_async_uses_wmi_worker(self):
        """异步写入应交给 WMI 模块，而不是起 DDC 线程。"""
        calls = []
        wmi_brightness.set_brightness_async = (
            lambda instance, value: calls.append((instance, value)))
        ddcci.set_brightness_async(self.monitor, 55)
        self.assertEqual(calls, [(INSTANCE, 55)])

    def test_destroy_skips_missing_handle(self):
        """WMI 条目没有句柄，释放时应直接跳过。"""
        ddcci.destroy(self.monitor)
        ddcci.cleanup([self.monitor])


class TargetTest(unittest.TestCase):
    """热键目标选择与未映射内置屏兜底。"""

    def setUp(self):
        """构造一台外接屏和一台 WMI 内置屏。"""
        self.external = MonitorInfo(
            111, 1234, "Generic PnP Monitor", 0, 100, 50,
            r"\\.\DISPLAY1", "LG ULTRAWIDE")
        self.internal = MonitorInfo(
            0, None, "内置显示器 (WMI)", 0, 100, 45, "",
            source="wmi", instance=INSTANCE)

    def test_hmonitor_match(self):
        """正常映射的内置屏按 HMONITOR 命中。"""
        mapped = MonitorInfo(
            112, None, "内置显示器 (WMI)", 0, 100, 45, "",
            source="wmi", instance=INSTANCE)
        self.assertEqual(
            flyout._targets_for_monitor([self.external, mapped], 112),
            [mapped])

    def test_single_unmapped_fallback(self):
        """唯一未映射内置屏在光标不匹配任何屏时兜底。"""
        self.assertEqual(
            flyout._targets_for_monitor([self.external, self.internal], 222),
            [self.internal])

    def test_unmapped_does_not_steal_external(self):
        """光标在外接屏上时不应命中未映射内置屏。"""
        self.assertEqual(
            flyout._targets_for_monitor([self.external, self.internal], 111),
            [self.external])

    def test_multiple_unmapped_no_guess(self):
        """多条未映射内置屏时不猜目标。"""
        second = MonitorInfo(
            0, None, "内置显示器 (WMI)", 0, 100, 60, "",
            source="wmi", instance="DISPLAY\\XXX\\0")
        self.assertEqual(
            flyout._targets_for_monitor([self.internal, second], 222), [])


if __name__ == "__main__":
    unittest.main()
