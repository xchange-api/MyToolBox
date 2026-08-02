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

user32.SendMessageW.argtypes = [
    wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM,
]
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetKeyboardLayout.argtypes = [ctypes.c_uint]
user32.GetKeyboardLayout.restype = ctypes.c_ulong
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
imm32.ImmGetDefaultIMEWnd.argtypes = [wintypes.HWND]
imm32.ImmGetDefaultIMEWnd.restype = wintypes.HWND

user32.SetWinEventHook.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
]
user32.SetWinEventHook.restype = wintypes.HANDLE
user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
user32.UnhookWinEvent.restype = wintypes.BOOL

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
ENGLISH_LANG_ID = 0x0409
IME_CMODE_CHINESE = 0x0400
EVENT_SYSTEM_FOREGROUND = 0x0003
WINEVENT_OUTOFCONTEXT = 0x0000


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", ctypes.c_ulong),
        ("scanCode", ctypes.c_ulong),
        ("flags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)

WinEventProc = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
    wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)


class InputStateMonitor:
    def __init__(self, config):
        self._popup = PopupWindow()

        self._caps_on = bool(user32.GetKeyState(VK_CAPITAL) & 1)
        self._num_on = bool(user32.GetKeyState(VK_NUMLOCK) & 1)
        self._ime_chinese = self._get_ime_is_chinese()

        self._prev_caps = self._caps_on
        self._prev_num = self._num_on
        self._prev_ime = self._ime_chinese

        self._last_toast = {"caps": 0.0, "num": 0.0, "ime": 0.0}
        self._ime_check_seq = 0
        self._ctrl_down = False

        self._hook_id = None
        self._win_event_hook = None
        self._running = False
        self._poll_thread = None
        self._stop_event = threading.Event()

        self._toast_duration = config.get("toast_duration", 0.8)
        self._debounce_interval = config.get("debounce_interval", 0.3)
        self._ime_check_delay = config.get("ime_check_delay", 0.05)

        self._hook_proc_cb = LowLevelKeyboardProc(self._hook_callback)
        self._win_event_proc_cb = WinEventProc(self._on_foreground_change)

    def _get_ime_is_chinese(self):
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False

        default_ime = imm32.ImmGetDefaultIMEWnd(hwnd)
        if not default_ime:
            return False

        if not user32.SendMessageW(default_ime, WM_IME_CONTROL, IMC_GETOPENSTATUS, 0):
            return False

        target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        lang_id = user32.GetKeyboardLayout(target_tid) & 0xFFFF

        if lang_id == ENGLISH_LANG_ID:
            return False

        conv_mode = user32.SendMessageW(default_ime, WM_IME_CONTROL, IMC_GETCONVERSIONMODE, 0)
        return bool(conv_mode & IME_CMODE_CHINESE)

    def _show_toast(self, text_type, text):
        now = time.monotonic()
        if now - self._last_toast.get(text_type, 0) < self._debounce_interval:
            return
        self._last_toast[text_type] = now
        self._popup.show(text, self._toast_duration)

    def _on_caps_change(self, state):
        self._caps_on = state
        if state != self._prev_caps:
            self._prev_caps = state
            self._show_toast("caps", "ABC" if state else "abc")

    def _on_num_change(self, state):
        self._num_on = state
        if state != self._prev_num:
            self._prev_num = state
            self._show_toast("num", "123" if state else "1\u03362\u03363\u0336")

    def _on_ime_change(self, is_chinese):
        self._ime_chinese = is_chinese
        if is_chinese != self._prev_ime:
            self._prev_ime = is_chinese
            if is_chinese and not self._caps_on:
                self._show_toast("ime", "中")
            else:
                self._show_toast("ime", "ABC" if self._caps_on else "abc")

    def _ime_poll_loop(self):
        while not self._stop_event.wait(60):
            try:
                self._ime_poll_once()
            except Exception:
                pass

    def _ime_poll_once(self):
        is_chinese = self._get_ime_is_chinese()
        if is_chinese != self._ime_chinese:
            self._on_ime_change(is_chinese)

    def _on_foreground_change(self, hWinEventHook, event, hwnd,
                              idObject, idChild, dwEventThread, dwmsEventTime):
        if not self._running:
            return
        try:
            is_chinese = self._get_ime_is_chinese()
            self._ime_chinese = is_chinese
            self._prev_ime = is_chinese
        except Exception:
            pass

    def _hook_callback(self, nCode, wParam, lParam):
        try:
            if nCode >= 0:
                kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if wParam == WM_KEYDOWN:
                    if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                        self._ctrl_down = True
                    elif kb.vkCode == VK_CAPITAL:
                        self._on_caps_change(not self._caps_on)
                    elif kb.vkCode == VK_NUMLOCK:
                        self._on_num_change(not self._num_on)
                elif wParam == WM_KEYUP:
                    if kb.vkCode in (VK_LCONTROL, VK_RCONTROL):
                        self._ctrl_down = False
                    elif kb.vkCode in (VK_LSHIFT, VK_RSHIFT):
                        self._trigger_ime_check()
                    elif kb.vkCode == VK_SPACE and self._ctrl_down:
                        self._trigger_ime_check()
        except Exception:
            pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _trigger_ime_check(self):
        self._ime_check_seq += 1
        seq = self._ime_check_seq
        threading.Thread(
            target=self._delayed_ime_check, args=(seq,), daemon=True,
        ).start()

    def _delayed_ime_check(self, seq):
        time.sleep(self._ime_check_delay)
        if seq != self._ime_check_seq:
            return
        try:
            self._on_ime_change(self._get_ime_is_chinese())
        except Exception:
            pass

    def start(self):
        if self._running:
            return

        self._stop_event.clear()

        try:
            hmod = kernel32.GetModuleHandleW(None)
            self._hook_id = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hook_proc_cb, hmod, 0)
        except Exception:
            self._hook_id = None

        try:
            self._win_event_hook = user32.SetWinEventHook(
                EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND,
                None, self._win_event_proc_cb, 0, 0, WINEVENT_OUTOFCONTEXT,
            )
        except Exception:
            self._win_event_hook = None

        self._running = True
        self._poll_thread = threading.Thread(target=self._ime_poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        self._running = False
        self._ime_check_seq += 1
        self._stop_event.set()

        if self._win_event_hook:
            user32.UnhookWinEvent(self._win_event_hook)
            self._win_event_hook = None
        if self._hook_id:
            user32.UnhookWindowsHookEx(self._hook_id)
            self._hook_id = None
