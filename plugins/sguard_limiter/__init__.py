"""SGuard 限速插件入口，向插件管理器导出 ``PLUGIN``。"""

from plugins.sguard_limiter.plugin import SGuardLimiterPlugin

PLUGIN = SGuardLimiterPlugin
