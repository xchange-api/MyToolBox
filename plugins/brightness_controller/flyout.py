import ctypes
import math
import threading
import winreg
from ctypes import wintypes

import pystray
from PIL import Image

from plugins.brightness_controller.ddcci import (
    get_brightness, set_brightness_async,
    cleanup, enumerate_monitors_async,
)

BG = "#3C3C3C"
FG = "#ffffff"
SUN = "#ffc832"
TRACK = "#6b6b6b"
SEP = "#2d2d2d"

TRACK_H = 1
THUMB_W = 8
THUMB_H = 24

FLYOUT_PAD = 15
ROW_H = 42
SLIDER_W = 250
AUTO_MS = 2500


def get_accent_color():
    sources = [
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "AccentColor"),
        (r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent", "StartColor"),
        (r"Software\Microsoft\Windows\DWM", "AccentColor"),
    ]
    for path, name in sources:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as k:
                val, _ = winreg.QueryValueEx(k, name)
            if val == 0 or val == 0xFFFFFFFF:
                continue
            r = val & 0xFF
            g = (val >> 8) & 0xFF
            b = (val >> 16) & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
        except (FileNotFoundError, OSError):
            continue
    try:
        color = ctypes.c_uint32()
        opaque = ctypes.c_int()
        if ctypes.windll.dwmapi.DwmGetColorizationColor(ctypes.byref(color), ctypes.byref(opaque)) == 0:
            v = color.value
            r = (v >> 16) & 0xFF
            g = (v >> 8) & 0xFF
            b = v & 0xFF
            return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        pass
    return "#0078d4"


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def get_taskbar_info():
    MONITOR_DEFAULTTOPRIMARY = 1
    try:
        hmon = ctypes.windll.user32.MonitorFromPoint(
            wintypes.POINT(0, 0), MONITOR_DEFAULTTOPRIMARY)
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return "BOTTOM", (0, 0, 0, 0)
        b, w = mi.rcMonitor, mi.rcWork
        bw, bh = b.right - b.left, b.bottom - b.top
        ww, wh = w.right - w.left, w.bottom - w.top
        if b.left < w.left:
            pos = "LEFT"
        elif b.top < w.top:
            pos = "TOP"
        elif bw > ww:
            pos = "RIGHT"
        else:
            pos = "BOTTOM"
        return pos, (w.left, w.top, w.right, w.bottom)
    except Exception:
        return "BOTTOM", (0, 0, 0, 0)


class _Slider:
    """Custom horizontal slider using tkinter Canvas."""

    def __init__(self, parent, width, height, value=50, command=None, accent="#0078d4"):
        import tkinter as tk
        self._frame = tk.Frame(parent, width=width, height=height, bg=BG)
        self._val = max(0, min(100, value))
        self._cmd = command
        self._sw = width
        self._sh = height
        self._accent = accent
        self._drag = False
        self._frame.pack_propagate(False)

        self._can = tk.Canvas(self._frame, width=width, height=height, bg=BG, highlightthickness=0)
        self._can.pack(fill=tk.BOTH, expand=True)
        self._redraw()
        self._can.bind("<Button-1>", self._click)
        self._can.bind("<B1-Motion>", self._drag_ev)
        self._can.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", False))
        self._can.bind("<MouseWheel>", self._wheel)

    @property
    def widget(self):
        return self._frame

    def _redraw(self):
        self._can.delete("all")
        w, h = self._sw, self._sh
        pad, yc = 10, h // 2
        th = TRACK_H
        tw = w - pad * 2

        self._can.create_rectangle(pad, yc - th, pad + tw, yc + th, fill=TRACK, outline="")

        fw = int(tw * self._val / 100)
        if fw > 0:
            self._can.create_rectangle(pad, yc - th, pad + fw, yc + th, fill=self._accent, outline="")

        tx = pad + int(self._val * tw / 100)
        self._draw_pill(tx, yc, THUMB_W, THUMB_H, fill=self._accent, outline="")

    def _draw_pill(self, cx, cy, pw, ph, **kw):
        r = pw / 2
        pts = []
        for a in range(180, 361, 10):
            rad = math.radians(a)
            pts.append(cx + r * math.cos(rad))
            pts.append(cy - ph / 2 + r + r * math.sin(rad))
        pts.append(cx + r)
        pts.append(cy + ph / 2 - r)
        for a in range(0, 181, 10):
            rad = math.radians(a)
            pts.append(cx + r * math.cos(rad))
            pts.append(cy + ph / 2 - r + r * math.sin(rad))
        pts.append(cx - r)
        pts.append(cy - ph / 2 + r)
        self._can.create_polygon(pts, smooth=True, **kw)

    def _x_to_v(self, x):
        pad = 10
        cw = self._sw
        if cw <= pad * 2:
            return 50
        return max(0, min(100, int((x - pad) / (cw - pad * 2) * 100)))

    def _click(self, e):
        self._drag = True
        v = self._x_to_v(e.x)
        if v != self._val:
            self._val = v
            self._redraw()
            if self._cmd:
                self._cmd(self._val)

    def _drag_ev(self, e):
        if self._drag:
            v = self._x_to_v(max(10, min(e.x, self._sw - 10)))
            if v != self._val:
                self._val = v
                self._redraw()
                if self._cmd:
                    self._cmd(self._val)

    def _wheel(self, e):
        d = 5 if e.delta > 0 or e.num == 4 else -5
        self._val = max(0, min(100, self._val + d))
        self._redraw()
        if self._cmd:
            self._cmd(self._val)


