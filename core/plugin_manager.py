"""插件发现与生命周期管理。

扫描 ``plugins/`` 下的子包，找出导出 ``PLUGIN`` 属性的模块并实例化。
每个生命周期调用都被 try/except 包裹，单个插件故障不会影响宿主。
"""

import importlib
import pkgutil

from core.logger import get_logger


def _iter_plugin_dirs():
    """依次产出 ``plugins/`` 下所有非私有的子包名。"""
    import plugins as plugins_pkg
    for finder, name, ispkg in pkgutil.iter_modules(plugins_pkg.__path__):
        if ispkg and not name.startswith("_"):
            yield name


class PluginManager:
    """维护插件实例表，并代理它们的启动与停止。"""

    def __init__(self, config_manager):
        self._plugins = {}
        self._config = config_manager
        self._log = get_logger()

    def discover(self):
        """导入所有插件包并实例化。

        缺少 ``PLUGIN`` 属性或导入失败的插件会被记录日志后跳过。

        Returns:
            插件名到插件实例的映射。
        """
        for name in _iter_plugin_dirs():
            try:
                mod = importlib.import_module(f"plugins.{name}")
                if not hasattr(mod, "PLUGIN"):
                    self._log.warning(f"插件 {name} 没有 PLUGIN 属性，跳过")
                    continue
                plugin_class = mod.PLUGIN
                instance = plugin_class()
                # 类属性统一搬到实例上，便于后续按插件名取用
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
        """对所有已发现插件调用 ``on_load``，并传入各自的合并配置。"""
        for name, plugin in self._plugins.items():
            try:
                cfg = self._config.get_plugin_config(name)
                plugin.on_load(cfg)
            except Exception as e:
                self._log.error(f"插件 {name} on_load 失败: {e}")

    def start_plugin(self, name):
        """启动指定插件；异常只记录，不向外抛出。"""
        plugin = self._plugins.get(name)
        if not plugin:
            return
        try:
            plugin.on_start()
        except Exception as e:
            self._log.error(f"插件 {name} 启动失败: {e}")

    def stop_plugin(self, name):
        """停止指定插件；异常只记录，不向外抛出。"""
        plugin = self._plugins.get(name)
        if not plugin:
            return
        try:
            plugin.on_stop()
        except Exception as e:
            self._log.error(f"插件 {name} 停止失败: {e}")

    def stop_all(self):
        """停止全部插件，用于应用退出。"""
        for name in list(self._plugins.keys()):
            self.stop_plugin(name)

    @property
    def plugins(self):
        """插件名到插件实例的映射。"""
        return self._plugins
