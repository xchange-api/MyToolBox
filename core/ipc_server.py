import json
import os
import threading
import time

import pywintypes
import win32pipe
import win32file
import win32event
import win32security
import win32api

from core.logger import get_logger


PIPE_PREFIX = r"\\.\pipe\MyToolBox"
HEARTBEAT_TIMEOUT = 15.0
HEARTBEAT_INTERVAL = 5.0
CONNECT_TIMEOUT = 10.0
PIPE_BUFFER = 4096


def make_pipe_name(plugin_name, core_pid=None):
    pid = core_pid or os.getpid()
    return f"{PIPE_PREFIX}\\{plugin_name}_{pid}"


def _create_pipe_security():
    sd = win32security.SECURITY_ATTRIBUTES()
    sd.bInheritHandle = False
    user_sid = win32security.GetTokenInformation(
        win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY),
        win32security.TokenUser,
    )[0]
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        user_sid,
    )
    sd.SetSecurityDescriptorDacl(1, acl, 0)
    return sd


class IpcServer:
    def __init__(self):
        self._log = get_logger()
        self._pipes = {}
        self._listener_threads = {}
        self._running = False

    def listen(self, plugin_name):
        self._cleanup_pipe(plugin_name)
        pipe_name = make_pipe_name(plugin_name)
        self._log.info(f"IPC 监听: {pipe_name}")
        t = threading.Thread(target=self._listen_loop, args=(plugin_name, pipe_name), daemon=True)
        self._listener_threads[plugin_name] = t
        t.start()

    def _listen_loop(self, plugin_name, pipe_name):
        self._running = True
        try:
            sa = _create_pipe_security()
            handle = win32pipe.CreateNamedPipe(
                pipe_name,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                1, PIPE_BUFFER, PIPE_BUFFER, 0, sa,
            )
            self._pipes[plugin_name] = handle

            connected = win32pipe.ConnectNamedPipe(handle, None)
            if connected == 0:
                self._log.error(f"IPC 连接失败: {plugin_name}")
                return

            self._log.info(f"IPC 客户端已连接: {plugin_name}")
            self._pipes[plugin_name] = handle
            self._read_loop(plugin_name, handle)
        except Exception as e:
            self._log.error(f"IPC 监听异常 ({plugin_name}): {e}")
        finally:
            self._cleanup_pipe(plugin_name)

    def _read_loop(self, plugin_name, handle):
        buf = b""
        self._pipes[plugin_name] = handle
        while self._running:
            try:
                hr, data = win32file.ReadFile(handle, PIPE_BUFFER)
                if hr == 0 and data:
                    buf += data
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if line:
                            self._on_message(plugin_name, line)
                elif hr == 234:
                    if data:
                        buf += data
                    continue
                else:
                    break
            except pywintypes.error:
                break
        self._on_disconnect(plugin_name)

    def _on_message(self, plugin_name, raw):
        try:
            msg = json.loads(raw)
            if self._on_message_callback:
                self._on_message_callback(plugin_name, msg)
        except json.JSONDecodeError:
            pass

    def _on_disconnect(self, plugin_name):
        self._log.info(f"IPC 客户端断开: {plugin_name}")

    def send(self, plugin_name, msg):
        handle = self._pipes.get(plugin_name)
        if not handle:
            return
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            win32file.WriteFile(handle, data)
        except Exception as e:
            self._log.error(f"IPC 发送失败 ({plugin_name}): {e}")

    def stop_all(self):
        self._running = False
        for plugin_name, handle in list(self._pipes.items()):
            self._cleanup_pipe(plugin_name)

    def _cleanup_pipe(self, plugin_name):
        handle = self._pipes.pop(plugin_name, None)
        if handle:
            try:
                win32pipe.DisconnectNamedPipe(handle)
            except Exception:
                pass
            try:
                win32file.CloseHandle(handle)
            except Exception:
                pass

    def set_message_callback(self, cb):
        self._on_message_callback = cb
