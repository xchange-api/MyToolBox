"""亮度调节浮窗。

布局参考 Windows 10 音量面板：折叠时只有一行标题加一条滑块，点击标题
展开显示器列表。窗口跟随鼠标所在显示器，失焦/超时/点击外部自动关闭。

三个入口：
    * ``show()`` —— 托盘图标点击，重新枚举显示器并重建界面；
    * ``show_with_adjust(delta)`` —— 全局热键，弹出并调节鼠标所在显示器；
    * ``brightness_up()`` / ``brightness_down()`` —— ``show_with_adjust``
      的具名包装。

所有方法都必须在 Qt GUI 线程调用。
"""

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

# 配色
BG = "#3C3C3C"
FG = "#ffffff"
SUN = "#ffc832"
TRACK = "#6b6b6b"
SEP = "#2d2d2d"
HOVER = "#4a4a4a"

# 滑块几何参数
TRACK_H = 1
THUMB_W = 8
THUMB_H = 24
TRACK_INSET = 10

# 布局尺寸
FLYOUT_W = 360
HEADER_H = 42
SLIDER_AREA_H = 54
LIST_ITEM_H = 40
LIST_MAX_VISIBLE = 5
ICON_W = 24
VALUE_W = 46
ROW_H = 42
SLIDER_W = 250
# 无操作多久后自动关闭（毫秒）
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
    """读取 Windows 主题强调色。

    依次尝试资源管理器 Accent 项、StartColor，最后退化到 DWM 的系统色。

    Returns:
        形如 ``#0078d4`` 的颜色字符串；全部失败时返回默认蓝。
    """
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
            # 注册表里是 ABGR 排列
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
            # DWM 返回的是 ARGB
            r = (v >> 16) & 0xFF
            g = (v >> 8) & 0xFF
            b = v & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return "#0078d4"


def _targets_for_monitor(monitors, target):
    """按 HMONITOR 找目标显示器。

    WMI 内置屏可能映射不到具体 HMONITOR（hmonitor=0）；只有它是唯一
    未映射条目时才兜底命中，避免误调到别的屏。
    """
    matches = [m for m in monitors if m.hmonitor == target]
    if matches:
        return matches
    unmapped = [
        m for m in monitors
        if not m.hmonitor and m.source == "wmi"
    ]
    return unmapped if len(unmapped) == 1 else []


