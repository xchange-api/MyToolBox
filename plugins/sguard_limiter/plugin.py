"""SGuard 限速插件（Core 侧声明）。

真正的限速逻辑运行在提权后的 ``--helper`` 子进程里（见
``plugin_helper.py``），本类只负责元数据声明，生命周期钩子均为空。
"""

from plugins.base import Plugin


class SGuardLimiterPlugin(Plugin):
    """声明一个需要管理员权限的限速插件。"""

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
        """保存应用引用。

        Note:
            宿主实际使用 ``app.initialize`` 里直接赋值的 ``plugin.app``
            属性，本方法没有调用方。
        """
        self._app = app

    def on_load(self, config):
        """Core 侧无需加载资源，限速由 helper 进程完成。"""

    def on_start(self):
        """Core 侧不直接启动，由 ``MyToolBoxApp._start_helper_plugin`` 处理。"""

    def on_stop(self):
        """Core 侧不直接停止，由 IPC 通知 helper 退出。"""

    def on_config_change(self, new_config):
        """配置变更回调，当前实现为空。"""

    def get_menu_items(self):
        """该插件不额外注册菜单项。"""
        return []
