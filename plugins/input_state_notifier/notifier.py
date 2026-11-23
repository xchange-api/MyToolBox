"""CapsLock / NumLock / IME 状态监听。

通过 ``WH_KEYBOARD_LL`` 低级键盘钩子观察按键，在状态真正发生变化时
调用 ``PopupWindow`` 显示提示。

设计要点：
    * CapsLock / NumLock 靠维护本地状态位，不读 ``GetKeyState``，避免
      钩子回调里再做一次同步系统调用；
    * IME 状态无法从按键直接推断，只能在按键抬起后延迟查询，并且要求
      期间前台窗口和焦点控件都没变，否则视为"不是本窗口的切换"。
"""

import ctypes
import threading
import time
from ctypes import wintypes

import win32gui
import win32process

from plugins.input_state_notifier.popup import PopupWindow

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
imm32 = ctypes.windll.imm32

# 下面统一声明 Win32 函数的签名，避免 64 位下指针被截断
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = ctypes.c_bool
user32.CallNextHookEx.argtypes = [
    ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
]
user32.CallNextHookEx.restype = wintypes.LPARAM

user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.GetKeyboardLayout.argtypes = [ctypes.c_uint]
user32.GetKeyboardLayout.restype = ctypes.c_ulong
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND

user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
    ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_IME_CONTROL = 0x0283
IMC_GETOPENSTATUS = 0x0005
IMC_GETCONVERSIONMODE = 0x0001
VK_CAPITAL = 0x14
VK_NUMLOCK = 0x90
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_MENU = 0x12
ENGLISH_LANG_ID = 0x0409
# imm.h 中 IME_CMODE_CHINESE 是 IME_CMODE_NATIVE 的别名，值为 0x0001。
# 0x0400 是 IME_CMODE_SYMBOL（中文标点），不能用来判断中英文模式：
# Win10 22H2 的微软拼音默认开启中文标点，恰好同时带 0x0001|0x0400，
# 所以旧代码在本机"碰巧"能用；Win Server 2019 等环境不置 0x0400 就会失效。
IME_CMODE_NATIVE = 0x0001
SMTO_ABORTIFHUNG = 0x0002
# 常规查询与"按键按下瞬间取基线"两种场景的超时，后者必须更短，
# 因为它在钩子回调里同步执行，会直接拖慢按键响应
IME_QUERY_TIMEOUT_MS = 100
IME_ARM_TIMEOUT_MS = 30


