# MyToolBox 代码审计报告

> 审计时间：2026-11-24
> 代码版本：`b02586b`（工作区干净）
> 审计范围：`main.py` + `core/`(9) + `plugins/`(15) + `tools/`(1)，共 26 个 Python 文件、约 3800 行
> 审计方式：全量通读 + 静态分析 + **4 项运行时实测复现**
> 说明：上一版报告 `audit_report-tkinter-2026-07-18.md` 针对 tkinter/pystray 架构，代码已迁移到 PySide6，该报告整体作废，仅保留作历史对照。

---

## 结论摘要

架构本身是合理的：插件化、权限隔离（Core 普通权限 + `--helper` 提权子进程）、UI 调度统一走 `UiDispatcher`、异步枚举配 generation 令牌防陈旧回调，这些设计比旧版明显更好。

但**当前构建不建议直接分发**，原因是有两个 P0 级缺陷会让生产环境直接不可用：

| 级别 | 数量 | 代表问题 |
|------|------|----------|
| **P0 阻断** | 2 | IPC 重复监听导致 GUI 主线程死锁（已实测复现）；日志路径在 Program Files 下启动即崩溃 |
| **P1 功能错误** | 13 | 配置双源漂移；UAC 拒绝后状态不回滚；helper 成孤儿进程；DDC 句柄泄漏等 |
| **P2 健壮性** | 12 | 无崩溃日志；心跳未实现；与 openspec 规格多处不符 |

另外，`openspec/` 里写明的 8 条验收要求中有 6 条**未实现或实现不符**（见第 6 节），说明设计与代码已经脱节。

---

## 修复记录

| 日期 | 内容 | 对应条目 |
|------|------|----------|
| 2026-11-24 | 定时器清理统一收口到 `_stop_timers()`；`_close()` / `destroy()` 改为幂等，销毁后重复调用不再抛 `RuntimeError` | 旧报告 2.3 在 PySide6 架构下的残留 |
| 2026-11-24 | 新增 `show_with_adjust()`：`Ctrl+Alt+↑/↓` 弹出亮度滑块并调节；已显示时复用 UI、只重置自动关闭定时器，连按不重建控件 | 新增功能 |
| 2026-11-24 | `_build_slider_row` 改用枚举时的缓存亮度，去掉一次同步 DDC 读 | P1-9 部分缓解 |

---

## 1. P0：必须立刻修的问题

### 🔴 P0-1 IPC 重复监听会让 GUI 主线程永久死锁（已实测复现）

**位置：** `core/ipc_server.py:50-56`（`listen`）→ `132-142`（`_cleanup_pipe`）

```python
def listen(self, plugin_name):
    self._cleanup_pipe(plugin_name)      # ← 在主线程同步执行
    ...

def _cleanup_pipe(self, plugin_name):
    handle = self._pipes.pop(plugin_name, None)
    if handle:
        win32pipe.DisconnectNamedPipe(handle)   # ← line 136，卡死在这里
```

**根因：** 上一个监听线程正阻塞在 `ConnectNamedPipe(handle)`（第 70 行），此时主线程对**同一个 pipe 实例**调用 `DisconnectNamedPipe`，内核里两个操作互相等待，调用方永久挂起。

**实测证据**（`faulthandler` 堆栈）：

```
Thread 0x00003060  ← 后台监听线程
  File "core/ipc_server.py", line 70 in _listen_loop     # ConnectNamedPipe 阻塞
Thread 0x00000578  ← GUI 主线程
  File "core/ipc_server.py", line 136 in _cleanup_pipe   # DisconnectNamedPipe 阻塞
  File "core/ipc_server.py", line 51 in listen
Timeout (0:00:08)!
```

**触发路径（完全正常的使用操作）：**
1. 托盘点「SGuard 限速」启用 → `_start_helper_plugin` → `ipc.listen("sguard_limiter")`
2. 再点一次关闭 → `_stop_helper_plugin`（**不清理 pipe**），pipe 仍挂着
3. 再点一次启用 → `ipc.listen` 命中已存在的 pipe → **整个托盘界面冻结，只能杀进程**

