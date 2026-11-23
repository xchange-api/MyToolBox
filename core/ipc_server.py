"""命名管道 IPC 服务端（Core 侧）。

Core 进程为每个需要提权的插件创建一条命名管道，等待 ``--helper``
子进程以管理员身份启动后连上来。消息格式为 JSON-line：每条消息是
一行 UTF-8 JSON，以 ``\\n`` 结尾。

协议约定见 ``openspec/changes/unify-toolbox/specs/ipc-transport/spec.md``。
"""

import json
import os
import threading

import pywintypes
import win32api
import win32file
import win32pipe
import win32security

from core.logger import get_logger

PIPE_PREFIX = r"\\.\pipe\MyToolBox"
# 以下三个常量目前未被任何代码引用：心跳超时检测尚未实现，
# 参见审计报告 P1-6。
HEARTBEAT_TIMEOUT = 15.0
HEARTBEAT_INTERVAL = 5.0
CONNECT_TIMEOUT = 10.0
PIPE_BUFFER = 4096


def make_pipe_name(plugin_name, core_pid=None):
    """拼出带 Core PID 的管道全名，避免同机多实例互相冲突。

    Args:
        plugin_name: 插件名，作为管道名的一段。
        core_pid: Core 进程 PID，缺省取当前进程。

    Returns:
        形如 ``\\\\.\\pipe\\MyToolBox\\sguard_limiter_1234`` 的完整路径。
    """
    pid = core_pid or os.getpid()
    return f"{PIPE_PREFIX}\\{plugin_name}_{pid}"


def _create_pipe_security():
    """构造只允许当前用户读写的安全描述符。

    Returns:
        已设置 DACL 的 ``SECURITY_ATTRIBUTES``，用于 ``CreateNamedPipe``。
    """
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
    """管理若干条命名管道，每条对应一个提权插件。"""

    def __init__(self):
        self._log = get_logger()
        self._pipes = {}
        self._listener_threads = {}
        self._running = False

    def listen(self, plugin_name):
        """为指定插件启动一条监听线程。

        Note:
            这里在调用方线程同步执行 ``_cleanup_pipe``，若该管道正被
            另一线程阻塞在 ``ConnectNamedPipe`` 上，``DisconnectNamedPipe``
            会与之互锁导致调用线程永久挂起（审计报告 P0-1）。
        """
        self._cleanup_pipe(plugin_name)
        pipe_name = make_pipe_name(plugin_name)
        self._log.info(f"IPC 监听: {pipe_name}")
        t = threading.Thread(target=self._listen_loop, args=(plugin_name, pipe_name), daemon=True)
        self._listener_threads[plugin_name] = t
        t.start()

    def _listen_loop(self, plugin_name, pipe_name):
        """创建管道实例、等待客户端连接，然后进入读取循环。

        Note:
            ``self._running`` 是实例级共享标志，多插件同时监听时会被
            互相覆盖。
        """
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
        """循环读取字节流，按换行切分出完整消息。

        读取可能返回半条消息（消息模式下一次 ReadFile 也可能只拿到部分
        数据），因此用 ``buf`` 累积后再切分。
        """
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
                    # ERROR_MORE_DATA：本条消息还没读完，继续累积
                    if data:
                        buf += data
                    continue
                else:
                    break
            except pywintypes.error:
                break
        self._on_disconnect(plugin_name)

    def _on_message(self, plugin_name, raw):
        """把一行字节解析成 JSON 后交给回调，解析失败静默丢弃。"""
        try:
            msg = json.loads(raw)
            if self._on_message_callback:
                self._on_message_callback(plugin_name, msg)
        except json.JSONDecodeError:
            pass

    def _on_disconnect(self, plugin_name):
        """客户端断开时的钩子，当前只记录日志。

        Note:
            helper 异常退出后这里不会清理应用层的运行状态，菜单会继续
            显示「运行中」（审计报告 P1-6）。
        """
        self._log.info(f"IPC 客户端断开: {plugin_name}")

    def send(self, plugin_name, msg):
        """向指定插件的 helper 发送一条 JSON 消息。

        管道不存在（helper 尚未连上）时静默返回，调用方无法得知消息
        被丢弃。
        """
        handle = self._pipes.get(plugin_name)
        if not handle:
            return
        try:
            data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
            win32file.WriteFile(handle, data)
        except Exception as e:
            self._log.error(f"IPC 发送失败 ({plugin_name}): {e}")

    def stop_all(self):
        """停止全部监听并关闭所有管道，用于应用退出。"""
        self._running = False
        for plugin_name, handle in list(self._pipes.items()):
            self._cleanup_pipe(plugin_name)

    def _cleanup_pipe(self, plugin_name):
        """断开并关闭指定插件的管道句柄（幂等）。"""
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
        """注册消息回调，签名为 ``cb(plugin_name, msg_dict)``。"""
        self._on_message_callback = cb
