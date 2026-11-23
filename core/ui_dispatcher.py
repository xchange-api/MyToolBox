"""跨线程 UI 调度器。

后台线程（IPC 读取、DDC 枚举等）不得直接操作 Qt 控件，统一通过
``UiDispatcher.post`` 把回调投递回 GUI 线程执行。
"""

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
        """排队执行 ``callback``，可从任意线程调用。

        调度器已停止（应用正在退出）时静默丢弃。
        """
        if self._stopped:
            return
        self._invoke.emit(callback)

    def stop(self):
        """停止接收新回调，已在队列中的回调也会被跳过。"""
        self._stopped = True

    @Slot(object)
    def _run(self, callback):
        """在 GUI 线程执行回调，异常只记录不向外抛。"""
        if self._stopped:
            return
        try:
            callback()
        except Exception:
            get_logger().exception("UI 回调执行失败")
