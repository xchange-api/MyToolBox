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
    def __init__(self, plugin_name, core_pid):
        self._plugin_name = plugin_name
        self._core_pid = core_pid
        self._pipe_name = f"\\\\.\\pipe\\MyToolBox\\{plugin_name}_{core_pid}"
        self._handle = None
        self._running = False
        self._reader_thread = None
        self._log = get_logger()

    def connect(self, timeout=10):
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
        self._running = True
        self._reader_callback = callback
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def _read_loop(self):
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
                    if data:
                        buf += data
                    continue
                else:
                    break
            except pywintypes.error:
                break
        self._running = False

    def _send(self, msg):
        if not self._handle:
            return
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            win32file.WriteFile(self._handle, data)
        except Exception as e:
            self._log.error(f"IPC 发送失败: {e}")

    def send_status(self, status_dict):
        self._send({"type": "status", **status_dict})

    def send_heartbeat(self):
        self._send({"type": "heartbeat"})

    def send_log(self, level, message):
        self._send({"type": "log", "level": level, "message": message})

    def close(self):
        self._running = False
        if self._handle:
            try:
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