class KBDLLHOOKSTRUCT(ctypes.Structure):
    """低级键盘钩子回调收到的按键信息。"""

    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class GUITHREADINFO(ctypes.Structure):
    """指定线程的 GUI 状态，用于判断焦点控件是否变化。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)]
user32.GetGUIThreadInfo.restype = wintypes.BOOL


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)


class InputStateMonitor:
    """监听 CapsLock / NumLock / IME 变化并触发提示。

    Args:
        config: 插件配置，支持 ``toast_duration``、
            ``debounce_interval``、``ime_check_delay`` 三个键。
    """

    def __init__(self, config):
        self._popup = PopupWindow()

        # 初始状态从系统读一次，之后完全依赖钩子事件维护，避免在
        # 回调里反复查询键盘状态
        self._caps_on = bool(user32.GetKeyState(VK_CAPITAL) & 1)
        self._num_on = bool(user32.GetKeyState(VK_NUMLOCK) & 1)

        self._prev_caps = self._caps_on
        self._prev_num = self._num_on

        self._last_toast = {"caps": 0.0, "num": 0.0, "ime": 0.0}
        self._ime_check_seq = 0
        self._ctrl_down = False
        self._shift_solo = False
        self._ctrl_space_armed = False

        # 切换键按下时的上下文：只有同一窗口/焦点内的状态变化才提示
        self._gesture_hwnd = 0
        self._gesture_focus = 0
        self._gesture_prev = None

        self._hook_id = None
        self._running = False

        self._toast_duration = config.get("toast_duration", 0.8)
        self._debounce_interval = config.get("debounce_interval", 0.3)
        self._ime_check_delay = config.get("ime_check_delay", 0.05)

        self._hook_proc_cb = LowLevelKeyboardProc(self._hook_callback)

    def _get_focus_window(self, hwnd):
        """取指定窗口所在线程的焦点控件，用于判断按键期间焦点是否变化。

        Args:
            hwnd: 目标顶层窗口句柄。

        Returns:
            焦点控件句柄；拿不到时退回 ``hwnd`` 本身，窗口无效时返回 0。
        """
        if not hwnd:
            return 0
        try:
            tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            if not tid:
                return hwnd
            info = GUITHREADINFO()
            info.cbSize = ctypes.sizeof(GUITHREADINFO)
            if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
                return info.hwndFocus
        except Exception:
            pass
        return hwnd

    def _query_ime(self, ime_wnd, msg, timeout_ms):
        """带超时的 WM_IME_CONTROL，目标 IME 卡死时不会拖住调用方。

        Returns:
            IME 返回的数值；超时或调用失败时返回 ``None``。
        """
        result = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(
            ime_wnd, WM_IME_CONTROL, msg, 0,
            SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result),
        )
        if not ok:
            return None
        return result.value

    def _get_ime_is_chinese(self, hwnd=None, timeout_ms=IME_QUERY_TIMEOUT_MS):
        """判断指定窗口当前的 IME 是否处于中文模式。

        先看 IME 是否打开、键盘布局是否是英文，再读转换模式里的
        ``IME_CMODE_NATIVE`` 位。

        Args:
            hwnd: 目标窗口，缺省取当前前台窗口。
            timeout_ms: 每次 ``WM_IME_CONTROL`` 的超时毫秒数。

        Returns:
            ``True`` 表示中文，``False`` 表示英文，``None`` 表示无法判断
            （无 IME 窗口 / 查询超时 / 调用异常）。
        """
        if hwnd is None:
            hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None

        try:
            default_ime = imm32.ImmGetDefaultIMEWnd(hwnd)
            if not default_ime:
                return None

            open_status = self._query_ime(default_ime, IMC_GETOPENSTATUS, timeout_ms)
            if open_status is None:
                return None
            if not open_status:
                return False

            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            lang_id = user32.GetKeyboardLayout(target_tid) & 0xFFFF

            if lang_id == ENGLISH_LANG_ID:
                return False

            conv_mode = self._query_ime(default_ime, IMC_GETCONVERSIONMODE, timeout_ms)
            if conv_mode is None:
                return None
            return bool(conv_mode & IME_CMODE_NATIVE)
        except Exception:
            return None

    def _show_toast(self, text_type, text, strike=False):
        """按类别限流后弹出提示。

        Args:
            text_type: 限流桶名，``caps`` / ``num`` / ``ime`` 各自独立计时。
            text: 提示文字。
            strike: 是否显示删除线。
        """
        now = time.monotonic()
        if now - self._last_toast.get(text_type, 0) < self._debounce_interval:
            return
        self._last_toast[text_type] = now
        self._popup.show(text, self._toast_duration, strike)

    def _on_caps_change(self, state):
        """更新 CapsLock 本地状态，仅在真正翻转时提示。

        翻转后的值由调用方按下取反得到，不查询系统状态。
        """
        self._caps_on = state
        if state != self._prev_caps:
            self._prev_caps = state
            self._show_toast("caps", "ABC" if state else "abc")

    def _on_num_change(self, state):
        """更新 NumLock 本地状态，仅在真正翻转时提示。

        关闭时给文字加删除线，与 Windows 原生提示一致。
        """
        self._num_on = state
        if state != self._prev_num:
            self._prev_num = state
            self._show_toast("num", "123", strike=not state)

    def _show_ime_toast(self, is_chinese):
        """按 IME 状态和 CapsLock 状态组合出要显示的文字。"""
        if is_chinese and not self._caps_on:
            self._show_toast("ime", "中")
        else:
            self._show_toast("ime", "ABC" if self._caps_on else "abc")

    def _hook_callback(self, nCode, wParam, lParam):
        """低级键盘钩子回调。

        只做状态位维护与手势识别，绝不做可能阻塞的 I/O——该回调阻塞
        会拖慢全系统按键响应，累计超时还会被 Windows 静默摘掉钩子。

        Args:
            nCode: 钩子代码，小于 0 表示必须直接透传。
            wParam: 消息类型，``WM_KEYDOWN`` 或 ``WM_KEYUP``。
            lParam: 指向 ``KBDLLHOOKSTRUCT`` 的指针。

        Returns:
            交给下一个钩子的结果。
        """
        try:
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if wParam == WM_KEYDOWN:
                    if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                        self._ctrl_down = True
                        self._shift_solo = False
                        self._ctrl_space_armed = False
                    elif kb.vkCode in (VK_LSHIFT, VK_RSHIFT):
                        # 只有"单独按 Shift"才可能是中英切换，带 Ctrl/Alt
                        # 的组合键（如 Ctrl+Shift+F）不应触发提示
                        self._shift_solo = not (
                            self._ctrl_down
                            or user32.GetAsyncKeyState(VK_CONTROL) & 0x8000
                            or user32.GetAsyncKeyState(VK_MENU) & 0x8000
                        )
                        self._ctrl_space_armed = False
                        if self._shift_solo:
                            self._arm_gesture()
                    elif kb.vkCode == VK_CAPITAL:
                        self._on_caps_change(not self._caps_on)
                        self._shift_solo = False
                        self._ctrl_space_armed = False
                    elif kb.vkCode == VK_NUMLOCK:
                        self._on_num_change(not self._num_on)
                        self._shift_solo = False
                        self._ctrl_space_armed = False
                    elif kb.vkCode == VK_SPACE and self._ctrl_down:
                        # Ctrl+Space 也是中英切换，按下即记录上下文
                        self._ctrl_space_armed = True
                        self._arm_gesture()
                    else:
                        self._shift_solo = False
                        self._ctrl_space_armed = False
                elif wParam == WM_KEYUP:
                    if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                        self._ctrl_down = False
                    elif kb.vkCode in (VK_LSHIFT, VK_RSHIFT):
                        if self._shift_solo:
                            self._shift_solo = False
                            self._trigger_ime_check()
                    elif kb.vkCode == VK_SPACE and self._ctrl_space_armed:
                        self._ctrl_space_armed = False
                        self._trigger_ime_check()
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _arm_gesture(self):
        """切换键按下时记录窗口/焦点/当前状态，抬起后只在同一上下文里提示。

        Note:
            这里会在钩子回调里同步做一次带超时的 IME 查询，因此使用更短
            的 ``IME_ARM_TIMEOUT_MS``（审计报告 P1-12）。
        """
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            self._gesture_hwnd = 0
            self._gesture_focus = 0
            self._gesture_prev = None
            return
        self._gesture_hwnd = hwnd
        self._gesture_focus = self._get_focus_window(hwnd)
        # 必须按手势所在的窗口现场取值：缓存的全局状态可能还是上一个窗口的
        self._gesture_prev = self._get_ime_is_chinese(
            hwnd, timeout_ms=IME_ARM_TIMEOUT_MS,
        )

    def _trigger_ime_check(self):
        """递增序号并起线程延迟查询 IME 状态。

        序号用于丢弃过期查询：连续快速切换时只有最后一次有效。
        """
        self._ime_check_seq += 1
        seq = self._ime_check_seq
        threading.Thread(
            target=self._delayed_ime_check,
            args=(
                seq, self._gesture_hwnd, self._gesture_focus,
                self._gesture_prev,
            ),
            daemon=True,
        ).start()

    def _delayed_ime_check(self, seq, hwnd, focus, prev_state):
        """延迟一小段时间后查询 IME，确认状态真的变了才提示。

        Args:
            seq: 触发时的序号，与当前序号不符说明已被更新的手势取代。
            hwnd: 手势开始时记录的顶层窗口。
            focus: 手势开始时记录的焦点控件。
            prev_state: 按键前的 IME 状态，``None`` 表示当时无法判断。
        """
        time.sleep(self._ime_check_delay)
        if seq != self._ime_check_seq:
            return
        try:
            # 按键期间窗口或焦点变了：不是当前窗口里的切换，不提示
            if not hwnd or win32gui.GetForegroundWindow() != hwnd:
                return
            if focus and self._get_focus_window(hwnd) != focus:
                return

            state = self._get_ime_is_chinese(hwnd)
            if state is None:
                return
            if prev_state is not None and state != prev_state:
                self._show_ime_toast(state)
        except Exception:
            pass

    def start(self):
        """安装全局低级键盘钩子并复位手势状态。

        钩子安装失败不影响托盘功能，只是不再有提示。
        """
        if self._running:
            return

        try:
            hmod = kernel32.GetModuleHandleW(None)
            self._hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_cb, hmod, 0)
        except Exception:
            self._hook_id = None

        self._ctrl_down = False
        self._shift_solo = False
        self._ctrl_space_armed = False
        self._gesture_hwnd = 0
        self._gesture_focus = 0
        self._gesture_prev = None

        self._running = True

    def stop(self):
        """摘掉钩子并让正在进行的延迟查询作废。"""
        self._running = False
        self._ime_check_seq += 1

        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
