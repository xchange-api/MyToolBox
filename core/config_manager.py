"""config.json 的读写与默认值合并。

配置文件位于应用根目录，格式为 UTF-8 JSON，顶层分为 ``general``
和 ``plugins`` 两段。用户在文件中提供的值优先于默认值。
"""

import json
import os

from core import get_app_dir

# 内置插件的默认配置。注意这与各插件类上的 ``default_config`` 是两份
# 独立来源，其中 sguard_limiter 一节的键名已与插件实际读取的配置漂移
# （只有 interval，缺 cpu_percent / monitor_interval / reapply_interval），
# 详见审计报告 P1-1。
_CONFIG = {
    "general": {
        "autostart": False,
        "log_level": "info",
    },
    "plugins": {
        "input_state_notifier": {
            "enabled": True,
            "toast_duration": 0.8,
            "debounce_interval": 0.3,
            "ime_check_delay": 0.05,
        },
        "sguard_limiter": {
            "enabled": False,
            "interval": 1.0,
        },
        "brightness_controller": {
            "enabled": True,
            "power_off_on_shutdown": False,
        },
    },
}

_CONFIG_PATH = os.path.join(get_app_dir(), "config.json")


class ConfigManager:
    """负责加载、合并并持久化 config.json。"""

    def __init__(self):
        self._data = {}

    def load(self, plugins=None):
        """读取配置文件并与默认值合并。

        Args:
            plugins: 插件名到插件实例的映射，用于为不在内置默认表中的
                插件补充 ``default_config``。

        Returns:
            合并后的完整配置字典。

        Note:
            当前实现遇到损坏的 JSON 会静默回退到默认值，既不记录日志
            也不修复磁盘上的坏文件（审计报告 P1-2）。
        """
        plugins = plugins or {}
        if os.path.exists(_CONFIG_PATH):
            try:
                with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._data = raw
            except (json.JSONDecodeError, Exception):
                self._data = {}
        self._merge_defaults(plugins)
        self._ensure_file()
        return self._data

    def _merge_defaults(self, plugins):
        """把默认值与用户配置合并进 ``self._data``。

        内置插件以模块级 ``_CONFIG`` 为准，其余插件以
        ``plugin.default_config`` 为准；用户值始终覆盖默认值。
        """
        for section in ("general", "plugins"):
            if section not in self._data:
                self._data[section] = {}

        for key, val in _CONFIG["general"].items():
            self._data["general"].setdefault(key, val)

        for name, defaults in _CONFIG["plugins"].items():
            user = self._data["plugins"].get(name, {})
            merged = defaults.copy()
            merged.update(user)
            self._data["plugins"][name] = merged

        for name, plugin in plugins.items():
            if name not in _CONFIG["plugins"]:
                cfg = self._data["plugins"].setdefault(name, {})
                defaults = plugin.default_config.copy()
                defaults.update(cfg)
                self._data["plugins"][name] = defaults

    def _ensure_file(self):
        """配置文件不存在时，用当前内存数据落盘。"""
        if not os.path.exists(_CONFIG_PATH):
            self.save()

    def save(self):
        """把内存中的配置写入磁盘。

        直接覆盖目标文件，非原子写入。
        """
        os.makedirs(os.path.dirname(_CONFIG_PATH) or ".", exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)

    def get_plugin_config(self, name):
        """返回指定插件的配置段，不存在时返回空字典。"""
        return self._data.get("plugins", {}).get(name, {})

    def get_general(self):
        """返回 ``general`` 配置段。"""
        return self._data.get("general", {})

    @property
    def raw(self):
        """返回内部配置字典的引用，修改会影响后续 ``save()``。"""
        return self._data
