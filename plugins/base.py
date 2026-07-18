class TrayMenuItem:
    def __init__(self, text, callback=None, checked=None, enabled=True, separator=False):
        self.text = text
        self.callback = callback
        self.checked = checked
        self.enabled = enabled
        self.separator = separator


class Plugin:
    name = ""
    display_name = ""
    description = ""
    category = ""
    needs_admin = False
    default_config = {}

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
