# -*- coding: utf-8 -*-
"""
win32ext.py —— Windows 原生能力扩展（纯 ctypes，零第三方依赖）

提供：
  * DPI 感知声明、窗口圆角 / 投影 / 置顶 / 从任务栏隐藏 / 抢前台
  * 系统托盘图标（Shell_NotifyIcon + 独立消息线程）
  * 开机自启（注册表 HKCU Run）
  * 单实例互斥体 + "第二实例把已在跑的窗口叫到前台"的跨进程唤醒
  * 提示音（winsound）、跟随系统的深色/浅色判定
  * 多显示器工作区、虚拟桌面钳制

注意这里不再有"亚克力毛玻璃"：-transparentcolor / Accent 那套透明分层窗口
在拖动时会触发 DWM 崩溃（实测 Fatal Python error → 0xC0000409），
界面改用稳定实色的玻璃拟态，见 ui.py 顶部说明。
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes as wt

try:
    import winsound
except Exception:      # 非 Windows / 精简系统上没这个模块：静音降级，不影响提醒
    winsound = None

try:
    import winreg
except ImportError:  # 非 Windows
    winreg = None

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

WM_USER = 0x0400
WM_DESTROY = 0x0002
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_NULL = 0x0000
WM_CLOSE = 0x0010

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_SHOWTIP = 0x00000080

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040
IDI_APPLICATION = 32512
SM_CXSMICON = 49          # 托盘 / 小图标的名义像素宽（100% DPI 下是 16）

TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_UNCHECKED = 0x00000000

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2

ERROR_ALREADY_EXISTS = 183


# ---------------------------------------------------------------- DPI

DPI_SCALE = 1.0  # 96dpi 基准的缩放因子，enable_dpi_awareness() 后更新


def enable_dpi_awareness():
    """
    声明 DPI 感知。必须在创建任何窗口之前调用。
    不声明的话 Windows 会对整个窗口做位图拉伸，高分屏上文字是糊的。
    返回缩放因子（96dpi 基准，200% 缩放 = 2.0）。
    """
    global DPI_SCALE
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
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


# ---------------------------------------------------------------- 窗口句柄


def get_hwnd(widget):
    """拿到 Tk 窗口真正的 HWND（Tk() 与 Toplevel 的层级不同，两种都兼容）。"""
    try:
        widget.update_idletasks()
    except Exception:
        pass
    wid = widget.winfo_id()
    hwnd = user32.GetParent(wt.HWND(wid))
    if not hwnd:
        hwnd = wid
    return hwnd


def windows_build():
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


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
    """
    给无边框窗口加投影。
    Win11（build >= 22000）的 DWM 本来就会给顶层窗口画一层柔和投影，
    再叠一次 CS_DROPSHADOW 就是两层影、边上多出一道硬边 —— 所以只在老系统上加。
    """
    if not hwnd:
        return False
    if windows_build() >= 22000:
        return True
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


# ---------------------------------------------------------------- 前台与键盘焦点
#
# 无边框（overrideredirect）窗口默认既不进任务栏也拿不到键盘焦点，
# 于是弹窗上绑的"回车=喝了"、设置窗的"Ctrl+S=保存"其实是空绑：
# 焦点还在用户上一刻用的那个程序里，键全打到别的窗口去了。
# 下面这套是标准做法 —— 把自己线程跟当前前台线程临时挂一起，
# 才可能越过 Windows 的前台锁把焦点拿到手（拿不到也别硬抢，见返回值）。


def activate_window(hwnd):
    """
    尽力把这个窗口拉到前台并拿到键盘焦点。
    成功返回 True；系统不让抢（用户正在别处打字）返回 False，调用方别硬来。
    """
    if not hwnd:
        return False
    try:
        SW_SHOW = 5
        user32.ShowWindow(wt.HWND(hwnd), SW_SHOW)
        fg = user32.GetForegroundWindow()
        if fg and fg != wt.HWND(hwnd):
            mine = user32.GetWindowThreadProcessId(wt.HWND(hwnd), None)
            theirs = user32.GetWindowThreadProcessId(fg, None)
            attached = False
            if mine and theirs and mine != theirs:
                attached = bool(user32.AttachThreadInput(ctypes.c_ulong(theirs), ctypes.c_ulong(mine), True))
            try:
                user32.BringWindowToTop(wt.HWND(hwnd))
                ok = bool(user32.SetForegroundWindow(wt.HWND(hwnd)))
            finally:
                if attached:
                    user32.AttachThreadInput(ctypes.c_ulong(theirs), ctypes.c_ulong(mine), False)
        else:
            ok = bool(user32.SetForegroundWindow(wt.HWND(hwnd)))
        user32.SetFocus(wt.HWND(hwnd))
        return bool(ok)
    except Exception:
        return False


def is_foreground(hwnd):
    """当前前台是不是这个窗口（用来判断键位绑定到底有没有通路）。"""
    try:
        return bool(hwnd) and user32.GetForegroundWindow() == wt.HWND(hwnd)
    except Exception:
        return False


# ---------------------------------------------------------------- 跨进程唤醒
#
# 双击 exe 时如果已经有一个在跑，以前的做法是弹一个"已经在运行了"的 MessageBox
# 然后自己退出 —— 用户视角是"我双击了，什么都没发生"。
# 正确姿势：把已在跑的那个实例的设置窗叫到前台。
# RegisterWindowMessageW 用同一个字符串在任何进程里都拿到同一个 ID，
# 所以靠窗口类名找到它的托盘隐藏窗口，再 PostMessage 过去就行。

TRAY_CLASS = "WaterReminderTrayClass"
ACTIVATE_MSG_NAME = "WaterReminder_Activate_v1"
# 约定：收到唤醒消息时，回调收到的命令号（和托盘点击那套命令号同一个通道）
TRAY_ACTIVATE = 90003

_activate_msg = [None]


def activate_message_id():
    """本进程内注册（幂等）跨进程唤醒用的窗口消息号。"""
    if _activate_msg[0] is None:
        try:
            _activate_msg[0] = user32.RegisterWindowMessageW(ACTIVATE_MSG_NAME)
        except Exception:
            _activate_msg[0] = 0
    return _activate_msg[0] or 0


def find_tray_window(class_name=TRAY_CLASS):
    """找到已在跑的那个实例的隐藏消息窗口；没找到返回 0。"""
    try:
        return user32.FindWindowW(class_name, None) or 0
    except Exception:
        return 0


def wake_running_instance(class_name=TRAY_CLASS):
    """
    第二实例调用：把第一实例的窗口叫出来。
    成功 True（那边走托盘线程回调 → 主线程开设置窗）；False 表示其实没在跑。
    """
    hwnd = find_tray_window(class_name)
    if not hwnd:
        return False
    msg = activate_message_id()
    if not msg:
        return False
    try:
        return bool(user32.PostMessageW(wt.HWND(hwnd), ctypes.c_uint(msg), 0, 0))
    except Exception:
        return False


# ---------------------------------------------------------------- 提示音
#
# 只有视觉提醒的话，人离开工位那一刻必然漏（这也是"到点不提醒"投诉的一半来源）。
# winsound 是标准库，不占体积、不联网；异步播放，绝不因为响一声就把主循环卡住。

_SOUND_ALIASES = ("MailBeep", "SystemAsterisk", "SystemExclamation")


def play_cue(alias="MailBeep"):
    """播一声系统提示音（异步）。系统里没这个声音就往下退，最后退到 MessageBeep。"""
    if winsound is None:
        return False
    SND_ASYNC = 0x0001
    SND_ALIAS = 0x0004
    chain = [alias] + [a for a in _SOUND_ALIASES if a != alias]
    for name in chain:
        try:
            winsound.PlaySound(name, SND_ALIAS | SND_ASYNC)
            return True
        except Exception:
            continue
    try:
        winsound.MessageBeep(-1)      # MB_OK
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 深色 / 浅色
#
# 界面默认跟随系统的"应用使用深色模式"，这样在浅色任务栏的桌面上不会突兀。
# 读不到（老系统 / 注册表被精简）算深色，因为这套配色本来就是按深色调的。


def apps_use_light_theme():
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            0, winreg.KEY_READ)
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return bool(int(value))
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False



def set_topmost(hwnd, on=True):
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOSIZE = 0x0001
    SWP_NOMOVE = 0x0002
    SWP_NOACTIVATE = 0x0010
    try:
        user32.SetWindowPos(
            wt.HWND(hwnd),
            wt.HWND(HWND_TOPMOST if on else HWND_NOTOPMOST),
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE,
        )
    except Exception:
        pass


def hide_from_taskbar(hwnd):
    """从任务栏隐藏（只留托盘）。"""
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080
    try:
        get_ptr = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
        set_ptr = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
        get_ptr.restype = ctypes.c_longlong
        set_ptr.restype = ctypes.c_longlong
        style = get_ptr(wt.HWND(hwnd), GWL_EXSTYLE)
        set_ptr(wt.HWND(hwnd), GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
    except Exception:
        pass


def work_area(x=None, y=None):
    """
    任务栏之外的工作区 (left, top, right, bottom)。
    默认按**当前光标所在显示器**取 —— 双屏下弹窗才会出现在人正在用的那块屏。
    （旧实现用 SPI_GETWORKAREA，它只有主屏，副屏干活的人收不到提醒）
    """
    return monitor_work_area(x, y)


# ---------------------------------------------------------------- 单实例


class SingleInstance(object):
    """命名互斥体，防止重复启动。"""

    def __init__(self, name):
        self._handle = None
        self.already_running = False
        try:
            kernel32.CreateMutexW.restype = wt.HANDLE
            self._handle = kernel32.CreateMutexW(None, True, name)
            self.already_running = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
        except Exception:
            self.already_running = False

    def release(self):
        if self._handle:
            try:
                kernel32.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


# ---------------------------------------------------------------- 开机自启

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_path():
    """真正要开机拉起来的那个可执行文件。"""
    return os.path.abspath(sys.executable)


def autostart_command():
    """
    写进注册表的完整命令行。
    注意别把 autostart_path() 的结果再套一层引号：源码运行时它是
    '"python.exe" "main.py"'，外面再加引号会变成 ""python.exe" "main.py""，
    开机解析直接失败 —— 界面上开关是亮的，实际什么都没启动。
    """
    exe = autostart_path()
    if getattr(sys, "frozen", False):
        return '"%s" --minimized' % exe
    return '"%s" "%s" --minimized' % (exe, os.path.abspath(sys.argv[0]))


def set_autostart(enabled, app_name="WaterReminder"):
    if winreg is None:
        return False
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_WRITE)
    except Exception:
        return False
    try:
        if enabled:
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, app_name)
            except FileNotFoundError:
                pass
        return True
    except Exception:
        return False
    finally:
        try:
            winreg.CloseKey(key)
        except Exception:
            pass


def _autostart_value(app_name="WaterReminder"):
    if winreg is None:
        return ""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, app_name)
        winreg.CloseKey(key)
        return str(value or "")
    except Exception:
        return ""


def autostart_target(value):
    """从 '"C:\\...\\WaterReminder.exe" --minimized' 里取出程序路径。"""
    v = (value or "").strip()
    if v.startswith('"'):
        end = v.find('"', 1)
        return v[1:end] if end > 0 else v.strip('"')
    return v.split(" ")[0] if v else ""


def get_autostart(app_name="WaterReminder"):
    """
    注册表里有这个值 ≠ 真的能自启。
    exe 被挪走/删掉后 Windows 只会静默失败，界面上开关却还是亮的 ——
    用户以为"开了自启"，其实开机什么都没有，这正是"到点不提醒"的另一条路。
    所以这里顺带校验路径还存在。
    """
    v = _autostart_value(app_name)
    if not v:
        return False
    target = autostart_target(v)
    if target and not os.path.exists(target):
        return False
    return True


# ---------------------------------------------------------------- 托盘


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wt.HANDLE),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uTimeout", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wt.HANDLE),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wt.HWND),
        ("message", ctypes.c_uint),
        ("wParam", wt.WPARAM),
        ("lParam", wt.LPARAM),
        ("time", wt.DWORD),
        ("pt", POINT),
        ("lPrivate", wt.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, wt.HWND, ctypes.c_uint, wt.WPARAM, wt.LPARAM
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HANDLE),
        ("hIcon", wt.HANDLE),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


# ---------------------------------------------------------------- ctypes 签名
#
# 64 位下 ctypes 不声明签名就按 C int 处理参数和返回值，句柄会被截断：
#   * 模块基址 0x7FF6xxxxxxxx 截成 32 位 → 当 hInstance 用直接 access violation
#   * HMENU / HWND 同理
# 所以这里把本模块用到的 API 一次性声明齐；调用点不再重复声明。


def _sig(dll, name, restype, argtypes):
    """给单个 API 打签名；函数在系统上不存在时静默跳过（兼容老 Windows）。"""
    try:
        fn = getattr(dll, name)
    except AttributeError:
        return
    try:
        fn.restype = restype
        fn.argtypes = argtypes
    except Exception:
        pass


_H = ctypes.c_void_p          # 通用句柄 / 指针
_LP = ctypes.c_longlong       # LONG_PTR
_UI = ctypes.c_uint
_I = ctypes.c_int
_W = ctypes.c_wchar_p
_B = wt.BOOL
_UV = ctypes.c_void_p         # 指向结构体的指针

_sig(user32, "GetParent", wt.HWND, [wt.HWND])
_sig(user32, "SetProcessDPIAware", _B, [])
_sig(user32, "GetDpiForSystem", _UI, [])
_sig(user32, "GetSystemMetrics", ctypes.c_int, [ctypes.c_int])
_sig(user32, "SetWindowPos", _B, [wt.HWND, _H, _I, _I, _I, _I, _UI])
_sig(user32, "GetWindowLongPtrW", _LP, [wt.HWND, _I])
_sig(user32, "SetWindowLongPtrW", _LP, [wt.HWND, _I, _LP])
_sig(user32, "GetClassLongPtrW", ctypes.c_ulonglong, [wt.HWND, _I])
_sig(user32, "SetClassLongPtrW", ctypes.c_ulonglong, [wt.HWND, _I, ctypes.c_ulonglong])
_sig(user32, "SystemParametersInfoW", _B, [_UI, _UI, _UV, _UI])
_sig(user32, "MonitorFromPoint", _H, [POINT, _UI])
_sig(user32, "GetMonitorInfoW", _B, [_H, _UV])
_sig(user32, "GetCursorPos", _B, [_UV])
_sig(user32, "GetClientRect", _B, [wt.HWND, _UV])
_sig(user32, "GetWindowRect", _B, [wt.HWND, _UV])
_sig(user32, "PostMessageW", _B, [wt.HWND, _UI, wt.WPARAM, wt.LPARAM])
_sig(user32, "SendMessageW", ctypes.c_longlong, [wt.HWND, _UI, wt.WPARAM, wt.LPARAM])
_sig(user32, "SetForegroundWindow", _B, [wt.HWND])
_sig(user32, "RegisterClassW", ctypes.c_ushort, [_UV])   # ATOM
_sig(user32, "CreateWindowExW", wt.HWND,
     [_UI, _W, _W, _UI, _I, _I, _I, _I, wt.HWND, wt.HWND, _H, _UV])
_sig(user32, "DefWindowProcW", ctypes.c_longlong, [wt.HWND, _UI, wt.WPARAM, wt.LPARAM])
_sig(user32, "GetMessageW", _I, [_UV, wt.HWND, _UI, _UI])
_sig(user32, "DispatchMessageW", ctypes.c_longlong, [_UV])
_sig(user32, "TranslateMessage", _B, [_UV])
_sig(user32, "PostQuitMessage", None, [_I])
_sig(user32, "DestroyWindow", _B, [wt.HWND])
_sig(user32, "CreatePopupMenu", _H, [])
_sig(user32, "DestroyMenu", _B, [_H])
_sig(user32, "AppendMenuW", _B, [_H, _UI, ctypes.c_void_p, _W])
_sig(user32, "TrackPopupMenu", _I, [_H, _UI, _I, _I, _I, wt.HWND, _UV])
_sig(user32, "LoadImageW", _H, [_H, _W, _I, _I, _I, _UI])
_sig(user32, "LoadIconW", _H, [_H, _W])
_sig(user32, "DestroyIcon", _B, [_H])
_sig(shell32, "Shell_NotifyIconW", _B, [_UI, _UV])
_sig(kernel32, "GetModuleHandleW", wt.HMODULE, [_W])
_sig(kernel32, "CreateMutexW", _H, [_UV, _B, _W])
_sig(kernel32, "CloseHandle", _B, [_H])
_sig(kernel32, "ReleaseMutex", _B, [_H])
_sig(user32, "RegisterWindowMessageW", _UI, [_W])
_sig(user32, "FindWindowW", wt.HWND, [_W, _W])
_sig(user32, "ShowWindow", _B, [wt.HWND, _I])
_sig(user32, "GetForegroundWindow", wt.HWND, [])
_sig(user32, "GetWindowThreadProcessId", _UI, [wt.HWND, _UV])
_sig(user32, "AttachThreadInput", _B, [wt.DWORD, wt.DWORD, _B])
_sig(user32, "BringWindowToTop", _B, [wt.HWND])
_sig(user32, "SetFocus", wt.HWND, [wt.HWND])


# ---------------------------------------------------------------- 多显示器
#
# SPI_GETWORKAREA 只返回**主显示器**去掉任务栏的区域。双屏机器上如果用它
# 定位提醒弹窗，弹窗永远只出现在主屏 —— 副屏干活的人看不见，等于"没提醒"。

MONITOR_DEFAULTTONEAREST = 0x00000002


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("rcMonitor", wt.RECT),
        ("rcWork", wt.RECT),
        ("dwFlags", wt.DWORD),
    ]


def monitor_work_area(x=None, y=None):
    """
    取包含点 (x, y) 的显示器工作区；不传坐标就用当前光标所在屏。
    拿不到时回落到 SPI_GETWORKAREA（单屏机器上两者等价）。
    """
    try:
        if x is None or y is None:
            pt = POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
        hmon = user32.MonitorFromPoint(POINT(int(x), int(y)),
                                       MONITOR_DEFAULTTONEAREST)
        if hmon:
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if user32.GetMonitorInfoW(wt.HANDLE(hmon), ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return _primary_work_area()


def virtual_desktop():
    """整个虚拟桌面（所有显示器拼起来）的边界。"""
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
    SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
    try:
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return x, y, x + w, y + h
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def clamp_into_view(x, y, w, h, margin=40):
    """
    把"窗口左上角应放在哪里"钳制到虚拟桌面内，并保证至少 margin 像素可见。
    无边框窗口不进任务栏也不进 Alt-Tab，一旦被拖出屏幕就再也点不回来了，
    所以拖动和恢复位置都必须过这一道。
    """
    vx0, vy0, vx1, vy1 = virtual_desktop()
    w = max(0, w)
    h = max(0, h)
    min_x = vx0 - w + margin
    max_x = vx1 - margin
    min_y = vy0
    max_y = vy1 - margin
    return max(min_x, min(int(x), max_x)), max(min_y, min(int(y), max_y))


def _primary_work_area():
    try:
        rect = wt.RECT()
        SPI_GETWORKAREA = 0x0030
        if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            if rect.right > rect.left:
                return rect.left, rect.top, rect.right, rect.bottom
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)



class TrayIcon(object):
    """
    系统托盘图标。自带一个隐藏窗口 + 消息线程（不占用 Tk 主线程）。

    menu_items: [(命令ID, 显示文本), ...]，用 None 表示分隔线
    callback:   回调在托盘线程中执行，跨线程操作 UI 请自行投递到主线程
    """

    def __init__(self, icon_path, tooltip, menu_items, callback):
        self.icon_path = icon_path
        self.tooltip = tooltip or ""
        self.menu_items = menu_items or []
        self.callback = callback
        self.hwnd = None
        self._thread = None
        self._wndproc_ref = None
        self._ready = threading.Event()
        # 第二个实例双击 exe 时往这个消息号上 PostMessage，我们收到就回调"打开设置"。
        # 在这里（主线程）注册一次就行：消息号是系统级的，跨进程一致。
        self._activate_msg = activate_message_id()
        # HICON 只加载一次。以前每次刷新都 LoadImageW 一遍又从不 DestroyIcon，
        # 实测每刷一次漏 1 个 GDI + 3 个 USER 对象（见 tests/drag_probe/tray_leak.py）。
        # 这程序是常驻的，一天十几次刷新攒几个月就会顶到 USER 对象配额。
        self._hicon = None

    # ---- 生命周期 ----
    def start(self):
        self._thread = threading.Thread(target=self._run, name="TrayThread")
        self._thread.daemon = True
        self._thread.start()
        return self._ready.wait(timeout=5)

    def stop(self):
        if self.hwnd:
            try:
                user32.PostMessageW(wt.HWND(self.hwnd), WM_DESTROY, 0, 0)
            except Exception:
                pass

    def update(self, tooltip=None, icon_path=None):
        """内容真变了才发消息刷新，避免无谓的 Shell_NotifyIcon 往返。"""
        changed = False
        if tooltip is not None and tooltip[:127] != self.tooltip:
            self.tooltip = tooltip[:127]
            changed = True
        if icon_path is not None and icon_path != self.icon_path:
            self.icon_path = icon_path
            self._drop_icon()
            changed = True
        if changed and self.hwnd:
            try:
                user32.PostMessageW(wt.HWND(self.hwnd), WM_USER + 2, 0, 0)
            except Exception:
                pass

    def set_menu(self, menu_items):
        """
        换托盘右键菜单内容（菜单是弹出时现建的，所以随时改都生效）。
        项可以是 (命令ID, 文本)、(命令ID, 文本, 是否打勾)，None 是分隔线。
        """
        self.menu_items = menu_items or []

    # ---- 内部 ----
    def _drop_icon(self):
        if self._hicon:
            try:
                user32.DestroyIcon(wt.HANDLE(self._hicon))
            except Exception:
                pass
            self._hicon = None

    def _load_icon(self):
        """加载一次并缓存；返回同一个 HICON 供后续 NIM_MODIFY 复用。"""
        if self._hicon:
            return self._hicon
        h = None
        if self.icon_path and os.path.exists(self.icon_path):
            # 按托盘实际像素尺寸去多尺寸 .ico 里挑那一帧。
            # 图省事用 LR_DEFAULTSIZE 会拿到 32px 再被系统缩到 16，
            # 水滴边缘直接糊成一团。
            side = user32.GetSystemMetrics(SM_CXSMICON)
            flags = LR_LOADFROMFILE | (LR_DEFAULTSIZE if side <= 0 else 0)
            h = user32.LoadImageW(
                None,
                self.icon_path,
                IMAGE_ICON,
                side,
                side,
                flags,
            )
        if not h:
            # 资源 ID 形式的图标：直接传整数（MAKEINTRESOURCE），不能转成字符串指针
            h = user32.LoadIconW(None, IDI_APPLICATION)
        self._hicon = h
        return h

    def _fill(self, add=True):
        data = NOTIFYICONDATA()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        data.hWnd = wt.HWND(self.hwnd)
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        data.uCallbackMessage = WM_USER + 1
        data.hIcon = self._load_icon()
        data.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(
            NIM_ADD if add else NIM_MODIFY, ctypes.byref(data)
        )
        return data

    def _show_menu(self):
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        for item in self.menu_items:
            if item is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            if len(item) >= 3:
                cmd_id, text = item[0], item[1]
                flags = MF_STRING | (MF_CHECKED if item[2] else MF_UNCHECKED)
            else:
                cmd_id, text = item[0], item[1]
                flags = MF_STRING
            user32.AppendMenuW(menu, flags, cmd_id, text)
        try:
            user32.SetForegroundWindow(wt.HWND(self.hwnd))
            pos = POINT()
            user32.GetCursorPos(ctypes.byref(pos))
            cmd = user32.TrackPopupMenu(
                menu,
                TPM_RETURNCMD | TPM_NONOTIFY,
                pos.x,
                pos.y,
                0,
                wt.HWND(self.hwnd),
                None,
            )
            # 消除菜单残留（MSDN 建议）
            user32.PostMessageW(wt.HWND(self.hwnd), WM_NULL, 0, 0)
        finally:
            user32.DestroyMenu(menu)
        if cmd and self.callback:
            try:
                self.callback(cmd)
            except Exception:
                pass

    @staticmethod
    def _def_proc(hwnd, msg, wparam, lparam):
        # 签名已在模块顶部统一声明，这里不再每条消息重设一遍
        return user32.DefWindowProcW(wt.HWND(hwnd), ctypes.c_uint(msg),
                                     wt.WPARAM(wparam), wt.LPARAM(lparam))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if self._activate_msg and msg == self._activate_msg:
            # 第二个实例被双击起来了 —— 等于用户想看这个程序，把设置窗叫出来
            if self.callback:
                try:
                    self.callback(TRAY_ACTIVATE)
                except Exception:
                    pass
            return 0
        if msg == WM_USER + 1:  # 托盘消息
            if lparam in (WM_RBUTTONUP, 0x0204):  # 右键
                self._show_menu()
            elif lparam == WM_LBUTTONDBLCLK:
                if self.callback:
                    self.callback(90001)  # 约定：双击 = 打开设置
            elif lparam == WM_LBUTTONUP:
                if self.callback:
                    self.callback(90002)  # 单击 = 立刻提醒/查看
        elif msg == WM_USER + 2:  # 刷新
            self._fill(add=False)
        elif msg == WM_DESTROY:
            data = NOTIFYICONDATA()
            data.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            data.hWnd = wt.HWND(hwnd)
            data.uID = 1
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            self._drop_icon()   # 缓存的 HICON 在这里归还
            user32.PostQuitMessage(0)
        return self._def_proc(hwnd, msg, wparam, lparam)

    def _run(self):
        try:
            hinstance = kernel32.GetModuleHandleW(None)
            class_name = TRAY_CLASS
            self._wndproc_ref = WNDPROC(self._wndproc)
            wc = WNDCLASS()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = wt.HANDLE(hinstance)
            wc.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(wc)):
                if ctypes.get_last_error() != 1410:  # 已注册
                    self._ready.set()
                    return
            hwnd = user32.CreateWindowExW(
                0,
                class_name,
                "WaterReminder",
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                wt.HANDLE(hinstance),
                None,
            )
            if not hwnd:
                self._ready.set()
                return
            self.hwnd = hwnd
            self._fill(add=True)
            self._ready.set()

            msg = MSG()
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret in (0, -1):
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self._ready.set()
