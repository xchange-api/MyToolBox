"""亮度控制相关的纯逻辑测试。

覆盖 EDID 型号解析、显示器标题生成，以及 ctypes 结构体的内存布局
（布局写错会导致 Win32 调用崩溃，值得用测试钉住）。
"""

import ctypes
import unittest

from plugins.brightness_controller import ddcci
from plugins.brightness_controller import flyout
from plugins.sguard_limiter import limiter


def _make_edid(name, tag=0xFC):
    """构造一个只含一个描述块的 128 字节 EDID。

    Args:
        name: 写进描述块的型号名，超过 13 字节会被截断。
        tag: 描述块类型，0xFC 表示显示器名称。
    """
    edid = bytearray(128)
    block = bytearray(18)
    block[0] = 0
    block[1] = 0
    block[2] = 0
    block[3] = tag
    block[4] = 0

    encoded = name.encode("ascii")[:13]
    block[5:5 + len(encoded)] = encoded
    for i in range(5 + len(encoded), 18):
        block[i] = 0x20

    edid[54:72] = block
    return bytes(edid)


def _monitor(friendly="", device=""):
    """造一个只关心标题字段的 MonitorInfo。"""
    return ddcci.MonitorInfo(1, 1, "Generic PnP Monitor", 0, 100, 50,
                             device=device, friendly=friendly)


class _FlyoutStub:
    """只带标题生成所需成员的轻量替身，避免创建 QWidget。"""

    _monitor_label = flyout.BrightnessFlyout._monitor_label

    def __init__(self, monitors):
        self._monitors = monitors


class EdidParsingTest(unittest.TestCase):
    """ddcci._parse_edid_name 的解析结果。"""

    def test_reads_friendly_name(self):
        self.assertEqual(
            ddcci._parse_edid_name(_make_edid("LG ULTRAWIDE")),
            "LG ULTRAWIDE",
        )

    def test_stops_at_newline_terminator(self):
        """EDID 规范用 0x0A 结束名称，其后内容应被丢弃。"""
        self.assertEqual(
            ddcci._parse_edid_name(_make_edid("DELL U2419H\x0a")),
            "DELL U2419H",
        )

    def test_ignores_non_name_descriptor(self):
        """0xFD（频率范围）块不应被当成型号名。"""
        self.assertEqual(ddcci._parse_edid_name(_make_edid("RANGE", tag=0xFD)), "")

    def test_short_data_returns_empty(self):
        """数据不足一个描述块时返回空串而不是抛异常。"""
        self.assertEqual(ddcci._parse_edid_name(b"\x00" * 60), "")

    def test_empty_input_returns_empty(self):
        self.assertEqual(ddcci._parse_edid_name(b""), "")


class MonitorTitleTest(unittest.TestCase):
    """BrightnessFlyout 的显示器标题生成规则。"""

    def test_prefers_edid_friendly_name(self):
        stub = _FlyoutStub([_monitor(friendly="LG ULTRAWIDE",
                                     device=r"\\.\DISPLAY1")])
        self.assertEqual(
            flyout.BrightnessFlyout._monitor_titles(stub), ["LG ULTRAWIDE"])

    def test_falls_back_to_device_index(self):
        stub = _FlyoutStub([_monitor(device=r"\\.\DISPLAY2")])
        self.assertEqual(
            flyout.BrightnessFlyout._monitor_titles(stub), ["显示器 2"])

    def test_falls_back_to_positional_index(self):
        stub = _FlyoutStub([_monitor(), _monitor()])
        self.assertEqual(
            flyout.BrightnessFlyout._monitor_titles(stub),
            ["显示器 1", "显示器 2"],
        )

    def test_duplicate_names_get_suffix(self):
        """同型号显示器必须能区分，否则列表里看不出选的是哪台。"""
        stub = _FlyoutStub([
            _monitor(friendly="DELL U2419H"),
            _monitor(friendly="DELL U2419H"),
        ])
        self.assertEqual(
            flyout.BrightnessFlyout._monitor_titles(stub),
            ["DELL U2419H (1)", "DELL U2419H (2)"],
        )

    def test_mixed_names_only_number_duplicates(self):
        stub = _FlyoutStub([
            _monitor(friendly="LG ULTRAWIDE"),
            _monitor(friendly="DELL U2419H"),
            _monitor(friendly="DELL U2419H"),
        ])
        self.assertEqual(
            flyout.BrightnessFlyout._monitor_titles(stub),
            ["LG ULTRAWIDE", "DELL U2419H (1)", "DELL U2419H (2)"],
        )


class StructLayoutTest(unittest.TestCase):
    """ctypes 结构体布局。

    这些尺寸来自 Windows SDK 定义；一旦字段声明写错，Win32 调用会读到
    错位的数据，表现为随机崩溃，因此用测试固定下来。
    """

    def test_processentry32w_layout(self):
        self.assertEqual(ctypes.sizeof(limiter.PROCESSENTRY32W), 568)
        self.assertEqual(limiter.PROCESSENTRY32W.szExeFile.offset, 44)

    def test_threadentry32_layout(self):
        self.assertEqual(ctypes.sizeof(limiter.THREADENTRY32), 28)
        self.assertEqual(limiter.THREADENTRY32.th32OwnerProcessID.offset, 12)

    def test_job_object_structs(self):
        self.assertEqual(
            ctypes.sizeof(limiter.JOBOBJECT_BASIC_LIMIT_INFORMATION), 80)
        self.assertEqual(
            ctypes.sizeof(limiter.JOBOBJECT_CPU_RATE_CONTROL_INFORMATION), 8)

    def test_physical_monitor_layout(self):
        self.assertEqual(ctypes.sizeof(ddcci.PHYSICAL_MONITOR), 264)
        self.assertEqual(
            ddcci.PHYSICAL_MONITOR.hPhysicalMonitor.offset, 0)

    def test_monitorinfoexw_layout(self):
        self.assertEqual(ctypes.sizeof(ddcci.MONITORINFOEXW), 104)


class MonitorInfoTest(unittest.TestCase):
    """MonitorInfo 的属性行为。"""

    def test_current_setter_updates_value(self):
        monitor = _monitor(friendly="X")
        monitor.current = 73
        self.assertEqual(monitor.current, 73)

    def test_range_returns_min_max(self):
        monitor = ddcci.MonitorInfo(1, 1, "N", 10, 90, 50)
        self.assertEqual(monitor.range, (10, 90))

    def test_null_padding_is_stripped(self):
        """Win32 返回的字符串带 \\x00 填充，构造时应清掉。"""
        monitor = ddcci.MonitorInfo(
            1, 1, "Generic\x00\x00", 0, 100, 50,
            device="\\\\.\\DISPLAY1\x00", friendly=" LG  ",
        )
        self.assertEqual(monitor.name, "Generic")
        self.assertEqual(monitor.device, r"\\.\DISPLAY1")
        self.assertEqual(monitor.friendly, "LG")


if __name__ == "__main__":
    unittest.main()
