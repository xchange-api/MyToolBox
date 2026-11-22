from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget


class _ToastWindow(QWidget):
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
        self._text = text
        self._strike = strike
        self._center_on_screen()
        self.show()
        self.raise_()
        self.update()
        self._hide_timer.start(max(1, int(duration * 1000)))

    def _center_on_screen(self):
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = area.x() + (area.width() - self.width()) // 2
        y = area.y() + (area.height() - self.height()) // 2
        self.move(x, y)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(173, 172, 171, 210))
        painter.drawRoundedRect(
            self.rect().adjusted(0, 0, -1, -1),
            self.CORNER_RADIUS,
            self.CORNER_RADIUS,
        )

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
        self._show_requested.emit(text, float(duration), bool(strike))

    @Slot(str, float, bool)
    def _on_show(self, text, duration, strike):
        self._window.show_text(text, duration, strike)
