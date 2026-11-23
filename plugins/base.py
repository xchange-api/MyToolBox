"""插件基类与生命周期约定。

插件包在自身 ``__init__.py`` 中导出 ``PLUGIN`` 属性指向一个 ``Plugin``
子类，``PluginManager`` 据此发现并实例化。
"""


class TrayMenuItem:
    """插件自注册菜单项的载体。

    Note:
        这是 tkinter/pystray 时代留下的结构。当前托盘菜单由
        ``TrayManager`` 自行按插件列表生成，``get_menu_items`` 也不再有
        调用方，本类实际未被使用。
    """

    def __init__(self, text, callback=None, checked=None, enabled=True, separator=False):
        self.text = text
        self.callback = callback
        self.checked = checked
        self.enabled = enabled
        self.separator = separator


class Plugin:
    """所有插件的基类。

    子类通过类属性声明元数据，按需覆盖生命周期钩子。宿主对每个钩子
    调用都做了异常隔离，单个插件抛错不会影响其他插件。
    """

    name = ""
    display_name = ""
    description = ""
    category = ""
    needs_admin = False
    default_config = {}

    def on_load(self, config):
        """插件加载时调用，用于保存配置、准备资源。

        Args:
            config: 该插件在 config.json 中合并后的配置字典。
        """

    def on_start(self):
        """用户在托盘启用插件时调用。"""

    def on_stop(self):
        """用户在托盘停用插件、或应用退出时调用。"""

    def on_config_change(self, new_config):
        """配置变更时调用。

        Note:
            宿主目前没有任何地方触发该回调，修改 config.json 后需要
            重启才生效（审计报告 P1-13）。
        """

    def get_menu_items(self):
        """返回插件希望注册到托盘的菜单项。

        Note:
            当前实现未使用该返回值。
        """
        return []
