"""``--helper`` 模式入口：以管理员身份运行 SGuard 限速逻辑。

Core 通过 UAC 拉起本进程，本进程连回命名管道后启动限速循环，并把
状态与心跳上报给 Core，收到 ``stop`` 指令后退出。
"""

import os
import sys
import threading
import time

from core.ipc_client import IpcClient
from plugins.sguard_limiter.limiter import SGuardLimiterCore


def run_helper(core_pid):
    """作为提权子进程运行，直到收到停止指令。

    Args:
        core_pid: 父 Core 进程 PID，用于拼接命名管道名。

    Note:
        配置在此处硬编码，未从 Core 下发的 config.json 读取，因此用户
        修改 ``cpu_percent`` 等参数不会生效（审计报告 P1-3）。
    """
    plugin_name = "sguard_limiter"

    client = IpcClient(plugin_name, core_pid)
    if not client.connect(timeout=10):
        sys.exit(1)

    config = {"cpu_percent": 5, "monitor_interval": 3.0, "reapply_interval": 30.0}

    limiter = SGuardLimiterCore(config)

    def on_status(status):
        """限速器每次扫描后把状态转给 Core。"""
        client.send_status(status)

    limiter.set_status_callback(on_status)

    def on_msg(msg):
        """收到 Core 指令时的处理；当前只支持 stop。"""
        if msg.get("action") == "stop":
            limiter.stop()
            os._exit(0)

    client.start_reader(on_msg)

    def heartbeat_loop():
        """每 5 秒发一次心跳（Core 侧目前尚未消费）。"""
        while True:
            time.sleep(5)
            client.send_heartbeat()

    threading.Thread(target=heartbeat_loop, daemon=True).start()

    limiter.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        limiter.stop()
        client.close()
