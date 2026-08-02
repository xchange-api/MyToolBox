import os
import sys
import threading
import time

from core.ipc_client import IpcClient
from plugins.sguard_limiter.limiter import SGuardLimiterCore


def run_helper(core_pid):
    plugin_name = "sguard_limiter"

    client = IpcClient(plugin_name, core_pid)
    if not client.connect(timeout=10):
        sys.exit(1)

    config = {"cpu_percent": 5, "monitor_interval": 3.0, "reapply_interval": 30.0}

    limiter = SGuardLimiterCore(config)

    def on_status(status):
        client.send_status(status)

    limiter.set_status_callback(on_status)

    def on_msg(msg):
        if msg.get("action") == "stop":
            limiter.stop()
            os._exit(0)

    client.start_reader(on_msg)

    def heartbeat_loop():
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
