import importlib
import pkgutil

from core.logger import get_logger


def _iter_plugin_dirs():
    import plugins as plugins_pkg
    for finder, name, ispkg in pkgutil.iter_modules(plugins_pkg.__path__):
        if ispkg and not name.startswith("_"):
            yield name


class PluginManager:
    def __init__(self, config_manager):
        self._plugins = {}
        self._config = config_manager
        self._log = get_logger()

    def discover(self):
        for name in _iter_plugin_dirs():
            try:
                mod = importlib.import_module(f"plugins.{name}")
                if not hasattr(mod, "PLUGIN"):
                    self._log.warning(f"插件 {name} 没有 PLUGIN 属性，跳过")
                    continue
                plugin_class = mod.PLUGIN
                instance = plugin_class()
                instance.name = getattr(plugin_class, "name", name)
                instance.display_name = getattr(plugin_class, "display_name", name)
                instance.description = getattr(plugin_class, "description", "")
                instance.category = getattr(plugin_class, "category", "")
                instance.needs_admin = getattr(plugin_class, "needs_admin", False)
                instance.default_config = getattr(plugin_class, "default_config", {})
                self._plugins[instance.name] = instance
                self._log.info(f"加载插件: {instance.display_name} ({instance.name})")
            except Exception as e:
                self._log.error(f"加载插件 {name} 失败: {e}")
        return self._plugins

    def load_all(self):
        for name, plugin in self._plugins.items():
            try:
                cfg = self._config.get_plugin_config(name)
                plugin.on_load(cfg)
            except Exception as e:
                self._log.error(f"插件 {name} on_load 失败: {e}")

    def start_plugin(self, name):
        plugin = self._plugins.get(name)
        if not plugin:
            return
        try:
            plugin.on_start()
        except Exception as e:
            self._log.error(f"插件 {name} 启动失败: {e}")

    def stop_plugin(self, name):
        plugin = self._plugins.get(name)
        if not plugin:
            return
        try:
            plugin.on_stop()
        except Exception as e:
            self._log.error(f"插件 {name} 停止失败: {e}")

    def stop_all(self):
        for name in list(self._plugins.keys()):
            self.stop_plugin(name)

    @property
    def plugins(self):
        return self._plugins
