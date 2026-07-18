from plugins.base import Plugin, TrayMenuItem


class SGuardLimiterPlugin(Plugin):
    name = "sguard_limiter"
    display_name = "SGuard 限速"
    description = "检测 SGuard64.exe 并限制其 CPU/线程/IO 优先级"
    category = "性能工具"
    needs_admin = True
    default_config = {
        "cpu_percent": 5,
        "monitor_interval": 3.0,
        "reapply_interval": 30.0,
    }

    def __init__(self):
        super().__init__()
        self._app = None

    def set_app(self, app):
        self._app = app

    def on_load(self, config):
        pass

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def on_config_change(self, new_config):
        pass

    def get_menu_items(self):
        return []
