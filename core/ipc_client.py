"""命名管道 IPC 客户端（Helper 侧）。

以管理员身份运行的 ``--helper`` 子进程通过本模块连回 Core，发送
handshake / status / heartbeat 等消息，并接收 Core 下发的指令。
"""

import json
import os
import threading
import time

import pywintypes
import win32file
import win32pipe

from core.logger import get_logger

PIPE_BUFFER = 4096
RECONNECT_DELAY = 0.5


class IpcClient:
    """连接 Core 的命名管道客户端。

    Args:
        plugin_name: 插件名，需与 Core 侧 ``IpcServer.listen`` 一致。
        core_pid: Core 进程 PID，用于拼出管道名。
    """

    def __init__(self, plugin_name, core_pid):
        self._plugin_name = plugin_name
        self._core_pid = core_pid
        self._pipe_name = f"\\\\.\\pipe\\MyToolBox\\{plugin_name}_{core_pid}"
        self._handle = None
        self._running = False
        self._reader_thread = None
        self._log = get_logger()

    def connect(self, timeout=10):
        """重试连接管道，成功后会立即发送 handshake。

        Args:
            timeout: 总重试时长（秒），期间每 ``RECONNECT_DELAY`` 试一次。

        Returns:
            连接并握手成功返回 ``True``，超时返回 ``False``。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._handle = win32file.CreateFile(
                    self._pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None, win32file.OPEN_EXISTING, 0, None,
                )
                mode = win32pipe.PIPE_READMODE_MESSAGE
                win32pipe.SetNamedPipeHandleState(self._handle, mode, None, None)
                self._log.info(f"IPC 已连接: {self._pipe_name}")
                self._send({"type": "handshake", "pid": os.getpid(), "plugin": self._plugin_name})
                return True
            except pywintypes.error:
                time.sleep(RECONNECT_DELAY)
        return False

    def start_reader(self, callback):
        """启动后台读取线程。

        Args:
            callback: 收到一条消息时调用，签名为 ``callback(msg_dict)``。
        """
        self._running = True
        self._reader_callback = callback
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
        """读取字节流并按换行切分消息，退出时自动把 ``_running`` 置假。"""
        buf = b""
        while self._running:
            try:
                hr, data = win32file.ReadFile(self._handle, PIPE_BUFFER)
                if hr == 0 and data:
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line and self._reader_callback:
                            try:
                                msg = json.loads(line)
                                self._reader_callback(msg)
                            except json.JSONDecodeError:
                                pass
                elif hr == 234:
                    # ERROR_MORE_DATA：本条消息未读完
                    if data:
                        buf += data
                    continue
                else:
                    break
            except pywintypes.error:
                break
        self._running = False

    def _send(self, msg):
        """序列化为 JSON-line 后写入管道，未连接时静默丢弃。"""
        if not self._handle:
            return
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            win32file.WriteFile(self._handle, data)
        except Exception as e:
            self._log.error(f"IPC 发送失败: {e}")

    def send_status(self, status_dict):
        """上报运行状态，例如 ``{"running": True, "pids": [...]}``。"""
        self._send({"type": "status", **status_dict})

    def send_heartbeat(self):
        """发送心跳。

        Note:
            Core 侧目前并未消费该消息，心跳超时判定尚未实现。
        """
        self._send({"type": "heartbeat"})

    def send_log(self, level, message):
        """把 helper 侧日志转发给 Core 统一记录。"""
        self._send({"type": "log", "level": level, "message": message})

    def close(self):
        """关闭管道句柄并停止读取循环。"""
        self._running = False
        if self._handle:
            try:
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
