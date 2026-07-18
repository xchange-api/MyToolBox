from plugins.base import Plugin
from plugins.input_state_notifier.notifier import InputStateMonitor


class InputStateNotifierPlugin(Plugin):
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
        self._monitor = InputStateMonitor(config)

    def on_start(self):
        if self._monitor:
            self._monitor.start()

    def on_stop(self):
        if self._monitor:
            self._monitor.stop()

    def on_config_change(self, new_config):
        pass

    def get_menu_items(self):
        from plugins.base import TrayMenuItem
        return []
