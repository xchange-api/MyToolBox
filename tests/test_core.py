"""核心基础设施测试：单实例锁、IPC 消息解析、插件生命周期隔离。"""

import os
import unittest
from types import SimpleNamespace

import main
from core import ipc_server
from core.plugin_manager import PluginManager


class SingleInstanceTest(unittest.TestCase):
    """命名互斥体实现的单实例约束。"""

    def test_second_instance_exits(self):
        """第一次创建互斥体正常返回，第二次应直接退出进程。

        这里用带 PID 的唯一名字，避免与真实运行的 MyToolBox 冲突。
        """
        original_name = main.MUTEX_NAME
        main.MUTEX_NAME = "MyToolBox_UnitTest_%d" % os.getpid()
        try:
            main._ensure_single_instance()
            with self.assertRaises(SystemExit) as ctx:
                main._ensure_single_instance()
            self.assertEqual(ctx.exception.code, 0)
        finally:
            main.MUTEX_NAME = original_name


class IpcPipeNameTest(unittest.TestCase):
    """管道名拼接规则。"""

    def test_explicit_pid_is_used(self):
        self.assertEqual(
            ipc_server.make_pipe_name("brightness_controller", 4321),
            r"\\.\pipe\MyToolBox\brightness_controller_4321",
        )

    def test_defaults_to_current_pid(self):
        name = ipc_server.make_pipe_name("demo")
        self.assertTrue(name.endswith("demo_%d" % os.getpid()))

    def test_different_pids_produce_different_pipes(self):
        """同机多实例时不能串台。"""
        self.assertNotEqual(
            ipc_server.make_pipe_name("demo", 1),
            ipc_server.make_pipe_name("demo", 2),
        )


class IpcMessageTest(unittest.TestCase):
    """JSON-line 消息解析。"""

    def setUp(self):
        self.server = ipc_server.IpcServer()
        self.received = []
        self.server.set_message_callback(
            lambda name, msg: self.received.append((name, msg)))

    def test_valid_json_is_dispatched(self):
        self.server._on_message("demo", b'{"type": "status", "running": true}')
        self.assertEqual(
            self.received, [("demo", {"type": "status", "running": True})])

    def test_invalid_json_is_silently_dropped(self):
        """坏消息不能冒泡成异常，否则读取线程会退出。"""
        self.server._on_message("demo", b"not a json line")
        self.assertEqual(self.received, [])

    def test_utf8_payload_roundtrip(self):
        self.server._on_message("demo", '{"type": "log", "message": "中文"}'.encode("utf-8"))
        self.assertEqual(
            self.received[0][1]["message"], "中文")


class PluginManagerIsolationTest(unittest.TestCase):
    """插件异常隔离：单个插件失败不能影响其他插件。"""

    def setUp(self):
        self.manager = PluginManager(
            SimpleNamespace(get_plugin_config=lambda name: {}))

    def test_start_failure_does_not_raise(self):
        class Broken:
            name = "broken"

            def on_start(self):
                raise RuntimeError("boom")

        self.manager.plugins["broken"] = Broken()
        self.manager.start_plugin("broken")   # 不应抛出

    def test_other_plugins_still_start(self):
        class Broken:
            name = "broken"

            def on_start(self):
                raise RuntimeError("boom")

        class Healthy:
            name = "healthy"

            def __init__(self):
                self.started = False

            def on_start(self):
                self.started = True

        healthy = Healthy()
        self.manager.plugins["broken"] = Broken()
        self.manager.plugins["healthy"] = healthy

        self.manager.start_plugin("broken")
        self.manager.start_plugin("healthy")

        self.assertTrue(healthy.started)

    def test_stop_failure_does_not_raise(self):
        class Broken:
            name = "broken"

            def on_stop(self):
                raise RuntimeError("boom")

        self.manager.plugins["broken"] = Broken()
        self.manager.stop_all()   # 不应抛出

    def test_unknown_plugin_is_noop(self):
        """对不存在的插件调用 start/stop 应安静返回。"""
        self.manager.start_plugin("missing")
        self.manager.stop_plugin("missing")


if __name__ == "__main__":
    unittest.main()
