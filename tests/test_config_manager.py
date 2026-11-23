"""config.json 读写与默认值合并的行为测试。

每个用例都会把 ``_CONFIG_PATH`` 重定向到临时目录，不会碰到真实的
配置文件。
"""

import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from core import config_manager as cm


class ConfigManagerTest(unittest.TestCase):
    """覆盖加载、合并、容错与存取往返。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_path = cm._CONFIG_PATH
        cm._CONFIG_PATH = os.path.join(self._tmp.name, "config.json")
        self.manager = cm.ConfigManager()

    def tearDown(self):
        cm._CONFIG_PATH = self._orig_path
        self._tmp.cleanup()

    def _write(self, payload):
        """把给定字典写成当前测试用的 config.json。"""
        with open(cm._CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def test_missing_file_is_created_with_defaults(self):
        """文件不存在时应自动生成，并带上 general 默认值。"""
        self.assertFalse(os.path.exists(cm._CONFIG_PATH))

        data = self.manager.load({})

        self.assertTrue(os.path.exists(cm._CONFIG_PATH))
        self.assertEqual(data["general"]["log_level"], "info")
        self.assertIs(data["general"]["autostart"], False)

    def test_user_value_overrides_default(self):
        """用户写进文件的值优先于默认值，缺失的键要补齐。"""
        self._write({"general": {"log_level": "debug"}})

        data = self.manager.load({})

        self.assertEqual(data["general"]["log_level"], "debug")
        self.assertIn("autostart", data["general"])

    def test_unknown_keys_are_preserved(self):
        """未来版本新增的键不应被合并逻辑丢弃。"""
        self._write({"general": {"log_level": "warning", "future_option": 42}})

        data = self.manager.load({})

        self.assertEqual(data["general"]["future_option"], 42)

    def test_builtin_plugin_uses_module_defaults(self):
        """内置插件的默认值来自模块级 _CONFIG，而不是插件类的 default_config。

        这正是审计报告 P1-1 记录的双源问题：sguard_limiter 的
        ``_CONFIG`` 只有 ``interval``，与插件实际读取的
        ``monitor_interval`` 对不上。此处固定当前行为作为回归基线。
        """
        self._write({"plugins": {"sguard_limiter": {"cpu_percent": 20}}})

        data = self.manager.load({})
        cfg = data["plugins"]["sguard_limiter"]

        self.assertEqual(cfg["cpu_percent"], 20)
        self.assertIn("interval", cfg)
        self.assertNotIn("monitor_interval", cfg)

    def test_new_plugin_uses_its_own_default_config(self):
        """不在内置表中的插件，默认值取自它自己的 default_config。"""
        plugin = SimpleNamespace(default_config={"enabled": False, "threshold": 7})

        data = self.manager.load({"custom_tool": plugin})

        self.assertEqual(
            data["plugins"]["custom_tool"], {"enabled": False, "threshold": 7})

    def test_user_value_overrides_plugin_default(self):
        """新插件的用户配置同样优先于它自己的默认值。"""
        self._write({"plugins": {"custom_tool": {"threshold": 99}}})
        plugin = SimpleNamespace(default_config={"enabled": False, "threshold": 7})

        data = self.manager.load({"custom_tool": plugin})

        self.assertEqual(data["plugins"]["custom_tool"]["threshold"], 99)
        self.assertIs(data["plugins"]["custom_tool"]["enabled"], False)

    def test_corrupted_json_falls_back_to_defaults(self):
        """JSON 损坏时回退到默认值。

        当前实现既不修复磁盘文件也不记录日志（审计报告 P1-2），
        这里把该行为固定下来，等修复后应改为断言文件被重建。
        """
        with open(cm._CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write('{"general": {"log_level": "debug"}, broken')

        data = self.manager.load({})

        self.assertEqual(data["general"]["log_level"], "info")
        with open(cm._CONFIG_PATH, encoding="utf-8") as f:
            self.assertIn("broken", f.read())

    def test_get_plugin_config_returns_empty_for_unknown(self):
        """查询不存在的插件返回空字典而不是抛异常。"""
        self.manager.load({})

        self.assertEqual(self.manager.get_plugin_config("not_a_plugin"), {})

    def test_save_then_load_roundtrip(self):
        """save 后由新实例读回，改动应保留。"""
        self.manager.load({})
        self.manager.raw["general"]["log_level"] = "error"
        self.manager.save()

        data = cm.ConfigManager().load({})

        self.assertEqual(data["general"]["log_level"], "error")


if __name__ == "__main__":
    unittest.main()
