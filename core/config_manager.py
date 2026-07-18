import json
import os

from core import get_app_dir


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
        },
    },
}

_CONFIG_PATH = os.path.join(get_app_dir(), "config.json")


class ConfigManager:
    def __init__(self):
        self._data = {}

    def load(self, plugins=None):
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
        if not os.path.exists(_CONFIG_PATH):
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(_CONFIG_PATH) or ".", exist_ok=True)
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=4, ensure_ascii=False)

    def get_plugin_config(self, name):
        return self._data.get("plugins", {}).get(name, {})

    def get_general(self):
        return self._data.get("general", {})

    @property
    def raw(self):
        return self._data