def _make_sun_pixmap(size=20):
    """画一个太阳图标，用于图标文件缺失时兜底。

    Args:
        size: 生成位图的边长（像素）。

    Returns:
        透明背景的 ``QPixmap``。
    """
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    cx = cy = size / 2.0
    r = size * 0.26
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(SUN))
    painter.drawEllipse(QPoint(int(cx), int(cy)), int(r), int(r))

    # 8 条光芒，与水平线成 0/45/90/... 度
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
    """与旧版 Canvas 滑块行为对齐的 Qt 滑块。

    完全自绘（不用 QSS），因此轨道、已填充段和滑块位置都由
    ``paintEvent`` 计算；鼠标拖动时按 X 坐标反算 0~100 的值。
    """

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
        """把控件内的 X 坐标换算成 0~100 的亮度值。"""
        left = float(TRACK_INSET)
        right = self.width() - float(TRACK_INSET)
        if right <= left:
            return self.value()
        ratio = (x - left) / (right - left)
        return max(0, min(100, int(round(ratio * 100))))

    def paintEvent(self, event):
        """绘制背景轨道、强调色填充段和圆形滑块。"""
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
        """左键按下即跳转到点击位置，并进入拖动状态。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        """拖动过程中持续跟随鼠标 X 坐标。"""
        if self.isSliderDown():
            self.setValue(self._value_from_x(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        """松开左键时退出拖动状态。"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        """滚轮每格调整 5，方向与系统滚动方向一致。"""
        delta = 5 if event.angleDelta().y() > 0 else -5
        self.setValue(max(0, min(100, self.value() + delta)))
        event.accept()

    def _on_value_changed(self, value):
        """值变化时回调外部命令（设置值时由 ``_setting_values`` 屏蔽）。"""
        if self._cmd:
            self._cmd(value)


class ClickableRow(QWidget):
    """可点击的行，带 Windows 风格的鼠标悬停高亮。

    子控件需要设置 ``WA_TransparentForMouseEvents``，否则点击会被
    QLabel 吃掉而收不到 ``clicked``。
    """

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        """鼠标进入时重绘高亮背景。"""
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        """鼠标离开时重绘去掉高亮。"""
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        """在自身范围内松开左键时发出 ``clicked``。"""
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        """仅在悬停时填充一层浅色背景。"""
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
        # 热键发起显示时携带的调节步长，由 _on_monitors 消费后置空
        self._hotkey_delta = None
        # destroy() 之后 C++ 对象已回收，任何清理动作都必须直接跳过
        self._destroyed = False

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
        """显示浮窗（托盘图标点击入口），会重新枚举显示器。"""
        self._show_impl()

    def show_with_adjust(self, delta):
        """热键入口：弹出浮窗，并对鼠标所在显示器调节 delta。

        窗口已显示时只复用现有 UI 并重置自动关闭定时器，绝不重建控件：
        否则连续按热键会不断 enumerate + 新建控件，每次 _build_ui 都
        再挂一条 _auto_timer，定时器链会越积越多。
        """
        self._show_impl(delta)

    def _show_impl(self, hotkey_delta=None):
        """``show`` 与 ``show_with_adjust`` 的共同实现。

        Args:
            hotkey_delta: 非 ``None`` 表示由热键触发，需要在不抢焦点的
                前提下弹出并调节；枚举完成后在 ``_on_monitors`` 里消费。
        """
        if self.isVisible():
            self.raise_()
            if hotkey_delta is not None:
                # 热键调节时不抢焦点，否则会打断用户正在进行的全屏操作
                self._adjust_brightness(hotkey_delta)
            else:
                self.activateWindow()
            self._schedule()
            return

        # 清掉上一次可能残留的显示器句柄和防抖计时器
        self._close()

        self._generation += 1
        generation = self._generation
        self._show_target = cursor_monitor()
        self._expanded = False
        self._hotkey_delta = hotkey_delta

        enumerate_monitors_async(
            lambda monitors: self.app.schedule_ui(
                lambda: self._on_monitors(generation, monitors)
            )
        )

    def _on_monitors(self, generation, monitors):
        """枚举结果回到主线程后构建界面。

        Args:
            generation: 发起枚举时记录的代次。与当前代次不符说明期间又有
                新的显示请求，直接释放句柄并丢弃本次结果。
            monitors: 后台线程枚举出的显示器列表。
        """
        if generation != self._generation:
            cleanup(monitors)
            return
        self._monitors = monitors
        # 取出并立即消费，避免枚举失败时残留到下一次 show 误触发调节
        hotkey_delta = self._hotkey_delta
        self._hotkey_delta = None
        if not self._monitors:
            return

        self._titles = self._monitor_titles()
        targets = _targets_for_monitor(self._monitors, self._show_target)
        self._selected = targets[0] if targets else self._monitors[0]
        self._build_ui(activate=hotkey_delta is None)
        if hotkey_delta is not None:
            self._adjust_brightness(hotkey_delta)

    def _build_ui(self, activate=True):
        """重建全部子控件并显示窗口。

        Args:
            activate: 是否抢焦点。热键触发时传 ``False``，避免打断用户
                正在进行的全屏操作。
        """
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
        if activate:
            self.activateWindow()
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._schedule()
        # 记录此刻鼠标是否已按下，避免"打开窗口时正按着左键"被误判为
        # 点击了窗口外部而立即关闭
        self._prev_mouse = bool(
            ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
        self._outside_timer.start()

    def _resize_to_content(self):
        """按当前是否展开列表计算窗口尺寸并重新定位。"""
        list_h = 0
        if self._expanded:
            visible = min(len(self._monitors), LIST_MAX_VISIBLE)
            list_h = visible * LIST_ITEM_H
        height = HEADER_H + 1 + list_h + SLIDER_AREA_H
        self.setFixedSize(FLYOUT_W, height)
        self._position(FLYOUT_W, height)

    # ---- 顶部标题栏 ----

    def _build_header(self):
        """构建顶部标题行：左侧显示器名，右侧展开箭头。

        只有一台显示器时不注册点击、并隐藏箭头（没有可切换的目标）。
        """
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
        """刷新标题文字与箭头方向，过长时右侧省略。"""
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
        """返回当前选中显示器的标题，找不到时回退到 "显示器"。"""
        for i, monitor in enumerate(self._monitors):
            if monitor is self._selected and i < len(self._titles):
                return self._titles[i]
        return "显示器"

    # ---- 显示器选择列表 ----

    def _build_selector(self):
        """构建可滚动的显示器选择列表。

        超过 ``LIST_MAX_VISIBLE`` 台时出现竖向滚动条，容器高度固定为
        可见行数，避免窗口被撑高。
        """
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
            detail = " · ".join(
                part for part in (
                    monitor.friendly, monitor.name, monitor.device)
                if part
            )
            row.setToolTip(f"{title}\n{detail}".strip())

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
        """列表项被点击：切换到该显示器并收起列表。

        点当前已选中的显示器只收起列表，不重复读亮度。
        """
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
        """把界面同步到当前选中显示器：重读亮度、更新勾选与标题。

        Note:
            ``get_brightness`` 是同步 DDC 读，会在 GUI 线程上阻塞几十毫秒
            （审计报告 P1-9）。
        """
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
        """点击标题行时展开/收起显示器列表。"""
        if len(self._monitors) <= 1:
            return
        self._set_expanded(not self._expanded)

    def _set_expanded(self, expanded):
        """设置列表展开状态，联动尺寸与自动关闭计时。"""
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
        """构建底部滑块行：太阳图标、滑块、右侧数值。"""
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

        # 枚举时刚读过一次亮度，直接用缓存值，省掉一次同步 DDC 读，
        # 让热键弹出浮窗时更快出现
        current = self._selected.current

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
        """加载托盘图标作为滑块左侧装饰，失败时退回手绘太阳。"""
        pix = QIcon(self._icon_path).pixmap(20, 20)
        if pix.isNull():
            return _make_sun_pixmap(20)
        return pix

    def _on_slider_changed(self, value):
        """滑块值变化：更新数字、重置自动关闭、排队写入硬件。

        ``_setting_values`` 为真时说明是程序在同步界面，不应当作用户操作。
        """
        if self._setting_values or self._selected is None:
            return
        self._value_label.setText(str(int(value)))
        self._schedule()
        self._queue_brightness(self._selected, int(value))

    def _queue_brightness(self, monitor, value):
        """记录待写入的亮度，并用 40ms 防抖合并连续拖动。

        每台显示器一个独立计时器，键为 ``id(monitor)``；``_pending`` 里
        只保留最后一次请求的值。
        """
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
        """防抖计时器到期，把该显示器最后一次请求的亮度写下去。"""
        item = self._pending.pop(key, None)
        if item is None:
            return
        monitor, value = item
        set_brightness_async(monitor, value)

    def _take_pending(self):
        """取出并清空所有待写入项，同时停掉对应防抖计时器。"""
        pending = list(self._pending.values())
        self._pending.clear()
        for timer in self._debounce_timers.values():
            timer.stop()
        return pending

    def _flush_pending_now(self):
        """同步写入所有待写亮度，用于切换显示器或关窗前保证不丢请求。"""
        pending = self._take_pending()
        for monitor, value in pending:
            try:
                set_brightness(monitor, value)
            except Exception:
                pass

    # ---- 热键：只调鼠标所在显示器 ----

    def brightness_up(self):
        """调亮一档（+10）并弹出浮窗。"""
        self.show_with_adjust(10)

    def brightness_down(self):
        """调暗一档（-10）并弹出浮窗。"""
        self.show_with_adjust(-10)

    def _adjust_brightness(self, delta):
        """按 ``delta`` 调节鼠标所在显示器。

        Args:
            delta: 亮度增量，正值调亮、负值调暗。

        已有枚举结果时直接调节；否则先异步枚举，再由
        ``_adjust_enumerated`` 接手。
        """
        # 在按键当下取一次 HMONITOR，避免异步枚举期间鼠标移动改变目标。
        target = cursor_monitor()
        if self._monitors:
            targets = _targets_for_monitor(self._monitors, target)
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
        """枚举完成后执行调节，并在结束时释放这批临时句柄。

        Args:
            monitors: 新枚举出的显示器列表，用完即释放。
            target: 按键当下记录的 ``HMONITOR``。
            delta: 亮度增量。
        """
        targets = _targets_for_monitor(monitors, target)
        if not targets:
            cleanup(monitors)
            return
        self._do_adjust(targets, delta, cleanup_list=monitors)

    def _focus_monitor(self, monitor):
        """把界面选中项切到指定显示器（窗口未显示时不做任何事）。"""
        if not self.isVisible() or monitor is self._selected:
            return
        self._flush_pending_now()
        self._selected = monitor
        self._refresh_selection()

    def _do_adjust(self, targets, delta, cleanup_list=None):
        """在后台线程读出当前亮度、加上增量后写回。

        Args:
            targets: 要调节的 ``MonitorInfo`` 列表（同一台显示器的多个
                物理句柄）。
            delta: 亮度增量。
            cleanup_list: 需要在本轮结束后整体释放的句柄列表；为 ``None``
                表示句柄归窗口所有，不在这里释放。

        Note:
            后台线程直接使用了 ``targets`` 里的句柄，与 GUI 线程的
            ``_close()`` 之间没有同步（审计报告 P1-11）。
        """
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
        """把后台调节结果写回滑块与数值标签。

        Args:
            results: ``(monitor, value)`` 列表，``value`` 为 ``None``
                表示该显示器调节失败。
        """
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

        # DDC 写入回执有延迟，结果回来时可能已接近自动关闭时间，
        # 重置一次让用户能看到最终亮度
        self._schedule()

    # ---- 定位 / 自动关闭 ----

    def _position(self, w, h):
        """把窗口摆到鼠标所在显示器的可用区角落。

        按任务栏位置避让：左侧任务栏贴左下，顶部任务栏贴右上，
        其余情况贴右下。
        """
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
        """每 50ms 轮询一次左键，检测"在窗口外按下"从而关闭浮窗。

        用按下沿（从未按到按下）判断，避免按住左键拖动滑块时被误关。
        """
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
        """重置自动关闭倒计时；列表展开期间不启动计时。"""
        # 展开选择列表时暂停自动关闭，避免用户还没选就消失。
        if self._expanded:
            self._auto_timer.stop()
        else:
            self._auto_timer.start(AUTO_MS)

    # ---- 清理 ----

    def _monitor_label(self, monitor, index):
        """优先用 EDID 型号名，取不到时退回显示器编号。

        WMI 内置屏的 EDID 型号通常是 ``LP140QH2`` 这类面板料号，有就照用；
        读不到 EDID 时才退回“内置显示器”。
        """
        if monitor.friendly:
            return monitor.friendly
        if monitor.source == "wmi":
            return "内置显示器"
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
        """销毁所有子控件、防抖计时器与待写队列，为重建做准备。"""
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

    def _stop_timers(self):
        """停掉本窗口持有的全部定时器。

        _close() 只 hide 窗口，对象仍然存活，定时器若还在跑，timeout
        会继续往已隐藏的窗口上派发。集中收口，避免以后新增定时器时
        漏掉某一条清理路径。

        destroy() 之后 C++ 对象可能已被回收，此时再访问会抛
        RuntimeError，直接忽略即可。
        """
        try:
            self._auto_timer.stop()
            self._outside_timer.stop()
        except RuntimeError:
            return
        for timer in list(self._debounce_timers.values()):
            try:
                timer.stop()
            except RuntimeError:
                pass

    def _close(self):
        """停止定时器、隐藏窗口，并把剩余工作交后台线程收尾。

        幂等：已销毁的实例直接返回；C++ 对象已被回收时静默跳过，
        不再访问 ``_layout`` 等原生成员。
        """
        if self._destroyed:
            return
        try:
            self._generation += 1
            self._stop_timers()
            self._hotkey_delta = None

            pending = self._take_pending()
            monitors = self._monitors
            self._monitors = []
            self._titles = []
            self._selected = None
            self._expanded = False

            self._clear_ui()
            self.hide()
        except RuntimeError:
            # C++ 对象已被回收（重复销毁），剩余句柄交给进程退出回收
            return

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
        """重写 ``QWidget.close``，统一走内部清理流程。"""
        self._close()

    def destroy(self):
        """关闭窗口并安排对象销毁，可安全重复调用。"""
        self._close()
        if self._destroyed:
            return
        self._destroyed = True
        try:
            self.deleteLater()
        except RuntimeError:
            pass

    def keyPressEvent(self, event):
        """按 Esc 关闭浮窗。"""
        if event.key() == Qt.Key.Key_Escape:
            self._close()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):
        """系统要求关闭窗口时执行同样的清理。"""
        self._close()
        event.accept()

    def hideEvent(self, event):
        """窗口隐藏后停止外部点击轮询，省掉无意义的 50ms 定时器。"""
        try:
            self._outside_timer.stop()
        except RuntimeError:
            pass
        super().hideEvent(event)