class FlyoutWindow:
    """Manages the brightness flyout popup window. Must be used on the tkinter main thread."""

    def __init__(self, app, tk_root, icon_path):
        self.app = app
        self._root = tk_root
        self._icon_path = icon_path
        self._accent = get_accent_color()
        self._win = None
        self._sliders = []
        self._monitors = []
        self._timer = None
        self._prev_mouse = False
        self._lock = threading.Lock()

    def show(self):
        with self._lock:
            self._show_internal()

    def _show_internal(self):
        if self._win and self._win.winfo_exists():
            self._win.lift()
            self._win.focus_force()
            self._schedule()
            return

        cleanup(self._monitors)
        self._monitors = []
        self._win = None

        enumerate_monitors_async(lambda monitors:
            self.app.schedule_ui(lambda: self._build_ui(monitors)))

    def _build_ui(self, monitors):
        self._monitors = monitors
        if not self._monitors:
            return

        import tkinter as tk

        self._win = tk.Toplevel(self._root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=BG)
        self._win.bind("<Escape>", lambda e: self._close())
        self._win.protocol("WM_DELETE_WINDOW", self._close)

        n = len(self._monitors)
        total_h = n * ROW_H + (n - 1) * 1 + FLYOUT_PAD * 2
        total_w = 30 + SLIDER_W + 46 + FLYOUT_PAD * 2

        self._win.geometry(f"{total_w}x{total_h}")

        inner = tk.Frame(self._win, bg=BG)
        inner.pack(fill=tk.BOTH, expand=True, padx=FLYOUT_PAD, pady=FLYOUT_PAD)

        self._sliders = []

        for i, mon in enumerate(self._monitors):
            if i > 0:
                tk.Frame(inner, height=1, bg=SEP).pack(fill=tk.X)
            self._build_row(inner, mon, i)

        self._win.update_idletasks()
        req_w = inner.winfo_reqwidth() + FLYOUT_PAD * 2
        if req_w > total_w:
            total_w = req_w
            self._win.geometry(f"{total_w}x{total_h}")

        self._position(total_w, total_h)
        for s in self._sliders:
            s._redraw()
        self._win.focus_force()
        self._schedule()
        self._prev_mouse = bool(ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000)
        self._win.after(100, self._check_outside)

    def _build_row(self, parent, mon, idx):
        import tkinter as tk
        from PIL import ImageTk

        row = tk.Frame(parent, bg=BG)
        row.pack(fill=tk.X)

        cur = get_brightness(mon)

        icon_img = None
        try:
            pil_img = Image.open(self._icon_path).resize((20, 20), Image.LANCZOS)
            icon_img = ImageTk.PhotoImage(pil_img)
        except Exception:
            pass

        can = tk.Canvas(row, width=24, height=ROW_H, bg=BG, highlightthickness=0)
        can.pack(side=tk.LEFT)
        cy = ROW_H // 2
        if icon_img:
            can.create_image(12, cy, image=icon_img)
            can.image = icon_img
        else:
            can.create_oval(2, cy - 9, 20, cy + 9, fill=SUN, outline="")
            for d in range(0, 360, 45):
                rad = math.radians(d)
                can.create_line(12 + int(7 * math.cos(rad)), cy + int(7 * math.sin(rad)),
                                12 + int(12 * math.cos(rad)), cy + int(12 * math.sin(rad)),
                                fill=SUN, width=1)

        pv = tk.StringVar(value=f"{cur}")
        tk.Label(row, textvariable=pv, fg=FG, bg=BG, font=("Segoe UI", 18),
                 width=4, anchor=tk.E).pack(side=tk.RIGHT)

        sl = _Slider(row, SLIDER_W, ROW_H, cur,
                      lambda v, m=mon, p=pv: (
                          p.set(f"{int(v)}"), self._schedule(), set_brightness_async(m, int(v))
                      ),
                      accent=self._accent)
        sl.widget.pack(side=tk.RIGHT, padx=(4, 2))
        self._sliders.append(sl)

    def brightness_up(self):
        self._adjust_brightness(10)

    def brightness_down(self):
        self._adjust_brightness(-10)

    def _adjust_brightness(self, delta):
        monitors = list(self._monitors) if self._monitors else None
        if not monitors:
            enumerate_monitors_async(lambda mons: self._do_adjust(mons, delta))
        else:
            self._do_adjust(monitors, delta)

    def _do_adjust(self, monitors, delta):
        def _worker():
            new_vals = []
            for mon in monitors:
                try:
                    cur = get_brightness(mon)
                    new = max(0, min(100, cur + delta))
                    set_brightness_async(mon, new)
                    new_vals.append(new)
                except Exception:
                    new_vals.append(None)
            if self._win and self._win.winfo_exists():
                self.app.schedule_ui(lambda: self._update_sliders(new_vals))
        threading.Thread(target=_worker, daemon=True).start()

    def _update_sliders(self, vals):
        for i, sl in enumerate(self._sliders):
            if i < len(vals) and vals[i] is not None:
                sl._val = vals[i]
                sl._redraw()

    def _position(self, w, h):
        pos, work = get_taskbar_info()
        wl, wt, wr, wb = work
        if pos == "LEFT":
            x, y = wl, wb - h
        elif pos == "TOP":
            x, y = wr - w, wt
        else:
            x, y = wr - w, wb - h
        self._win.geometry(f"+{x}+{y}")

    def _check_outside(self):
        if not self._win or not self._win.winfo_exists():
            return
        pressed = ctypes.windll.user32.GetAsyncKeyState(1) & 0x8000
        if pressed and not self._prev_mouse:
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            fx = self._win.winfo_rootx()
            fy = self._win.winfo_rooty()
            fw = self._win.winfo_width()
            fh = self._win.winfo_height()
            if not (fx <= pt.x <= fx + fw and fy <= pt.y <= fy + fh):
                self._close()
                return
        self._prev_mouse = pressed
        self._win.after(50, self._check_outside)

    def _schedule(self):
        self._cancel()
        if self._win and self._win.winfo_exists():
            self._timer = self._win.after(AUTO_MS, self._close)

    def _cancel(self):
        if self._timer:
            try:
                if self._win:
                    self._win.after_cancel(self._timer)
            except Exception:
                pass
            self._timer = None

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

    def close(self):
        self._close()

    def destroy(self):
        self._close()
        cleanup(self._monitors)
        self._monitors = []


def create_tray_icon(icon_path, on_activate):
    """Create a pystray Icon for brightness control.

    Left-click triggers on_activate. No right-click menu.
    """
    try:
        icon_img = Image.open(icon_path)
    except Exception:
        icon_img = _fallback_icon()

    menu = pystray.Menu(
        pystray.MenuItem("显示", on_activate, default=True, visible=False),
    )

    icon = pystray.Icon("brightness", icon_img, "亮度控制", menu)
    return icon


def _fallback_icon():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    cx = cy = 32
    r = 30
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 200, 50, 180), outline="#ffc864", width=2)
    for a in range(0, 360, 30):
        rad = math.radians(a)
        d.line([cx + int(r * 0.6 * math.cos(rad)), cy + int(r * 0.6 * math.sin(rad)),
                cx + int(r * 0.95 * math.cos(rad)), cy + int(r * 0.95 * math.sin(rad))],
               fill="#ffc864", width=2)
    return img
