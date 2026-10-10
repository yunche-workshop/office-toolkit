# -*- coding: utf-8 -*-
"""
win32ext.py —— Windows 原生增强（从 water-reminder 精简而来）

只保留批量重命名用得到的部分：DPI 感知、窗口句柄、圆角、投影、多屏工作区。
托盘 / 开机自启 / 亚克力那些不要 —— 亚克力跟透明分层窗口不能一起用，
而且这工具是普通窗口，不需要。
"""

import ctypes
import sys
import ctypes.wintypes as wt

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
shcore = ctypes.windll.shcore

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

DPI_SCALE = 1.0


# ---------------------------------------------------------------- DPI


def enable_dpi_awareness():
    """
    声明 DPI 感知，必须在创建任何窗口之前调用。
    不声明的话 Windows 会对整个窗口做位图拉伸，200% 屏上文字是糊的。
    返回缩放因子（96dpi 基准，200% 缩放 = 2.0）。
    """
    global DPI_SCALE
    try:
        shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    try:
        user32.GetDpiForSystem.restype = ctypes.c_uint
        dpi = user32.GetDpiForSystem()
        if dpi and dpi >= 96:
            DPI_SCALE = dpi / 96.0
    except Exception:
        pass
    return DPI_SCALE


def px(value):
    """逻辑像素 -> 物理像素。窗口尺寸/坐标一律过一遍这个函数。"""
    return int(round(value * DPI_SCALE))


# ---------------------------------------------------------------- 窗口句柄


def get_hwnd(widget):
    """拿到 Tk 窗口真正的 HWND。"""
    try:
        widget.update_idletasks()
    except Exception:
        pass
    wid = widget.winfo_id()
    hwnd = user32.GetParent(wt.HWND(wid))
    if not hwnd:
        hwnd = wid
    return hwnd


def set_round_corner(hwnd, mode=DWMWCP_ROUND):
    """Win11 圆角窗口；老系统静默失败。"""
    if not hwnd:
        return False
    try:
        pref = ctypes.c_int(mode)
        dwmapi.DwmSetWindowAttribute(
            wt.HWND(hwnd),
            ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
            ctypes.byref(pref),
            ctypes.sizeof(pref),
        )
        return True
    except Exception:
        return False


def set_window_shadow(hwnd):
    """给窗口加投影（CS_DROPSHADOW），让它"浮"起来。"""
    if not hwnd:
        return False
    GCL_STYLE = -26
    CS_DROPSHADOW = 0x00020000
    try:
        get_fn = getattr(user32, "GetClassLongPtrW", None) or user32.GetClassLongW
        set_fn = getattr(user32, "SetClassLongPtrW", None) or user32.SetClassLongW
        get_fn.restype = ctypes.c_ulonglong
        set_fn.restype = ctypes.c_ulonglong
        style = get_fn(wt.HWND(hwnd), GCL_STYLE)
        set_fn(wt.HWND(hwnd), GCL_STYLE, style | CS_DROPSHADOW)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 屏幕


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def monitor_work_area(x=None, y=None):
    """
    取「指定坐标所在显示器」的工作区（不含任务栏）。
    SPI_GETWORKAREA 只给主屏，双屏会算错，所以按坐标查监视器。
    """
    if x is None or y is None:
        x, y = 0, 0
    try:
        pt = wt.POINT(int(x), int(y))
        hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW.restype = ctypes.c_int
        user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.POINTER(MONITORINFO)]
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    # 兜底：整个虚拟桌面
    return (user32.GetSystemMetrics(76),   # SM_XVIRTUALSCREEN
            user32.GetSystemMetrics(77),   # SM_YVIRTUALSCREEN
            user32.GetSystemMetrics(78),   # SM_CXVIRTUALSCREEN
            user32.GetSystemMetrics(79))   # SM_CYVIRTUALSCREEN


def clamp_into_view(x, y, w, h, margin=40):
    """把窗口位置夹进可见工作区，避免拖到屏幕外找不着。"""
    left, top, right, bottom = monitor_work_area(x, y)
    m = px(margin)
    x = max(left + m, min(x, right - w - m))
    y = max(top + m, min(y, bottom - h - m))
    return x, y


def windows_build():
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0