或者 UAC 取消后重试也会走到同一路径。

**修复建议：** 核心原则是「谁创建的 handle 谁负责关闭」，不要在 A 线程关 B 线程正在用的 handle。

```python
class _PipeSession:
    """一次监听 = 一个独立会话，自带停止信号，避免跨线程操作同一 handle"""
    def __init__(self, plugin_name, pipe_name, server):
        self.plugin_name = plugin_name
        self.pipe_name = pipe_name
        self._server = server
        self._stopped = threading.Event()
        self._handle = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stopped.set()
        if self._handle:
            try:
                win32file.CancelIoEx(self._handle, None)   # 打断挂起的 ConnectNamedPipe
            except Exception:
                pass

    def _run(self):
        try:
            sa = _create_pipe_security()
            self._handle = win32pipe.CreateNamedPipe(...)
            if self._stopped.is_set():
                return
            self._server._register(self.plugin_name, self._handle)
            win32pipe.ConnectNamedPipe(self._handle, None)
            ...
        finally:
            self._close()          # 只有本线程关自己的 handle

    def _close(self):
        if self._handle:
            try:
                win32pipe.DisconnectNamedPipe(self._handle)
            except Exception:
                pass
            try:
                win32file.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
```

`listen()` 里改成 `old = self._sessions.pop(name, None); if old: old.stop()` 然后新建 session，全程不做任何可能阻塞的 Win32 调用。

---

### 🔴 P0-2 日志路径写在 exe 同目录，装在 Program Files 下启动即崩溃

**位置：** `core/__init__.py:9-12`（`get_app_dir`）、`core/logger.py:8, 36`

```python
# core/__init__.py
def get_app_dir():
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))   # ← exe 所在目录
    ...

# core/logger.py
_LOG_FILE = os.path.join(get_app_dir(), "mytoolbox.log")
file_handler = RotatingFileHandler(_LOG_FILE, ...)   # ← 权限不足直接抛 PermissionError
```

**影响：** 用户把 exe 装到 `C:\Program Files\MyToolBox\`（最常见的安装位置）后：
- `initialize()` 里 `setup_logger()` 抛 `PermissionError`
- `main._run_core` 的 `except Exception: app.shutdown(); raise` 只是重抛
- `MyToolBox.spec` 是 `console=False`，**用户看不到任何提示，双击图标毫无反应**

同理 `config.json`（`core/config_manager.py:29`）也写在同一目录，`save()` 时会再次失败。

这个问题在 7 月的报告里已提过（1.2 / 3.2），本次仍未修。

**修复建议：** 运行时可写数据一律放 `%LOCALAPPDATA%`，程序目录只读。

```python
# core/__init__.py
def get_data_dir():
    """运行时可写目录：%LOCALAPPDATA%\MyToolBox"""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "MyToolBox")
    os.makedirs(path, exist_ok=True)
    return path
```

同时给 `setup_logger` 加降级：文件 handler 失败时只留 stdout，不要连带把整个应用打死。

```python
try:
    file_handler = RotatingFileHandler(_LOG_FILE, ...)
    logger.addHandler(file_handler)
except OSError as e:
    logger.warning(f"无法写入日志文件 {_LOG_FILE}: {e}，仅输出到 stderr")
