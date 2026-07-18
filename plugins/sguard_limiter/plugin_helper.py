import os
import sys
import time
import threading

from core.ipc_client import IpcClient
from plugins.sguard_limiter.limiter import SGuardLimiterCore


def run_helper(core_pid):
    plugin_name = "sguard_limiter"

    exe_dir = os.path.dirname(os.path.abspath(__file__ if not getattr(sys, "frozen", False) else sys.executable))
    log_path = os.path.join(exe_dir, "..", "..", "mytoolbox.log")
    import logging
    logging.basicConfig(
        filename=os.path.abspath(log_path),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    log = logging.getLogger("helper")

    log.info(f"Helper 启动: {plugin_name}, core_pid={core_pid}")

    client = IpcClient(plugin_name, core_pid)
    if not client.connect(timeout=10):
        log.error("无法连接到 Core 进程")
        sys.exit(1)

    config = {"cpu_percent": 5, "monitor_interval": 3.0, "reapply_interval": 30.0}

    limiter = SGuardLimiterCore(config)

    def on_status(status):
        client.send_status(status)

    limiter.set_status_callback(on_status)

    def on_msg(msg):
        if msg.get("action") == "stop":
            log.info("收到停止指令")
            limiter.stop()
            os._exit(0)

    client.start_reader(on_msg)

    def heartbeat_loop():
        while True:
            time.sleep(5)
            client.send_heartbeat()

    threading.Thread(target=heartbeat_loop, daemon=True).start()

    limiter.start()
    log.info("Helper 运行中")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        limiter.stop()
        client.close()
