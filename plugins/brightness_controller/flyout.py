import ctypes
import math
import re
import threading
import winreg

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QPainter,
    QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from plugins.brightness_controller.ddcci import (
    cursor_monitor, get_brightness, set_brightness, set_brightness_async,
    cleanup, enumerate_monitors_async,
)

BG = "#3C3C3C"
FG = "#ffffff"
SUN = "#ffc832"
TRACK = "#6b6b6b"
SEP = "#2d2d2d"
HOVER = "#4a4a4a"

TRACK_H = 1
THUMB_W = 8
THUMB_H = 24
TRACK_INSET = 10

FLYOUT_W = 360
HEADER_H = 42
SLIDER_AREA_H = 54
LIST_ITEM_H = 40
LIST_MAX_VISIBLE = 5
ICON_W = 24
VALUE_W = 46
ROW_H = 42
SLIDER_W = 250
AUTO_MS = 2500

SCROLL_QSS = """
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical {
    background: transparent; width: 8px; margin: 2px 2px 2px 0;
}
QScrollBar::handle:vertical {
    background: #6b6b6b; border-radius: 4px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #8a8a8a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
"""


def get_accent_color():
    sources = [
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "AccentColor"),
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "StartColor"),
        (r"Software\Microsoft\Windows\DWM", "AccentColor"),
    ]
    for path, name in sources:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                val, _ = winreg.QueryValueEx(k, name)
            if val == 0 or val == 0xFFFFFFFF:
                continue
            r = val & 0xFF
            g = (val >> 8) & 0xFF
            b = (val >> 16) & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
        except (FileNotFoundError, OSError):
            continue
    try:
        color = ctypes.c_uint32()
        opaque = ctypes.c_int()
        if ctypes.windll.dwmapi.DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
            v = color.value
            r = (v >> 16) & 0xFF
            g = (v >> 8) & 0xFF
            b = v & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return "#0078d4"


def _make_sun_pixmap(size=20):
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    cx = cy = size / 2.0
    r = size * 0.26
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(SUN))
    painter.drawEllipse(QPoint(int(cx), int(cy)), int(r), int(r))

    painter.setPen(QPen(QColor(SUN), 1))
    for degree in range(0, 360, 45):
        rad = math.radians(degree)
        x1 = cx + r * 1.35 * math.cos(rad)
        y1 = cy + r * 1.35 * math.sin(rad)
        x2 = cx + r * 1.85 * math.cos(rad)
        y2 = cy + r * 1.85 * math.sin(rad)
        painter.drawLine(
            QPoint(int(round(x1)), int(round(y1))),
            QPoint(int(round(x2)), int(round(y2))),
        )

    painter.end()
    return pix


class BrightnessSlider(QSlider):
    """与旧版 Canvas 滑块行为对齐的 Qt 滑块。"""

    def __init__(self, value=50, command=None, accent="#0078d4", parent=None):
        super().__init__(Qt.Orientation.Horizontal, parent)
        self._cmd = command
        self._accent = accent

        self.setRange(0, 100)
        self.setSingleStep(1)
        self.setPageStep(5)
        self.setValue(max(0, min(100, int(value))))
        self.setFixedWidth(SLIDER_W)
        self.setFixedHeight(ROW_H)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.valueChanged.connect(self._on_value_changed)

    def _value_from_x(self, x):
        left = float(TRACK_INSET)
        right = self.width() - float(TRACK_INSET)
        if right <= left:
            return self.value()
        ratio = (x - left) / (right - left)
        return max(0, min(100, int(round(ratio * 100))))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        width = float(self.width())
        center_y = self.height() / 2.0
        left = float(TRACK_INSET)
        right = width - float(TRACK_INSET)
        if right <= left:
            painter.end()
            return

        ratio = self.value() / 100.0
        thumb_center_x = left + (right - left) * ratio
        track_top = center_y - TRACK_H
        track_height = TRACK_H * 2

        # 背景轨道
        painter.setBrush(QColor(TRACK))
        painter.drawRect(QRectF(left, track_top, right - left, track_height))

        # 已填充部分：从轨道起点画到滑块中心，不留空隙
        fill_width = thumb_center_x - left
        if fill_width > 0:
            painter.setBrush(QColor(self._accent))
            painter.drawRect(QRectF(left, track_top, fill_width, track_height))

        # 滑块
        painter.setBrush(QColor(self._accent))
        painter.drawRoundedRect(
            QRectF(
                thumb_center_x - THUMB_W / 2.0,
                center_y - THUMB_H / 2.0,
                THUMB_W,
                THUMB_H,
            ),
            THUMB_W / 2.0,
            THUMB_W / 2.0,
        )

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        delta = 5 if event.angleDelta().y() > 0 else -5
        self.setValue(max(0, min(100, self.value() + delta)))
        event.accept()

    def _on_value_changed(self, value):
        if self._cmd:
            self._cmd(value)


