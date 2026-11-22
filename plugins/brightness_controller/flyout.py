import ctypes
import math
import threading
import winreg

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QFont, QGuiApplication, QIcon, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget,
)

from plugins.brightness_controller.ddcci import (
    get_brightness, set_brightness, set_brightness_async,
    cleanup, enumerate_monitors_async,
)

BG = "#3C3C3C"
FG = "#ffffff"
SUN = "#ffc832"
TRACK = "#6b6b6b"
SEP = "#2d2d2d"

TRACK_H = 1
THUMB_W = 8
THUMB_H = 24
TRACK_INSET = 10

FLYOUT_PAD = 15
ROW_H = 42
SLIDER_W = 250
AUTO_MS = 2500


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


class BrightnessFlyout(QWidget):
    """亮度浮窗。必须在 Qt GUI 线程创建和使用。"""

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
        self._layout.setContentsMargins(
            FLYOUT_PAD, FLYOUT_PAD, FLYOUT_PAD, FLYOUT_PAD)
        self._layout.setSpacing(0)

        self._sliders = []
        self._labels = []
        self._monitors = []
        self._generation = 0
        self._prev_mouse = False
        self._setting_values = False
        self._debounce_timers = {}
        self._pending = {}

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._close)

        self._outside_timer = QTimer(self)
        self._outside_timer.setInterval(50)
        self._outside_timer.timeout.connect(self._check_outside)

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
        self._build_ui()

    def _build_ui(self):
        self._clear_rows()

        for i, monitor in enumerate(self._monitors):
            if i > 0:
                separator = QFrame(self)
                separator.setFixedHeight(1)
                separator.setStyleSheet(f"background: {SEP};")
                self._layout.addWidget(separator)
            self._layout.addWidget(self._build_row(monitor))

        n = len(self._monitors)
        total_h = n * ROW_H + (n - 1) * 1 + FLYOUT_PAD * 2
        total_w = 30 + SLIDER_W + 46 + FLYOUT_PAD * 2

        self.setFixedSize(total_w, total_h)
        self._position(total_w, total_h)
        super().show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._schedule()
        self._prev_mouse = bool(
            ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
        self._outside_timer.start()

    def _build_row(self, monitor):
        row = QWidget(self)
        row.setFixedHeight(ROW_H)

        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        icon = QLabel(row)
        icon.setFixedSize(24, ROW_H)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(self._load_icon_pixmap())
        layout.addWidget(icon)

        current = get_brightness(monitor)

        value_label = QLabel(str(current), row)
        value_label.setFixedWidth(46)
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value_label.setFont(QFont("Segoe UI", 18))
        value_label.setStyleSheet(f"color: {FG}; background: transparent;")

        slider = BrightnessSlider(
            current,
            command=lambda value, m=monitor, label=value_label:
                self._on_slider_changed(m, value, label),
            accent=self._accent,
            parent=row,
        )

        layout.addStretch(1)
        layout.addWidget(slider)
        layout.addWidget(value_label)

        self._sliders.append(slider)
        self._labels.append(value_label)
        return row

    def _load_icon_pixmap(self):
        pix = QIcon(self._icon_path).pixmap(20, 20)
        if pix.isNull():
            return _make_sun_pixmap(20)
        return pix

    def _on_slider_changed(self, monitor, value, label):
        if self._setting_values:
            return
        label.setText(str(int(value)))
        self._schedule()
        self._queue_brightness(monitor, int(value))

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

    def brightness_up(self):
        self._adjust_brightness(10)

    def brightness_down(self):
        self._adjust_brightness(-10)

    def _adjust_brightness(self, delta):
        if self._monitors:
            self._do_adjust(list(self._monitors), delta, cleanup_after=False)
            return
        enumerate_monitors_async(
            lambda monitors: self.app.schedule_ui(
                lambda: self._do_adjust(
                    monitors, delta, cleanup_after=True)
            )
        )

    def _do_adjust(self, monitors, delta, cleanup_after=False):
        def _worker():
            new_values = []
            for monitor in monitors:
                try:
                    current = get_brightness(monitor)
                    new_value = max(0, min(100, current + delta))
                    set_brightness(monitor, new_value)
                    new_values.append(new_value)
                except Exception:
                    new_values.append(None)
            if cleanup_after:
                cleanup(monitors)
            self.app.schedule_ui(lambda: self._update_sliders(new_values))

        threading.Thread(target=_worker, daemon=True).start()

    def _update_sliders(self, values):
        self._setting_values = True
        try:
            for i, (slider, label) in enumerate(
                    zip(self._sliders, self._labels)):
                if i < len(values) and values[i] is not None:
                    slider.setValue(values[i])
                    label.setText(str(int(values[i])))
        finally:
            self._setting_values = False

    def _position(self, w, h):
        # 使用 QScreen 的逻辑坐标（Qt move() 也是逻辑像素），避免缩放屏错位。
        screen = QGuiApplication.primaryScreen()
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
        self._auto_timer.start(AUTO_MS)

    def _clear_rows(self):
        self._sliders = []
        self._labels = []
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

        self._clear_rows()
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
