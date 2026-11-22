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
IME_QUERY_TIMEOUT_MS = 100
IME_ARM_TIMEOUT_MS = 30


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class GUITHREADINFO(ctypes.Structure):
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
    def __init__(self, config):
        self._popup = PopupWindow()

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
        """取指定窗口所在线程的焦点控件，用于判断按键期间焦点是否变化"""
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
        """带超时的 WM_IME_CONTROL，目标 IME 卡死时不会拖住调用方"""
        result = ctypes.c_size_t(0)
        ok = user32.SendMessageTimeoutW(
            ime_wnd, WM_IME_CONTROL, msg, 0,
            SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result),
        )
        if not ok:
            return None
        return result.value

    def _get_ime_is_chinese(self, hwnd=None, timeout_ms=IME_QUERY_TIMEOUT_MS):
        """True=中文，False=英文，None=无法判断（无 IME 窗口/查询超时）"""
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
        now = time.monotonic()
        if now - self._last_toast.get(text_type, 0) < self._debounce_interval:
            return
        self._last_toast[text_type] = now
        self._popup.show(text, self._toast_duration, strike)

    def _on_caps_change(self, state):
        self._caps_on = state
        if state != self._prev_caps:
            self._prev_caps = state
            self._show_toast("caps", "ABC" if state else "abc")

    def _on_num_change(self, state):
        self._num_on = state
        if state != self._prev_num:
            self._prev_num = state
            self._show_toast("num", "123", strike=not state)

    def _show_ime_toast(self, is_chinese):
        if is_chinese and not self._caps_on:
            self._show_toast("ime", "中")
        else:
            self._show_toast("ime", "ABC" if self._caps_on else "abc")

    def _hook_callback(self, nCode, wParam, lParam):
        try:
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if wParam == WM_KEYDOWN:
                    if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                        self._ctrl_down = True
                        self._shift_solo = False
                        self._ctrl_space_armed = False
                    elif kb.vkCode in (VK_LSHIFT, VK_RSHIFT):
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
        """切换键按下时记录窗口/焦点/当前状态，抬起后只在同一上下文里提示"""
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
        self._running = False
        self._ime_check_seq += 1

        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
