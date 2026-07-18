import os
import threading

import pystray
from PIL import Image, ImageDraw, ImageFont

from core.logger import get_logger


def _create_default_icon():
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 2, size - 2], radius=8, fill=(0, 120, 215))
    try:
        font = ImageFont.truetype("segoeui.ttf", 24)
    except OSError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), "TB", font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (size - tw) // 2
    y = (size - th) // 2 - 2
    draw.text((x, y), "TB", fill="white", font=font)
    return img


class TrayManager:
    def __init__(self, app_ref):
        self._app = app_ref
        self._icon = None
        self._log = get_logger()
        self._menu_lock = threading.Lock()

    def start(self):
        menu = self._build_menu()
        self._icon = pystray.Icon(
            "MyToolBox",
            _create_default_icon(),
            "MyToolBox",
            menu,
        )
        self._icon.run()

    def stop(self):
        if self._icon:
            self._icon.stop()
            self._icon = None

    def update_menu(self):
        if self._icon:
            self._icon.menu = self._build_menu()

    def _build_menu(self):
        items = []

        categories = {}
        for name, plugin in self._app.plugins.items():
            cat = plugin.category or "其他"
            categories.setdefault(cat, []).append((name, plugin))

        first = True
        for cat_label in ["系统工具", "性能工具", "其他"]:
            if cat_label not in categories:
                continue
            cat_plugins = categories[cat_label]
            if not first:
                items.append(pystray.Menu.SEPARATOR)
            first = False

            sub_items = []
            for name, plugin in cat_plugins:
                sub_items.append(
                    pystray.MenuItem(
                        plugin.display_name,
                        lambda *_, n=name: self._on_toggle(n),
                        checked=lambda item, n=name: self._is_active(n),
                    )
                )
                if name in self._app.active_helper_plugins:
                    status = self._app.helper_statuses.get(name, {})
                    if status.get("running"):
                        sub_items.append(
                            pystray.MenuItem(
                                f"状态: 运行中",
                                None,
                                enabled=False,
                            )
                        )

            if len(sub_items) == 1:
                items.append(sub_items[0])
            else:
                items.append(pystray.MenuItem(cat_label, pystray.Menu(*sub_items)))

        items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem(
                "开机自启",
                self._app.on_toggle_autostart,
                checked=lambda item: self._app.autostart_enabled,
            )
        )
        items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem(
                "退出",
                self._app.on_exit,
            )
        )

        return pystray.Menu(*items)

    def _on_toggle(self, name):
        self._app.toggle_plugin(name)

    def _is_active(self, name):
        return (name in self._app.active_plugins
                or name in self._app.active_helper_plugins)
