"""Desktop Goose-style ground for Clawde: the tops of visible windows and the
taskbar are floors he can stand on, fall to, and hop up onto."""
import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowLongW.argtypes = [wt.HWND, ctypes.c_int]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsIconic.argtypes = [wt.HWND]
dwmapi.DwmGetWindowAttribute.argtypes = [wt.HWND, wt.DWORD, ctypes.c_void_p, wt.DWORD]

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
MY_PID = os.getpid()


def windows():
    """Visible top-level windows of other apps, front to back, as (left, top, right, bottom)."""
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == MY_PID:
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE):
            return True
        cloaked = wt.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True                         # hidden UWP / other-desktop windows
        r = wt.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r), ctypes.sizeof(r)):
            user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 120 and r.bottom - r.top > 60:
            out.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def ledges(area, rects=None):
    """Window tops you can actually see (not covered by a window in front), as (left, right, y)."""
    l, t, r, b = area
    rects = windows() if rects is None else rects
    found = []
    for i, (wl, wt_, wr, wb) in enumerate(rects):
        if wt_ <= t + 4 or wt_ >= b - 40:
            continue                            # maximised, or off the bottom of the screen
        # the parts of this window's top edge that no window in front of it covers
        spans = [(max(wl, l), min(wr, r))]
        for (fl, ft, fr, fb) in rects[:i]:
            if ft <= wt_ - 2 <= fb:
                spans = [piece for a, z in spans for piece in ((a, min(z, fl)), (max(a, fr), z)) if piece[1] - piece[0] > 0]
        found += [(a, z, wt_) for a, z in spans if z - a >= 60]
    return found


def floor_below(x, feet_y, area, rects=None):
    """The highest floor at or below feet_y under x: a visible window top, or the taskbar."""
    best = area[3]
    for a, z, y in ledges(area, rects):
        if a + 8 <= x <= z - 8 and feet_y - 3 <= y < best:
            best = y
    return best


def ledges_above(x, feet_y, area, reach=320, rects=None):
    """Window tops a little above Clawde and within hopping distance."""
    out = []
    for a, z, y in ledges(area, rects):
        if feet_y - reach <= y <= feet_y - 40 and a - 220 <= x <= z + 220:
            out.append((a, z, y))
    return out