class ClickableRow(QWidget):
    """可点击的行，带 Windows 风格的鼠标悬停高亮"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        if self.underMouse():
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(HOVER))
            painter.end()


class BrightnessFlyout(QWidget):
    """亮度浮窗，布局参考 Windows 10 音量面板。

    折叠时只显示当前显示器和一条滑块；点击顶部展开显示器列表，
    选择后自动折叠。必须在 Qt GUI 线程创建和使用。
    """

    def __init__(self, app, icon_path):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.app = app
        self._icon_path = icon_path
        self._accent = get_accent_color()

        self.setObjectName("brightnessFlyout")
        self.setStyleSheet(f"#brightnessFlyout {{ background: {BG}; }}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._monitors = []
        self._titles = []
        self._selected = None
        self._expanded = False
        self._show_target = 0
        self._generation = 0
        self._prev_mouse = False
        self._setting_values = False
        self._debounce_timers = {}
        self._pending = {}

        self._title_label = None
        self._chevron = None
        self._selector = None
        self._slider = None
        self._value_label = None
        self._list_rows = []

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._close)

        self._outside_timer = QTimer(self)
        self._outside_timer.setInterval(50)
        self._outside_timer.timeout.connect(self._check_outside)

    # ---- 显示 / 枚举 ----

    def show(self):
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            self._schedule()
            return

        # 清掉上一次可能残留的显示器句柄和防抖计时器
        self._close()

        self._generation += 1
        generation = self._generation
        self._show_target = cursor_monitor()
        self._expanded = False

        enumerate_monitors_async(
            lambda monitors: self.app.schedule_ui(
                lambda: self._on_monitors(generation, monitors)
            )
        )

    def _on_monitors(self, generation, monitors):
        if generation != self._generation:
            cleanup(monitors)
            return
        self._monitors = monitors
        if not self._monitors:
            return

        self._titles = self._monitor_titles()
        self._selected = next(
            (m for m in self._monitors
             if m.hmonitor == self._show_target),
            self._monitors[0],
        )
        self._build_ui()

    def _build_ui(self):
        self._clear_ui()

        header = self._build_header()
        self._layout.addWidget(header)

        separator = QFrame(self)
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {SEP};")
        self._layout.addWidget(separator)

        self._selector = self._build_selector()
        self._selector.setVisible(self._expanded)
        self._layout.addWidget(self._selector)

        self._layout.addWidget(self._build_slider_row())

        self._resize_to_content()
        super().show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._schedule()
        self._prev_mouse = bool(
            ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
        self._outside_timer.start()

    def _resize_to_content(self):
        list_h = 0
        if self._expanded:
            visible = min(len(self._monitors), LIST_MAX_VISIBLE)
            list_h = visible * LIST_ITEM_H
        height = HEADER_H + 1 + list_h + SLIDER_AREA_H
        self.setFixedSize(FLYOUT_W, height)
        self._position(FLYOUT_W, height)

    # ---- 顶部标题栏 ----

    def _build_header(self):
        header = ClickableRow(self)
        header.setFixedHeight(HEADER_H)
        if len(self._monitors) > 1:
            header.clicked.connect(self._toggle_expanded)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(15, 0, 12, 0)
        layout.setSpacing(8)

        self._title_label = QLabel(header)
        self._title_label.setFont(
            QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        self._title_label.setStyleSheet(
            f"color: {FG}; background: transparent;")
        self._title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self._title_label, 1)

        self._chevron = QLabel(header)
        self._chevron.setFixedWidth(16)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.setFont(QFont("Segoe MDL2 Assets", 9))
        self._chevron.setStyleSheet(
            f"color: {FG}; background: transparent;")
        self._chevron.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if len(self._monitors) <= 1:
            self._chevron.hide()
        layout.addWidget(self._chevron)

        self._update_header()
        return header

    def _update_header(self):
        if self._title_label is None or self._selected is None:
            return
        if self._expanded:
            text = "选择显示器"
        else:
            text = self._selected_title()

        metrics = QFontMetrics(self._title_label.font())
        width = FLYOUT_W - 15 - 12 - 16 - 8
        self._title_label.setText(metrics.elidedText(
            text, Qt.TextElideMode.ElideRight, width))
        self._chevron.setText("\uE70E" if self._expanded else "\uE70D")

    def _selected_title(self):
        for i, monitor in enumerate(self._monitors):
            if monitor is self._selected and i < len(self._titles):
                return self._titles[i]
        return "显示器"

    # ---- 显示器选择列表 ----

    def _build_selector(self):
        container = QWidget(self)
        visible = min(len(self._monitors), LIST_MAX_VISIBLE)
        container.setFixedHeight(visible * LIST_ITEM_H)

        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet(SCROLL_QSS)
        scroll.viewport().setAutoFillBackground(False)

        inner = QWidget()
        inner.setObjectName("selectorInner")
        inner.setAutoFillBackground(False)
        inner.setStyleSheet("#selectorInner { background: transparent; }")

        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        self._list_rows = []
        text_width = FLYOUT_W - 15 - 15 - 16 - 8
        for monitor, title in zip(self._monitors, self._titles):
            row = ClickableRow(inner)
            row.setFixedHeight(LIST_ITEM_H)
            row.setToolTip(f"{title}\n{monitor.name} · {monitor.device}".strip())

            layout = QHBoxLayout(row)
            layout.setContentsMargins(15, 0, 15, 0)
            layout.setSpacing(8)

            name = QLabel(title, row)
            name.setFont(QFont("Segoe UI", 10))
            name.setStyleSheet(f"color: {FG}; background: transparent;")
            name.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            metrics = QFontMetrics(name.font())
            name.setText(metrics.elidedText(
                title, Qt.TextElideMode.ElideRight, text_width))
            layout.addWidget(name, 1)

            check = QLabel("\u2713", row)
            check.setFixedWidth(16)
            check.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            check.setFont(QFont("Segoe UI", 11))
            check.setStyleSheet(
                f"color: {self._accent}; background: transparent;")
            check.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            check.setVisible(monitor is self._selected)
            layout.addWidget(check)

            row.clicked.connect(lambda m=monitor: self._select_monitor(m))
            inner_layout.addWidget(row)
            self._list_rows.append((monitor, check))

        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return container

    def _select_monitor(self, monitor):
        if monitor is self._selected:
            self._set_expanded(False)
            self._schedule()
            return

        self._flush_pending_now()
        self._selected = monitor
        self._refresh_selection()
        self._set_expanded(False)
        self._schedule()

    def _refresh_selection(self):
        if self._selected is None or self._slider is None:
            return

        current = get_brightness(self._selected)
        self._setting_values = True
        try:
            self._slider.setValue(current)
            self._value_label.setText(str(current))
        finally:
            self._setting_values = False

        for monitor, check in self._list_rows:
            check.setVisible(monitor is self._selected)
        self._update_header()

    def _toggle_expanded(self):
        if len(self._monitors) <= 1:
            return
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded):
        if expanded and len(self._monitors) <= 1:
            return
        if self._expanded == expanded:
            return
        self._expanded = expanded
        if self._selector is not None:
            self._selector.setVisible(expanded)
        self._update_header()
        self._resize_to_content()
        self._schedule()

    # ---- 滑块 ----

    def _build_slider_row(self):
        body = QWidget(self)
        body.setFixedHeight(SLIDER_AREA_H)

        layout = QHBoxLayout(body)
        layout.setContentsMargins(15, 6, 15, 6)
        layout.setSpacing(0)

        icon = QLabel(body)
        icon.setFixedSize(ICON_W, ROW_H)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(self._load_icon_pixmap())
        layout.addWidget(icon)
        layout.addSpacing(6)
        layout.addStretch(1)

        current = get_brightness(self._selected)

        self._value_label = QLabel(str(current), body)
        self._value_label.setFixedWidth(VALUE_W)
        self._value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._value_label.setFont(QFont("Segoe UI", 18))
        self._value_label.setStyleSheet(
            f"color: {FG}; background: transparent;")

        self._slider = BrightnessSlider(
            current,
            command=self._on_slider_changed,
            accent=self._accent,
            parent=body,
        )

        layout.addWidget(self._slider)
        layout.addWidget(self._value_label)
        return body

    def _load_icon_pixmap(self):
        pix = QIcon(self._icon_path).pixmap(20, 20)
        if pix.isNull():
            return _make_sun_pixmap(20)
        return pix

    def _on_slider_changed(self, value):
        if self._setting_values or self._selected is None:
            return
        self._value_label.setText(str(int(value)))
        self._schedule()
        self._queue_brightness(self._selected, int(value))

    def _queue_brightness(self, monitor, value):
        key = id(monitor)
        self._pending[key] = (monitor, value)

        timer = self._debounce_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(40)
            timer.timeout.connect(lambda k=key: self._flush_brightness(k))
            self._debounce_timers[key] = timer
        timer.start()

    def _flush_brightness(self, key):
        item = self._pending.pop(key, None)
        if item is None:
            return
        monitor, value = item
        set_brightness_async(monitor, value)

    def _take_pending(self):
        pending = list(self._pending.values())
        self._pending.clear()
        for timer in self._debounce_timers.values():
            timer.stop()
        return pending

    def _flush_pending_now(self):
        pending = self._take_pending()
        for monitor, value in pending:
            try:
                set_brightness(monitor, value)
            except Exception:
                pass

    # ---- 热键：只调鼠标所在显示器 ----

    def brightness_up(self):
        self._adjust_brightness(10)

    def brightness_down(self):
        self._adjust_brightness(-10)

    def _adjust_brightness(self, delta):
        # 在按键当下取一次 HMONITOR，避免异步枚举期间鼠标移动改变目标。
        target = cursor_monitor()
        if self._monitors:
            targets = [m for m in self._monitors if m.hmonitor == target]
            if targets:
                self._focus_monitor(targets[0])
                self._do_adjust(targets, delta)
            return
        enumerate_monitors_async(
            lambda monitors: self.app.schedule_ui(
                lambda: self._adjust_enumerated(monitors, target, delta)
            )
        )

    def _adjust_enumerated(self, monitors, target, delta):
        targets = [m for m in monitors if m.hmonitor == target]
        if not targets:
            cleanup(monitors)
            return
        self._do_adjust(targets, delta, cleanup_list=monitors)

    def _focus_monitor(self, monitor):
        if not self.isVisible() or monitor is self._selected:
            return
        self._flush_pending_now()
        self._selected = monitor
        self._refresh_selection()

    def _do_adjust(self, targets, delta, cleanup_list=None):
        def _worker():
            results = []
            for monitor in targets:
                try:
                    current = get_brightness(monitor)
                    new_value = max(0, min(100, current + delta))
                    set_brightness(monitor, new_value)
                    results.append((monitor, new_value))
                except Exception:
                    results.append((monitor, None))
            if cleanup_list:
                cleanup(cleanup_list)
            if self.app:
                self.app.schedule_ui(lambda: self._update_slider(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_slider(self, results):
        if self._selected is None or not self.isVisible():
            return

        self._setting_values = True
        try:
            for monitor, value in results:
                if monitor is self._selected and value is not None:
                    self._slider.setValue(value)
                    self._value_label.setText(str(int(value)))
                    break
        finally:
            self._setting_values = False

    # ---- 定位 / 自动关闭 ----

    def _position(self, w, h):
        # 使用 QScreen 的逻辑坐标（Qt move() 也是逻辑像素），避免缩放屏错位。
        # 浮窗跟随鼠标所在显示器，而不是固定钉在主屏。
        screen = (
            QGuiApplication.screenAt(QCursor.pos())
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return
        avail = screen.availableGeometry()
        full = screen.geometry()

        left = avail.x()
        top = avail.y()
        right = avail.x() + avail.width()
        bottom = avail.y() + avail.height()

        if full.x() < avail.x():
            # 任务栏在左侧
            x, y = left, bottom - h
        elif full.y() < avail.y():
            # 任务栏在顶部
            x, y = right - w, top
        else:
            # 任务栏在底部或右侧
            x, y = right - w, bottom - h
        self.move(x, y)

    def _check_outside(self):
        if not self.isVisible():
            self._outside_timer.stop()
            return

        pressed = bool(ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
        if pressed and not self._prev_mouse:
            if not self.frameGeometry().contains(QCursor.pos()):
                self._close()
                return
        self._prev_mouse = pressed

    def _schedule(self):
        # 展开选择列表时暂停自动关闭，避免用户还没选就消失。
        if self._expanded:
            self._auto_timer.stop()
        else:
            self._auto_timer.start(AUTO_MS)

    # ---- 清理 ----

    def _monitor_label(self, monitor, index):
        """优先用 EDID 型号名，取不到时退回 Windows 的显示器编号"""
        if monitor.friendly:
            return monitor.friendly
        match = re.search(r"DISPLAY(\d+)$", monitor.device)
        if match:
            return f"显示器 {match.group(1)}"
        return f"显示器 {index + 1}"

    def _monitor_titles(self):
        """生成每台显示器的标题；同名显示器追加序号，避免无法区分"""
        titles = [
            self._monitor_label(monitor, i)
            for i, monitor in enumerate(self._monitors)
        ]
        counts = {}
        for title in titles:
            counts[title] = counts.get(title, 0) + 1

        result = []
        seen = {}
        for title in titles:
            if counts[title] > 1:
                seen[title] = seen.get(title, 0) + 1
                result.append(f"{title} ({seen[title]})")
            else:
                result.append(title)
        return result

    def _clear_ui(self):
        self._title_label = None
        self._chevron = None
        self._selector = None
        self._slider = None
        self._value_label = None
        self._list_rows = []

        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for timer in self._debounce_timers.values():
            timer.stop()
            timer.deleteLater()
        self._debounce_timers.clear()
        self._pending.clear()

    def _close(self):
        self._generation += 1
        self._auto_timer.stop()
        self._outside_timer.stop()

        pending = self._take_pending()
        monitors = self._monitors
        self._monitors = []
        self._titles = []
        self._selected = None
        self._expanded = False

        self._clear_ui()
        self.hide()

        if pending or monitors:
            def _worker():
                for monitor, value in pending:
                    try:
                        set_brightness(monitor, value)
                    except Exception:
                        pass
                cleanup(monitors)

            threading.Thread(target=_worker, daemon=True).start()

    def close(self):
        self._close()

    def destroy(self):
        self._close()
        self.deleteLater()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._close()
        event.accept()

    def hideEvent(self, event):
        self._outside_timer.stop()
        super().hideEvent(event)