```

---

## 2. P1：功能性错误

### P1-1 配置默认值有两个来源，已经漂移到完全对不上（实测）

**位置：** `core/config_manager.py:6-27`（`_CONFIG`）vs `plugins/*/plugin.py` 的 `default_config`

`_merge_defaults` 对内置插件用的是 `_CONFIG["plugins"]` 里的硬编码，**完全忽略插件自己声明的 `default_config`**：

```python
for name, defaults in _CONFIG["plugins"].items():
    user = self._data["plugins"].get(name, {})
    merged = defaults.copy()
    merged.update(user)
    self._data["plugins"][name] = merged     # ← 内置插件以 _CONFIG 为准
```

实测结果：

```
[2] sguard 合并结果 = {'enabled': False, 'interval': 1.0}
```

而 `SGuardLimiterPlugin.default_config` 是：

```python
{"cpu_percent": 5, "monitor_interval": 3.0, "reapply_interval": 30.0}
```

**`_CONFIG` 里的 `interval` 是旧版遗留键，跟插件真正读取的 `monitor_interval` 毫无关系，`cpu_percent` 在配置文件里根本不存在。** 用户打开 config.json 既看不到可调项，也不知道该写什么键名，只能去翻源码。

**修复建议：** 删掉 `_CONFIG["plugins"]` 整段，只保留 `general` 默认值，插件配置全部来自 `plugin.default_config`（这才是单一事实来源，也符合 `plugin-system` 规格）。

```python
_CONFIG = {"general": {"autostart": False, "log_level": "info"}}

def _merge_defaults(self, plugins):
    ...
    for name, plugin in plugins.items():
        user = self._data["plugins"].get(name, {})
        merged = dict(plugin.default_config)
        merged.update(user)
        merged.setdefault("enabled", False)
        self._data["plugins"][name] = merged
```

### P1-2 配置文件损坏时静默丢配置、不修文件、不记日志（实测）

**位置：** `core/config_manager.py:38-47`

```python
try:
    with open(_CONFIG_PATH, ...) as f:
        raw = json.load(f)
    self._data = raw
except (json.JSONDecodeError, Exception):     # ← JSONDecodeError 是 Exception 子类，纯冗余
    self._data = {}                            # ← 用户配置全丢，且不记日志
self._merge_defaults(plugins)
self._ensure_file()                            # ← 文件已存在，所以坏文件原样留着
```

实测（config.json 里写了 `log_level: "debug"` 再故意写坏）：

```
[1] 损坏配置 -> 载入结果 log_level = info     ← 用户设置被静默丢弃
[1] 磁盘坏文件仍保留，读取直接抛 JSONDecodeError
[1] 无任何 ERROR/WARNING 日志
```

**后果：** 用户每次启动都在用默认配置，而磁盘上的坏文件永远不会被修复，用户也永远不知道发生了什么。`config-management` 规格明确要求 "SHALL log the error"，未实现。

**修复建议：**

```python
except json.JSONDecodeError as e:
    self._log.error(f"config.json 解析失败: {e}")
    try:
        shutil.copy2(_CONFIG_PATH, _CONFIG_PATH + ".bak")   # 保留现场
    except OSError:
        pass
    self._data = {}
    ...
    self._data = {}   # 之后强制重写，别让坏文件留着
    self.save()       # 用默认值重建
```

`save()` 也应改成原子写（`tempfile` + `os.replace`），避免写一半断电把配置写坏。

### P1-3 sguard_limiter 完全不读用户配置

**位置：** `plugins/sguard_limiter/plugin_helper.py:17`

```python
config = {"cpu_percent": 5, "monitor_interval": 3.0, "reapply_interval": 30.0}
```

Helper 进程硬编码配置，`app._start_helper_plugin` 也不向下传任何配置。用户在 config.json 里把 `cpu_percent` 从 5 改成 20 **完全无效**。

**修复建议：** 提权启动时把配置序列化传过去，或走 IPC 下发（`design.md` 里本来就设计了 `{"action":"update_config"}`）：

```python
# app._start_helper_plugin
cfg = json.dumps(self.config.get_plugin_config(name), ensure_ascii=False)
args = f'--helper {name} --core-pid {os.getpid()} --config "{cfg.replace(chr(34), chr(92)+chr(34))}"'
```

命令行转义容易出错，更稳的做法是 helper 连上 pipe 后先发 `{"type":"get_config"}`，Core 回配置。

### P1-4 UAC 取消后状态不回滚，需要点两次才能重试

**位置：** `core/app.py:148-159`

```python
ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 0)
self._active_helper_plugins.add(name)                    # ← 无论成功失败都标记为已启用
self._helper_statuses[name] = {"running": False, "pids": []}
```

`ShellExecuteW` 返回值 `< 32` 表示失败（用户点「否」时返回 5 = `SE_ERR_ACCESSDENIED`），代码没检查。结果：

- 用户点「否」→ `_active_helper_plugins` 里已经有这个插件
- 托盘菜单显示为**已勾选**，但实际什么都没跑
- 再点一次 → `toggle_plugin` 走 `_stop_plugin` 分支（只清状态）→ 什么都没发生
- 需要点第三次才会重新弹 UAC

`uac-elevation` 规格要求 10 秒超时回滚，也未实现。

**修复建议：**

```python
ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, args, None, 0)
if ret <= 32:
    self._log.error(f"提权启动失败 (ShellExecuteW={ret})")
    self.ipc.stop_listener(name)
    self._update_menu()
    return

self._active_helper_plugins.add(name)
self._helper_statuses[name] = {"running": False, "pids": []}
QTimer.singleShot(10_000, lambda: self._check_helper_alive(name))
```

配合 P1-6 的连接超时判断即可完整满足规格。

### P1-5 关闭管理员插件时 helper 可能变成孤儿进程（安全影响）

**位置：** `core/app.py:161-164`、`core/ipc_server.py:117-125`

```python
def _stop_helper_plugin(self, name):
    self.ipc.send(name, {"action": "stop"})
    self._active_helper_plugins.discard(name)
```

```python
def send(self, plugin_name, msg):
    handle = self._pipes.get(plugin_name)
    if not handle:
        return          # ← helper 还没连上就静默丢弃
```

如果 helper 还在 UAC 弹窗 / 启动过程中（pipe 未连接），`send` 直接 return，**一个管理员权限的进程从此不受任何管控地留在后台运行**。即使用户在托盘上已经"关闭"了它。

`uac-elevation` 规格要求：等待 3 秒后按 PID 强制终止。代码里 `_helper_pids` 已经记录了 PID，但从未使用。

**修复建议：**

```python
def _stop_helper_plugin(self, name):
    self.ipc.send(name, {"type": "command", "action": "stop"})
    pid = self._helper_pids.get(name)
    if pid:
        QTimer.singleShot(3000, lambda: self._force_kill_if_alive(name, pid))
    self._active_helper_plugins.discard(name)

def _force_kill_if_alive(self, name, pid):
    if name in self._active_helper_plugins:
        return
    try:
        h = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
        win32api.TerminateProcess(h, 0)
        self._log.warning(f"强制终止残留 helper PID={pid}")
    except Exception:
        pass   # 已退出
```

### P1-6 心跳机制只有常量，没有实现

**位置：** `core/ipc_server.py:14-15`

```python
HEARTBEAT_TIMEOUT = 15.0
HEARTBEAT_INTERVAL = 5.0
```

`grep` 结果：这两个常量**除了定义之外没有任何引用**。helper 侧 `plugin_helper.py:33-38` 确实每 5 秒发心跳，但 Core 侧从不消费。

**后果：** helper 崩溃 / 被任务管理器杀掉后：
- `_on_disconnect` 只写一行日志（`ipc_server.py:114-115`）
- `_active_plugins` 从不清理 → 菜单永久显示「运行中」
- 再点 toggle 走 stop 分支 → 用户必须先点一次"关闭"再点"开启"

**修复建议：** 在 `_on_message` 里刷新 `self._last_seen[plugin_name] = time.monotonic()`，Core 起一个 `QTimer(5s)` 巡检，超时则回调 `app.on_helper_lost(name)` 清状态并 `schedule_ui(self._update_menu)`。

### P1-7 DDC 物理显示器句柄泄漏

**位置：** `plugins/brightness_controller/ddcci.py:311-327`

```python
for i in range(count.value):
    pm = arr[i]
    ...
    if _GetMonitorBrightness(pm.hPhysicalMonitor, ...):
        result.append(MonitorInfo(...))
    # ← else 分支缺失：不支持亮度控制的显示器，句柄永不释放
```

`_GetPhysicalMonitorsFromHMONITOR` 返回的每个句柄都必须由调用方 `_DestroyPhysicalMonitor`。这里只对「读取亮度成功」的显示器保留了句柄（交给 `cleanup` 释放），失败的那些**直接泄漏**。

典型触发场景：接了投影仪、虚拟显示器、采集卡的设备，或者显示器处于待机状态时读亮度失败。每次打开浮窗泄漏一次，常驻 tray 用一整天会累积。

**修复建议：**

```python
if _GetMonitorBrightness(pm.hPhysicalMonitor, byref(min_v), byref(cur_v), byref(max_v)):
    result.append(MonitorInfo(...))
else:
    _DestroyPhysicalMonitor(pm.hPhysicalMonitor)    # 不支持就立刻还回去
```

### P1-8 Job Object 句柄泄漏

**位置：** `plugins/sguard_limiter/limiter.py:333-341`

```python
if not self._is_in_job(h):
    job = self._create_job_with_limits()
    if job:
        assign_ok = kernel32.AssignProcessToJobObject(job, h)
        if assign_ok:
            self._job_map[pid] = job
            self._active_pids.add(pid)
            continue
        # ← assign 失败时 job 没关，泄漏
self._apply_process_limits(h)
```

`AssignProcessToJobObject` 在目标进程已属于其他 job（Windows 8 之前）或权限不足时会失败。失败分支没有 `CloseHandle(job)`。

**修复建议：** 加 `else: kernel32.CloseHandle(job)`。

### P1-9 打开浮窗/切换显示器时 GUI 线程同步做 DDC 读

**位置：** `plugins/brightness_controller/flyout.py:551`

> 2026-11-24 更新：`_build_slider_row` 已改为读取枚举时缓存的 `MonitorInfo.current`，少一次 DDC 读；`_refresh_selection`（切换显示器时）仍是同步读，条目未完全关闭。

```python
current = get_brightness(self._selected)      # _refresh_selection，GUI 线程
```

`get_brightness` 是同步 I2C 读，正常 10~50ms，显示器休眠/线材差时可能几百毫秒。7 月的报告（1.1）只把**枚举**异步化了，**读取**仍在 GUI 线程。

**现象：** 点托盘图标后浮窗延迟出现；在多显示器列表里切换时界面顿一下。

**修复建议：** 复用已有的 `enumerate_monitors_async` 模式，加一个 `get_brightness_async(monitor, callback)`；UI 先用上次缓存值渲染，回调再更新。

### P1-10 亮度写入无串行化，可能乱序

**位置：** `plugins/brightness_controller/ddcci.py:415-417`

```python
def set_brightness_async(monitor, value):
    threading.Thread(target=set_brightness, args=(monitor, value), daemon=True).start()
```

每调一次起一个新线程。拖动滑块时（40ms 防抖）会连续起线程，相邻两次 `SetMonitorBrightness` 可能并发进入 DDC 通道 —— 显示器 I2C 不具备并发安全性，结果是设置失败、画面闪烁，或者**最终亮度与滑块位置不一致**（后发起的线程先完成）。

**修复建议：** 用一个专用的串行执行器，每个显示器一个「最新值」槽位：

```python
class _DdcWriter:
    def __init__(self):
        self._lock = threading.Lock()
        self._latest = {}          # id(monitor) -> (monitor, value)
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._wake = threading.Event()
        self._worker.start()

    def submit(self, monitor, value):
        with self._lock:
            self._latest[id(monitor)] = (monitor, value)
        self._wake.set()

    def _loop(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            with self._lock:
                items = list(self._latest.values())
                self._latest.clear()
            for monitor, value in items:   # 同一显示器只会写最后那个值
                set_brightness(monitor, value)
```

### P1-11 后台线程持有显示器句柄，与 `_close()` 存在 use-after-free

**位置：** `flyout.py:679-695`（`_do_adjust` 内部线程）vs `811-835`（`_close`）

`_do_adjust` 的后台线程遍历 `targets`（引用 `self._monitors` 里的 `MonitorInfo`）调用 `get_brightness` / `set_brightness`；与此同时 GUI 线程可能执行 `_close()` → 后台线程 `cleanup(monitors)` → `_DestroyPhysicalMonitor`。**两处操作同一批句柄，没有同步**。

**后果：** 按热键调亮度时刚好浮窗自动关闭（2.5s 超时），有一定概率向已销毁的 HDC 句柄发指令，产生无效调用或访问冲突。

**修复建议：** 引入引用计数，或者更简单——**让句柄只属于一个所有者**：`_do_adjust` 不要复用 `self._monitors`，而是在自己的工作线程里自行 `enumerate_monitors()` 拿独立句柄，用完自己释放（`_adjust_enumerated` 分支已经是这么做的，`_do_adjust` 的复用路径应当统一改成同样方式）。

### P1-12 全局键盘钩子的回调在 GUI 线程执行，且内部有阻塞调用

**位置：** `plugins/input_state_notifier/notifier.py:309-317`（`start`）、`263-276`（`_arm_gesture`）

```python
# start() 由 on_start 调用，而 on_start 在 Qt GUI 线程
self._hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_cb, hmod, 0)
```

`WH_KEYBOARD_LL` 不传线程 ID 时，回调投递到**安装线程的消息队列**——也就是 Qt 的 GUI 线程。而回调里有：

```python
def _arm_gesture(self):
    ...
    self._gesture_focus = self._get_focus_window(hwnd)          # GetWindowThreadProcessId + GetGUIThreadInfo
    self._gesture_prev = self._get_ime_is_chinese(
        hwnd, timeout_ms=IME_ARM_TIMEOUT_MS)                    # SendMessageTimeoutW，最多 30ms
```

**两个后果：**
1. 每次按 Shift / Ctrl+Space 都可能阻塞 GUI 线程 30~60ms，浮窗动画、托盘菜单都会掉帧
2. 一旦 IME 宿主进程卡住，回调累计耗时超过系统的 `LowLevelHooksTimeout`（默认 300ms），**Windows 会静默摘掉这个钩子**——用户表现为"用一段时间后 CapsLock 提示就不弹了"，且没有任何日志

**修复建议：** 把钩子放到专用线程，自己跑一个 `GetMessage` 循环（这也是微软文档推荐的做法）：

```python
def _hook_thread(self):
    self._hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_cb, hmod, 0)
    msg = wintypes.MSG()
    while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        pass
    user32.UnhookWindowsHookEx(self._hook_id)
```

同时 `_arm_gesture` 里的 IME 查询移到延迟线程（`_trigger_ime_check` 里已有线程，把 prev 状态也搬过去即可）。

### P1-13 配置热更新链路是死的

**位置：** 全项目

`grep` 结果：`on_config_change` 在 `plugins/base.py` 有定义、3 个插件有实现，但**没有任何调用方**；`ConfigManager.save()` 只在 `_ensure_file()`（文件不存在时）被调用。

**后果：** `BrightnessPlugin.on_config_change` 里那段"运行中开关 `power_off_on_shutdown`"的逻辑（`plugin.py:170-182`，带详细注释）永远不会执行。用户在 config.json 里改 `power_off_on_shutdown` 后必须重启进程才生效，而托盘菜单也没有对应开关。

**修复建议：** 二选一
- 托盘菜单加一个「关机时软关显示器」可勾选项 → `config.raw["plugins"][name][key] = x` → `save()` → `plugin.on_config_change(cfg)`
- 或者删掉这些死代码，在 README 里明确"配置改完需重启"

---

## 3. P2：健壮性与规范

| # | 问题 | 位置 | 说明 |
|---|------|------|------|
| P2-1 | 无崩溃日志 | `MyToolBox.spec:96` `console=False` + `main.py` 无 `excepthook` | 界面线程外的崩溃完全不可见；建议装 `sys.excepthook` + `threading.excepthook` 写 `crash.log` |
| P2-2 | `ShutdownHook` 在 WndProc 里 sleep | `shutdown_hook.py:237-245` | `time.sleep(0.1)` × 20 = 最多阻塞 2 秒，且期间调用 `win32evtlog`。QES 阶段超时会让 Windows 弹"此应用阻止关机"；`_install_wndproc` 也没有 `if self._installed: return` 保护，重复调用会把自己的回调叠成递归 |
| P2-3 | 托盘菜单未分类 | `tray_manager.py:65-92` | `unified-tray` 规格要求按 `category` 分组 + 分隔符，当前是平铺；`_helper_statuses` 拿到了 `pids` 但菜单不显示 |
| P2-4 | IPC 协议与规格不一致 | `app.py:162` vs `ipc-transport/spec.md` | 规格要求 `{"type":"command","action":"stop"}`，实现发 `{"action":"stop"}`；规格要求 `handshake_ack`，未实现 |
| P2-5 | 无 DDC 写并发保护 | 见 P1-10 | `_wake_screen`（后台线程）与 `shutdown_hook`（WndProc）可能同时 `set_all_power` |
| P2-6 | Toast 固定主屏 | `popup.py:41` | 用了 `primaryScreen()`，鼠标在副屏时提示也弹在副屏之外 |
| P2-7 | 死代码 | `ddcci.py:141` `_enum_list`；`base.py:1-7` `TrayMenuItem` 与 `get_menu_items`（全部返回 `[]`）；`sguard_limiter/plugin.py:20` `set_app`；`ipc_server.py:14-15` 心跳常量 | 建议清理，`TrayMenuItem` 是 pystray 时代残留 |
| P2-8 | 硬编码常量 | `flyout.py:42` `AUTO_MS=2500`；`limiter.py:80` `TARGET_EXE`；`limiter.py:266` 亲和性掩码 | 至少 `AUTO_MS` 应该可配 |
| P2-9 | `stop()` 后 `start()` 不重读键盘状态 | `notifier.py:309-326` | `_caps_on` / `_prev_caps` 保留旧值；若插件关闭期间状态被外部改变，重启后第一次提示方向会反 |
| P2-10 | 多余配置段不清理 | `config_manager.py:63-68` | 卸载插件后 `config.json` 里的段永远留着（实测 `ghost_plugin` 保留） |
| P2-11 | `IpcClient` 关闭竞态 | `ipc_client.py:54` vs `94-101` | `close()` 把 `_handle` 置 `None`，读线程 `ReadFile(self._handle)` 会抛 `TypeError`（只 catch 了 `pywintypes.error`） |
| P2-12 | 构建脚本缺参数 | `build.ps1:7` | `pyinstaller MyToolBox.spec` 没带 `--clean --noconfirm`，改 spec 后可能用旧缓存 |

---

## 4. 值得肯定的部分

- **IPC 管道 ACL 做了用户隔离**（`ipc_server.py:25-40`）：只给当前用户 SID 授读写，比默认 DACL 好很多
- **异步枚举 + generation 令牌**（`flyout.py:320-334`）：快速连续点击托盘不会串台，陈旧回调会自己 `cleanup`，这个模式写得很干净
- **插件隔离**：`plugin_manager.py` 对 discover / on_load / on_start / on_stop 都包了 try/except，单插件故障不会拖垮宿主
- **`shutdown()` 幂等**（`app.py:88-91`）：`aboutToQuit` 和手动退出都连到这里，有 `_shutting_down` 保护
- **1074 事件用 EventRecordID 基线和时间戳比较**（`shutdown_hook.py:141-145, 278-285`）：避免把上次关机的记录当成本次，这个细节考虑得很周到，比单纯看 `param5` 文字靠谱
- **单实例互斥锁实现正确**（`main.py`）：实测确认 `CreateMutexW` 新建成功时会把 last error 置 0，同名再建才返回 183，配合 `ERROR_ALREADY_EXISTS` 判定是可靠的

---

## 5. 与 openspec 规格的差距

`openspec/changes/unify-toolbox/` 的 tasks.md 把很多项打了 `[x]`，但实际实现缺失。逐条对照：

| 规格要求 | 来源 | 实现状态 |
|----------|------|----------|
| 心跳 15s 超时判定 helper 死亡 | `ipc-transport/spec.md` | ❌ 未实现（常量空转） |
| `handshake_ack` 应答 | `ipc-transport/spec.md` | ❌ 未实现 |
| `{"type":"command","action":"stop"}` | `ipc-transport/spec.md` | ⚠️ 格式不符 |
| UAC 取消后 10s 回滚为 disabled | `uac-elevation/spec.md` | ❌ 未实现 |
| 退出时 3s 后按 PID 终止 helper | `uac-elevation/spec.md` | ❌ 未实现（PID 已记录但没用） |
| 配置解析失败要「记录错误日志」 | `config-management/spec.md` | ❌ 静默吞掉（实测） |
| 插件 `config_schema()` | `plugin-system/spec.md` | ❌ 不存在 |
| 菜单按 `category` 分组 | `unified-tray/spec.md` | ❌ 平铺 |
| 插件配置默认值来自 `default_config` | `plugin-system/spec.md` | ❌ 被 `_CONFIG` 硬编码覆盖（P1-1） |
| 插件导入/生命周期故障隔离 | `plugin-system/spec.md` | ✅ |
| 无 UAC 启动 | `uac-elevation/spec.md` | ✅ |
| 单一托盘图标 | `unified-tray/spec.md` | ✅ |

另外 `tasks.md` 的 6.2 / 6.3 / 6.4（IPC 联调、错误韧性、打包验证）是未勾选状态，说明这三块**从未真正验证过**——本报告 P0-1、P1-4、P1-5 正好都在这个未验证范围内。

---

## 6. 建议修复顺序

**第一批（阻断分发，改动量小）**
1. P0-2 日志/配置目录改 `%LOCALAPPDATA%` + handler 降级 —— 约 20 行
2. P0-1 IPC 改按会话管理 —— 约 60 行
3. P1-7 / P1-8 两处漏掉的 `CloseHandle` / `DestroyPhysicalMonitor` —— 3 行

**第二批（功能正确性）**
4. P1-1 配置单一来源（删 `_CONFIG["plugins"]`）
5. P1-2 损坏配置备份 + 重建 + 日志
6. P1-4 + P1-6 UAC 返回值检查 + 心跳巡检（同一套状态机）
7. P1-5 helper 兜底终止
8. P1-3 helper 读配置

**第三批（体验与稳定性）**
9. P1-12 键盘钩子独立线程
10. P1-9 / P1-10 / P1-11 DDC 访问统一收口到一个串行执行器
11. P1-13 配置热更新接上，或明确删掉
12. P2-1 崩溃日志

**长期**
- 补上 `tests/`：`config_manager` 的合并逻辑、`ipc_server` 的会话生命周期、`monitor_titles` 的重名处理，这三个都是纯逻辑，不需要硬件就能覆盖。当前 3800 行代码**零测试**，上面的 P0/P1 基本都是可以靠单元测试挡住的类型。

---

*审计结束：P0 × 2、P1 × 13、P2 × 12，合计 27 项。其中 P0-1、P1-1、P1-2 有运行时实测证据。*
