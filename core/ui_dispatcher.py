from PySide6.QtCore import QObject, Qt, Signal, Slot

from core.logger import get_logger


class UiDispatcher(QObject):
    """把任意线程的回调安全地投递到 Qt GUI 线程执行。

    使用 QueuedConnection 保证即使调用方就在 GUI 线程，回调也会在当前
    事件处理结束后再执行，语义与旧版 queue + after 轮询保持一致。
    """

    _invoke = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)
        self._stopped = False

    def post(self, callback):
        if self._stopped:
            return
        self._invoke.emit(callback)

    def stop(self):
        self._stopped = True

    @Slot(object)
    def _run(self, callback):
        if self._stopped:
            return
        try:
            callback()
        except Exception:
            get_logger().exception("UI 回调执行失败")
