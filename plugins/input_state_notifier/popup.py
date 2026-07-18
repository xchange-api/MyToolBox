import ctypes
import queue
import threading
import unicodedata
from ctypes import wintypes

import win32api
import win32con
import win32gui
from PIL import Image, ImageDraw, ImageFont

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32

user32.SystemParametersInfoW.argtypes = [
    ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
]
user32.SystemParametersInfoW.restype = ctypes.c_bool

user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC,
    ctypes.c_void_p, ctypes.c_void_p,
    wintypes.HDC, ctypes.c_void_p,
    wintypes.COLORREF, ctypes.c_void_p, ctypes.c_ulong,
]
user32.UpdateLayeredWindow.restype = ctypes.c_bool

user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_void_p
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.KillTimer.restype = ctypes.c_bool

SPI_GETWORKAREA = 0x0030


class PopupWindow:
    POPUP_W, POPUP_H = 160, 80

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def show(self, text, duration=1):
        self._queue.put((text, duration))

    def _render_bytes(self, raw_text):
        SCALE = 2
        w = self.POPUP_W * SCALE
        h = self.POPUP_H * SCALE

        base_text = "".join(c for c in raw_text if unicodedata.combining(c) == 0)
        has_strikethrough = len(raw_text) != len(base_text)

        visible = len(base_text)
        font_size = (48 if visible <= 3 else 36) * SCALE

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            [(0, 0), (w - 1, h - 1)],
            radius=12 * SCALE, fill=(173, 172, 171, 210),
        )

        font = ImageFont.load_default()
        for name in [
            "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/segoeui.ttf", "msyh.ttc", "segoeui.ttf",
        ]:
            try:
                font = ImageFont.truetype(name, font_size)
                break
            except Exception:
                pass

        bbox = draw.textbbox((0, 0), base_text, font=font)
        tw = bbox[2] - bbox[0]
        cx = w // 2
        cy = h // 2
        draw.text((cx, cy), base_text, fill=(255, 255, 255, 255), font=font, anchor="mm")

        if has_strikethrough:
            draw.line(
                [(cx - tw // 2, cy), (cx + tw // 2, cy)],
                fill=(255, 255, 255, 255), width=3 * SCALE,
            )

        img = img.resize((self.POPUP_W, self.POPUP_H), Image.LANCZOS)

        r, g, b, a = img.split()
        return Image.merge("RGBA", (b, g, r, a)).tobytes()

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == win32con.WM_TIMER:
            if wparam == 100:
                try:
                    text, duration = self._queue.get_nowait()
                    user32.KillTimer(hwnd, 101)
                    self._set_content(hwnd, text)
                    user32.SetTimer(hwnd, 101, int(duration * 1000), None)
                except queue.Empty:
                    pass
            elif wparam == 101:
                user32.KillTimer(hwnd, 101)
                win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            return 0
        if msg == win32con.WM_DESTROY:
            win32api.PostQuitMessage(0)
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _set_content(self, hwnd, text):
        data = self._render_bytes(text)
        ctypes.memmove(self._ppv_bits, data, len(data))

        wa = wintypes.RECT()
        user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(wa), 0)
        cx = (wa.right - wa.left - self.POPUP_W) // 2
        cy = (wa.bottom - wa.top - self.POPUP_H) // 2 + wa.top

        pt_dst = wintypes.POINT(cx, cy)
        size = wintypes.SIZE(self.POPUP_W, self.POPUP_H)
        pt_src = wintypes.POINT(0, 0)

        user32.UpdateLayeredWindow(
            hwnd, None, ctypes.byref(pt_dst), ctypes.byref(size),
            self._hdc_mem, ctypes.byref(pt_src), 0,
            ctypes.byref(self._blend_func), 2,
        )
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    def _run(self):
        class_name = "ISN_Popup"

        wc = win32gui.WNDCLASS()
        wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = class_name
        wc.hbrBackground = win32gui.GetStockObject(win32con.NULL_BRUSH)
        self._wndproc_ref = self._wndproc
        wc.lpfnWndProc = self._wndproc_ref
        win32gui.RegisterClass(wc)

        hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
            | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW,
            class_name, "", win32con.WS_POPUP,
            0, 0, self.POPUP_W, self.POPUP_H,
            0, 0, win32api.GetModuleHandle(None), None,
        )

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_ulong),
                ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long),
                ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort),
                ("biCompression", ctypes.c_ulong),
                ("biSizeImage", ctypes.c_ulong),
                ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", ctypes.c_ulong),
                ("biClrImportant", ctypes.c_ulong),
            ]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER)]

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.POPUP_W
        bmi.bmiHeader.biHeight = -self.POPUP_H
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        self._hdc_mem = gdi32.CreateCompatibleDC(0)
        bits_ptr = ctypes.POINTER(ctypes.c_ubyte)()
        self._hbmp = gdi32.CreateDIBSection(
            self._hdc_mem, ctypes.byref(bmi), 0,
            ctypes.byref(bits_ptr), None, 0,
        )
        self._ppv_bits = bits_ptr
        gdi32.SelectObject(self._hdc_mem, self._hbmp)

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", ctypes.c_byte),
                ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte),
            ]

        self._blend_func = BLENDFUNCTION(0, 0, 255, 1)

        user32.SetTimer(hwnd, 100, 50, None)

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        gdi32.DeleteObject(self._hbmp)
        gdi32.DeleteDC(self._hdc_mem)
