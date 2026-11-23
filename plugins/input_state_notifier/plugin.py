"""CapsLock / NumLock / IME 状态提示插件。

把 ``InputStateMonitor`` 的键盘钩子接入插件生命周期：加载时创建监控
对象，启用时挂钩子，停用时摘钩子。
"""

from plugins.base import Plugin
from plugins.input_state_notifier.notifier import InputStateMonitor


class InputStateNotifierPlugin(Plugin):
    """状态变化时在屏幕中央弹出 Toast 的插件。"""

    name = "input_state_notifier"
    display_name = "输入状态通知"
    description = "CapsLock / NumLock / IME 状态变化时显示 Toast 弹窗"
    category = "系统工具"
    needs_admin = False
    default_config = {
        "toast_duration": 0.8,
        "debounce_interval": 0.3,
        "ime_check_delay": 0.05,
    }

    def __init__(self):
        super().__init__()
        self._monitor = None

    def on_load(self, config):
        """创建监控对象（此时尚未注册全局钩子）。"""
        self._monitor = InputStateMonitor(config)

    def on_start(self):
        """注册全局键盘钩子并开始监听状态变化。"""
        if self._monitor:
            self._monitor.start()

    def on_stop(self):
        """摘掉全局键盘钩子。"""
        if self._monitor:
            self._monitor.stop()

    def on_config_change(self, new_config):
        """配置变更回调，当前实现为空。"""

    def get_menu_items(self):
        """该插件不额外注册菜单项。"""
        return []
