"""屏幕中央的 Toast 提示窗。

窗口不接收鼠标、不接受焦点，只负责画一块半透明圆角背景和一行文字，
到时自动隐藏。``PopupWindow.show`` 可从任意线程调用。
"""

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget


class _ToastWindow(QWidget):
    """无边框、不抢焦点、点击穿透的提示窗口。"""

    WIDTH = 160
    HEIGHT = 80
    CORNER_RADIUS = 16

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(self.WIDTH, self.HEIGHT)

        self._text = ""
        self._strike = False

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def show_text(self, text, duration, strike):
        """显示文字并在 ``duration`` 秒后自动隐藏。

        Args:
            text: 要显示的内容，通常只有 1~3 个字符。
            duration: 显示时长（秒）。
            strike: 是否给文字加删除线（用于表示 NumLock 关闭）。
        """
        self._text = text
        self._strike = strike
        self._center_on_screen()
        self.show()
        self.raise_()
        self.update()
        self._hide_timer.start(max(1, int(duration * 1000)))

    def _center_on_screen(self):
        """把窗口摆到主屏可用区域的正中。

        Note:
            固定使用主屏，鼠标在副屏时提示仍会出现在主屏（审计报告 P2-6）。
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + (area.height() - self.height()) // 2
        self.move(x, y)

    def paintEvent(self, event):
        """绘制圆角背景与居中文字。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(173, 172, 171, 210))
        painter.drawRoundedRect(
            self.rect().adjusted(0, 0, -1, -1),
            self.CORNER_RADIUS,
            self.CORNER_RADIUS,
        )

        # 单字符用大字号，多字符（ABC）缩小一号避免溢出
        pixel_size = 48 if len(self._text) <= 3 else 36
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(pixel_size)
        font.setStrikeOut(self._strike)
        painter.setFont(font)
        painter.setPen(QColor(255, 255, 255, 255))
        painter.drawText(
            self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
        painter.end()


class PopupWindow(QObject):
    """Toast 弹窗控制器。show() 可从任意线程调用。"""

    _show_requested = Signal(str, float, bool)

    def __init__(self):
        super().__init__()
        self._window = _ToastWindow()
        self._show_requested.connect(
            self._on_show, Qt.ConnectionType.QueuedConnection)

    def show(self, text, duration=1, strike=False):
        """请求显示一条提示，线程安全。"""
        self._show_requested.emit(text, float(duration), bool(strike))

    @Slot(str, float, bool)
    def _on_show(self, text, duration, strike):
        """槽函数，保证在 GUI 线程上真正操作窗口。"""
        self._window.show_text(text, duration, strike)
