"""亮度控制插件入口，向插件管理器导出 ``PLUGIN``。"""

from plugins.brightness_controller.plugin import BrightnessPlugin

PLUGIN = BrightnessPlugin
