# MyToolBox GUI 桌面应用审计报告

> 审计时间：2026-07-18
> 审计范围：全部 23 个 Python 源文件
> 审计维度：8 项

---

## 目录

1. [UI 线程风险](#1-ui-线程风险)
   - ~~[1.1 DDC/CI 枚举与设值阻塞主线程](#-problem-11-ddcci-枚举与设值阻塞主线程)~~
   - [1.2 日志文件 RotatingFileHandler 初始化在主线程](#-problem-12-日志文件-rotatingfilehandler-初始化在主线程)
2. [内存泄漏](#2-内存泄漏)
   - ~~[2.1 PopupWindow GDI 对象未释放](#-problem-21-popupwindow-gdi-对象未释放)~~
    - ~~[2.2 IPC Listener 线程累积](#-problem-22-ipc-listener-线程累积)~~
   - [2.3 FlyoutWindow 定时器链未清理](#-problem-23-flyoutwindow-定时器链未清理)
3. [Windows 平台兼容](#3-windows-平台兼容)
   - ~~[3.1 字体路径硬编码 C: 盘](#-problem-31-字体路径硬编码-c-盘)~~
   - [3.2 日志和配置文件写入 exe 同目录](#-problem-32-日志和配置文件写入-exe-同目录)
   - [3.3 DPI 感知](#-problem-33-dpi-感知)
4. [界面逻辑缺陷](#4-界面逻辑缺陷)
    - ~~[4.1 show() 与 _close() 锁竞争](#-problem-41-show-与-_close-锁竞争)~~
   - [4.2 overrideredirect(True) 下 lift/focus 失效](#-problem-42-overrideredirecttrue-下-liftfocus-失效)
   - [4.3 _check_outside 鼠标按下误触发](#-problem-43-_check_outside-鼠标按下误触发)
5. [异常容错](#5-异常容错)
   - ~~[5.1 on_toggle_autostart 无异常保护](#-problem-51-on_toggle_autostart-无异常保护)~~
    - ~~[5.2 ddcci 全局变量非线程安全](#-problem-52-ddcci-全局变量非线程安全)~~
    - ~~[5.3 IPC 粘包/半包处理不健壮](#-problem-53-ipc-粘包半包处理不健壮)~~
6. [PyInstaller 打包优化](#6-pyinstaller-打包优化)
   - ~~[6.1 缺少 win32com 与 pystray 隐式导入](#-problem-61-缺少-win32com-与-pystray-隐式导入)~~
   - [6.2 构建脚本缺少参数](#-problem-62-构建脚本缺少参数)
   - [6.3 无窗口模式缺少崩溃日志](#-problem-63-无窗口模式缺少崩溃日志)
7. [代码规范冗余](#7-代码规范冗余)
    - ~~[7.1 _is_frozen() 三处重复](#-problem-71-_isfrozen-三处重复)~~
    - ~~[7.2 _app_dir() 二处重复](#-problem-72-_appdir-二处重复)~~
   - [7.3 硬编码常量清单](#-problem-73-硬编码常量清单)
8. [高危隐患](#8-高危隐患)
   - [8.1 全局键盘钩子 — 隐式键盘记录](#-problem-81-全局键盘钩子--隐式键盘记录)
   - [8.2 ShellExecuteW 无条件提权](#-problem-82-shellexecutew-无条件提权)
    - ~~[8.3 IPC 命名管道无 ACL](#-problem-83-ipc-命名管道无-acl)~~
   - [8.4 config.json 明文存储](#-problem-84-configjson-明文存储)

---

## 1. UI 线程风险

### ~~🔴 Problem 1.1: DDC/CI 枚举与设值阻塞主线程~~ ✅ 已修复

**文件位置：** `plugins\brightness_controller\flyout.py:220` → `plugins\brightness_controller\ddcci.py:126-160, 174-178`

**问题描述：** `FlyoutWindow._show_internal()` 调用 `self._refresh_monitors()` → `enumerate_monitors()` (ddcci:126)，内部为同步 Win32 API 调用涉及硬件 I/O。滑块拖动回调 (flyout:293-295) 同步调用 `set_brightness()` (ddcci:174)。所有操作均在 `schedule_ui` 投递的 tkinter 主线程执行，DDC/CI I/O 阻塞会导致整个托盘 UI 冻结。

**修复方案：** 使用 threading + schedule_ui 解耦：

```python
# ddcci.py — 增加异步包装
import threading

def enumerate_monitors_async(callback):
    def _worker():
        result = enumerate_monitors()
        callback(result)
    threading.Thread(target=_worker, daemon=True).start()

def set_brightness_async(monitor, value, callback=None):
    def _worker():
        set_brightness(monitor, value)
        if callback:
            callback(value)
    threading.Thread(target=_worker, daemon=True).start()
```

```python
# flyout.py:220 替换为：
def _show_internal(self):
    self._refresh_monitors_async()

def _refresh_monitors_async(self):
    def _done(monitors):
        self.app.schedule_ui(lambda: self._build_ui(monitors))
    from plugins.brightness_controller.ddcci import enumerate_monitors_async
    enumerate_monitors_async(_done)
```

```python
# flyout.py:293-295 — 滑块回调改为：
def _on_slider_change(self, val, mon, pv):
    pv.set(f"{int(val)}")
    self._schedule()
    from plugins.brightness_controller.ddcci import set_brightness_async
    set_brightness_async(mon, int(val))
```

---

### 🟡 Problem 1.2: 日志文件 RotatingFileHandler 初始化在主线程

**文件位置：** `core\logger.py:42`

**问题描述：** `RotatingFileHandler` 在模块加载时立即初始化。如果 `_LOG_FILE` 无法写入（如 exe 位于 `Program Files`），会直接抛出异常。

**修复方案：**

```python
# logger.py 修复
import os

def _get_log_dir():
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        log_dir = os.path.join(base, "MyToolBox", "logs")
    else:
        log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    return log_dir

_LOG_FILE = os.path.join(_get_log_dir(), "mytoolbox.log")
```

---

## 2. 内存泄漏

### ~~🔴 Problem 2.1: PopupWindow GDI 对象未释放~~ ✅ 已修复

**文件位置：** `plugins\input_state_notifier\popup.py:177-204`

**问题描述：** 独立线程消息循环退出时（如异常终止），`_hbmp`（GDI 位图）、`_hdc_mem`（兼容 DC）不会被释放，造成 GDI 对象泄漏。

**修复方案：**

```python
# popup.py:198-204
def _run(self):
    # ... 前面代码不变 ...
    try:
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        if hasattr(self, '_hbmp') and self._hbmp:
            gdi32.DeleteObject(self._hbmp)
        if hasattr(self, '_hdc_mem') and self._hdc_mem:
            gdi32.DeleteDC(self._hdc_mem)
```

---

### ~~🟡 Problem 2.2: IPC Listener 线程累积~~ ✅ 已修复

**文件位置：** `core\ipc_server.py:36-37`

**问题描述：** 每次 `listen()` 创建新 daemon 线程，同名插件反复启停时线程只增不减。

**修复方案：**

```python
# ipc_server.py:33-38
def listen(self, plugin_name):
    if plugin_name in self._listener_threads:
        self._cleanup_pipe(plugin_name)
    pipe_name = make_pipe_name(plugin_name)
    self._log.info(f"IPC 监听: {pipe_name}")
    t = threading.Thread(target=self._listen_loop, args=(plugin_name, pipe_name), daemon=True)
    self._listener_threads[plugin_name] = t
    t.start()
```

---

### 🟡 Problem 2.3: FlyoutWindow 定时器链未清理

**文件位置：** `plugins\brightness_controller\flyout.py:256, 326`

**问题描述：** `_check_outside` 每 50ms 通过 `after()` 重新注册自身，`_close()` 只 cancel `_timer`（自动关闭定时器），不 cancel `_check_outside` 链。窗口销毁后 `after` 回调仍会触发。

**修复方案：**

```python
# flyout.py:256
self._check_timer = None

# _check_outside 改为：
def _check_outside(self):
    if not self._win or not self._win.winfo_exists():
        self._check_timer = None
        return
    # ... 原有逻辑 ...
    self._check_timer = self._win.after(50, self._check_outside)

# _close 中增加：
def _close(self):
    self._cancel()
    if hasattr(self, '_check_timer') and self._check_timer:
        try:
            if self._win:
                self._win.after_cancel(self._check_timer)
        except Exception:
            pass
        self._check_timer = None
    # ...
```

---

## 3. Windows 平台兼容

### ~~🔴 Problem 3.1: 字体路径硬编码 C: 盘~~ ✅ 已修复

**文件位置：** `plugins\input_state_notifier\popup.py:68`

**问题描述：** `"C:/Windows/Fonts/msyh.ttc"` 假设 Windows 安装在 C: 盘，若系统盘为 D: 或其他则失败。

**修复方案：**

```python
# popup.py:67-69
_windows_fonts = os.path.join(
    os.environ.get("SystemRoot", "C:\\Windows"), "Fonts"
)
fonts_to_try = [
    os.path.join(_windows_fonts, "msyh.ttc"),
    os.path.join(_windows_fonts, "msyhbd.ttc"),
    os.path.join(_windows_fonts, "segoeui.ttf"),
    "msyh.ttc", "segoeui.ttf",
]
```

---

### 🟡 Problem 3.2: 日志和配置文件写入 exe 同目录

**文件位置：** `core\logger.py:7-14`, `core\config_manager.py:29-34`

**问题描述：** 当 exe 安装在 `C:\Program Files` 等受保护目录时，写入日志和 config.json 会因权限失败。

**修复方案：**

```python
# config_manager.py:29-35
def _app_data_dir():
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        data_dir = os.path.join(base, "MyToolBox")
    else:
        data_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(data_dir, exist_ok=True)
    return data_dir

_CONFIG_PATH = os.path.join(_app_data_dir(), "config.json")
```

---

### 🟢 Problem 3.3: DPI 感知

**文件位置：** `core\app.py:56-60`

**评价：** 已设置 `SetProcessDpiAwareness(1)` + 回退 `SetProcessDPIAware()`，满足基本高 DPI 场景。

---

## 4. 界面逻辑缺陷

### ~~🟡 Problem 4.1: `show()` 与 `_close()` 锁竞争~~ ✅ 已修复

**文件位置：** `plugins\brightness_controller\flyout.py:209-211 vs 342-350`

**问题描述：** `show()` 使用 `threading.Lock()` 保护，但 `_close()` 和 `_check_outside()` 不获取锁。多线程下 `_close()` 可能在 `_show_internal` 中途执行，破坏窗口状态。

**修复方案：**

```python
# flyout.py:342-350
def _close(self):
    with self._lock:
        self._cancel()
        if self._win:
            try:
                self._win.destroy()
            except Exception:
                pass
            self._win = None
            self._sliders = []
```

---

### 🟡 Problem 4.2: `overrideredirect(True)` 下 lift/focus 失效

**文件位置：** `plugins\brightness_controller\flyout.py:215-216`

**问题描述：** 无标题栏窗口的 `lift()` 和 `focus_force()` 在某些 Windows 版本无效。

**修复方案：**

```python
# flyout.py:215-216
if self._win and self._win.winfo_exists():
    import ctypes
    HWND_TOPMOST = -1
    SWP_SHOWWINDOW = 0x0040
    hwnd = ctypes.windll.user32.GetParent(self._win.winfo_id())
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                      0x0002 | 0x0001 | SWP_SHOWWINDOW)
    self._win.lift()
    self._win.focus_force()
    self._schedule()
    return
```

---

### 🟡 Problem 4.3: `_check_outside` 鼠标按下误触发

**文件位置：** `plugins\brightness_controller\flyout.py:255, 314`

**问题描述：** `GetAsyncKeyState(1)` 检测左键按下，`_prev_mouse` 初始化为 `False`。若打开窗口时鼠标已按下，窗口会立即关闭。

**修复方案：**

```python
# flyout.py:255
self._prev_mouse = bool(ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
```

---

## 5. 异常容错

### ~~🔴 Problem 5.1: `on_toggle_autostart` 无异常保护~~ ✅ 已修复

**文件位置：** `core\app.py:205-218`

**问题描述：** COM 初始化失败、`SpecialFolders` 异常、lnk 创建失败时直接崩溃，无任何 try/except。

**修复方案：**

```python
# app.py:205-218
def on_toggle_autostart(self):
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        startup = shell.SpecialFolders("Startup")
        lnk_path = os.path.join(startup, "MyToolBox.lnk")
        if os.path.exists(lnk_path):
            os.remove(lnk_path)
            self._autostart_enabled = False
        else:
            shortcut = shell.CreateShortCut(lnk_path)
            shortcut.TargetPath = _get_exe_path()
            shortcut.WorkingDirectory = os.path.dirname(_get_exe_path())
            shortcut.Save()
            self._autostart_enabled = True
    except Exception as e:
        if self._log:
            self._log.error(f"切换开机自启失败: {e}")
    self._update_menu()
```

---

### ~~🟡 Problem 5.2: ddcci 全局变量非线程安全~~ ✅ 已修复

**文件位置：** `plugins\brightness_controller\ddcci.py:96, 119`

**问题描述：** `_enum_list` 为模块级全局变量，`_get_monitor_handles()` 写入，`_enum_proc` 读取。多线程并发调用 `enumerate_monitors()` 产生竞态。

**修复方案：**

```python
# ddcci.py
import threading
_thread_local = threading.local()

def _get_monitor_handles():
    _thread_local.enum_list = []
    if not _EnumDisplayMonitors(None, None, _enum_callback, 0):
        raise ctypes.WinError()
    return list(_thread_local.enum_list)

def _enum_proc(hmonitor, hdc, rect, lparam):
    lst = getattr(_thread_local, 'enum_list', None)
    if lst is not None:
        lst.append(hmonitor)
    return True
```

---

### ~~🟡 Problem 5.3: IPC 粘包/半包处理不健壮~~ ✅ 已修复

**文件位置：** `core\ipc_server.py:64-81`, `core\ipc_client.py:50-70`

**问题描述：** `ReadFile` 返回数据可能不完整（单条消息拆成多次 ReadFile），当前仅按 `\n` 分割，可能读到半行。

**修复方案：**

```python
# ipc_server.py:68-81
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
            elif hr == 234:  # MORE_DATA
                buf += data if data else b""
                continue
            else:
                break
        except pywintypes.error:
            break
    self._on_disconnect(plugin_name)
```

---

## 6. PyInstaller 打包优化

### ~~🔴 Problem 6.1: 缺少 win32com 与 pystray 隐式导入~~ ✅ 已修复

**文件位置：** `MyToolBox.spec`

**问题描述：** `win32com.client.Dispatch` 打包后可能因 `gen_py` 缓存缺失而失败；`pystray` 的 PIL 后端需额外 hooks。

**修复方案：**

```python
# MyToolBox.spec
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('plugins/brightness_controller/assets/fluent.ico', 'plugins/brightness_controller/assets'),
    ],
    hiddenimports=[
        # ... 原有内容 ...
        'win32com',
        'win32com.client',
        'pystray',
        'PIL._tkinter_finder',
    ],
    hooksconfig={
        'PyInstaller': {
            'hiddenimports': ['win32com.gen_py'],
        }
    },
    # ...
)
```

---

### 🟡 Problem 6.2: 构建脚本缺少参数

**文件位置：** `build.ps1`

**修复方案：**

```powershell
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean MyToolBox.spec
```

---

### 🟡 Problem 6.3: 无窗口模式缺少崩溃日志

**文件位置：** `MyToolBox.spec:49` (`console=False`)

**修复方案（main.py 中增加）：**

```python
# main.py 顶部
import sys
import os
import traceback

def excepthook(exc_type, exc_value, exc_tb):
    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", "."), "MyToolBox")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "crash.log")
    with open(log_path, "a", encoding="utf-8") as f:
        traceback.print_exception(exc_type, exc_value, exc_tb, file=f)

sys.excepthook = excepthook
```

---

## 7. 代码规范冗余

### ~~🟡 Problem 7.1: `_is_frozen()` 三处重复~~ ✅ 已修复

**文件位置：** `main.py:17-18`, `core\app.py:13-14`, `plugins\brightness_controller\plugin.py:12-13`

**修复方案：**

```python
# core/__init__.py
import sys

def is_frozen():
    return getattr(sys, "frozen", False)
```

---

### ~~🟡 Problem 7.2: `_app_dir()` 二处重复~~ ✅ 已修复

**文件位置：** `core\config_manager.py:29-32`, `core\logger.py:7-10`

**修复方案：** 统一到 `core/__init__.py`。

---

### 🟢 Problem 7.3: 硬编码常量清单

| 位置 | 常量 | 建议 |
|------|------|------|
| `flyout.py:30` | `AUTO_MS = 2500` | 抽取为配置项 |
| `limiter.py:80` | `TARGET_EXE = "SGuard64.exe"` | 抽取到配置 |
| `popup.py:37` | `POPUP_W, POPUP_H = 160, 80` | 适配 DPI |
| `plugin_helper.py:30` | 硬编码 config dict | 从共享配置加载 |

---

## 8. 高危隐患

### 🔴 Problem 8.1: 全局键盘钩子 — 隐式键盘记录

**文件位置：** `plugins\input_state_notifier\notifier.py:224-228`

**问题描述：** `WH_KEYBOARD_LL` 钩子捕捉所有进程的键盘输入。虽然当前只关注 Caps/Num/Ctrl+Space，但在安全审计中属于键盘记录器行为。

**修复方案：**

```python
# notifier.py:224
def start(self):
    if self._log:
        self._log.warning("InputStateMonitor: 注册全局键盘钩子 (WH_KEYBOARD_LL)")
    # ... 原有逻辑 ...
```

---

### 🔴 Problem 8.2: ShellExecuteW 无条件提权

**文件位置：** `core\app.py:165`

**问题描述：** `runas` 操作码无条件弹出 UAC 提权对话框。若 exe 路径被篡改（如位于可写目录），可导致提权执行恶意代码。

**修复方案：**

```python
# app.py:156-167
def _start_helper_plugin(self, name, plugin):
    self.ipc.listen(name)
    exe = _get_exe_path()
    if _is_frozen():
        exe_dir = os.path.dirname(os.path.abspath(exe))
        safe_dirs = [
            os.environ.get("ProgramFiles", "C:\\Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        if not any(exe_dir.lower().startswith(s.lower()) for s in safe_dirs if s):
            self._log.warning(f"Helper exe 路径不在安全目录: {exe}")
    # ... 原有逻辑 ...
```

---

### ~~🟡 Problem 8.3: IPC 命名管道无 ACL~~ ✅ 已修复

**文件位置：** `core\ipc_server.py:43-48`

**问题描述：** 命名管道默认安全属性为 `None`，同一机器上的其他用户/进程可连接并伪造 IPC 消息。

**修复方案：**

```python
# ipc_server.py
import win32security
import win32api

def _create_secure_pipe(pipe_name):
    sd = win32security.SECURITY_ATTRIBUTES()
    sd.bInheritHandle = False
    user_sid = win32security.GetTokenInformation(
        win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY),
        win32security.TokenUser
    )[0]
    acl = win32security.ACL()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION,
                            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                            user_sid)
    sd.SetSecurityDescriptorDacl(1, acl, 0)
    return sd
```

---

### 🟡 Problem 8.4: config.json 明文存储

**文件位置：** `config.json`

**评价：** 当前不含敏感数据。若后续增加凭据类配置，建议改用 DPAPI 加密：

```python
import win32crypt
def encrypt_data(plain):
    return win32crypt.CryptProtectData(plain.encode("utf-16-le"), None, None, None, None, 0)

def decrypt_data(cipher):
    return win32crypt.CryptUnprotectData(cipher, None, None, None, None, 0)[0].decode("utf-16-le")
```

---

## 总结优先级矩阵

| 维度 | P0（必须修复） | P1（高优先级） | P2（中优先级） |
|------|---------------|---------------|---------------|
| **UI 线程** | ~~1.1 DDC/CI 主线程阻塞~~ | 1.2 日志路径权限 | |
| **内存泄漏** | ~~2.1 Popup GDI 泄漏~~ | ~~2.2 IPC 线程累积~~ | 2.3 定时器链 |
| **Win 兼容** | ~~3.1 硬编码 C: 盘~~ | 3.2 exe 同目录写权限 | 3.3 DPI |
| **界面逻辑** | | ~~4.1 锁竞争~~ | 4.2/4.3 焦点/鼠标 |
| **异常容错** | ~~5.1 自启 COM 无保护~~ | ~~5.2 ddcci 全局变量~~ | ~~5.3 IPC 粘包~~ |
| **打包优化** | ~~6.1 缺失 win32com/pystray~~ | 6.2 build 缺少参数 | 6.3 崩溃日志 |
| **规范冗余** | | ~~7.1/7.2 重复函数~~ | 7.3 硬编码常量 |
| **高危隐患** | 8.1 键盘钩子 / 8.2 UAC 提权 | ~~8.3 IPC 管道 ACL~~ | 8.4 配置存储 |

---

*审计结束，共识别 20+ 项风险点。*
