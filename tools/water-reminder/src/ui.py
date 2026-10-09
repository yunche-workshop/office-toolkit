# -*- coding: utf-8 -*-
"""
ui.py —— 玻璃质感界面

设计参考 glassmorphism 通行规范（半透明卡片 + 1px 亮边 + 柔和投影）与
Windows 11 的 Mica 视觉：

  * Tk Canvas 没有 alpha 色，所以"半透明白"用等效混色实现：
    卡片色 = 玻璃底色 与 白色 按比例混合，视觉上等同于叠在玻璃上的半透明白
  * 层次：玻璃底 < 卡片 < 控件 < 主按钮（强调色只给主要动作）
  * 进程必须先 win32ext.enable_dpi_awareness()，否则高分屏整体发糊
  * 布局坐标一律传 96dpi 设计值，由 self.u() 换算（只换算一次！）
"""

import datetime as dt
import math
import tkinter as tk
from tkinter import font as tkfont

import core
import win32ext

TRANSPARENT_KEY = "#010203"   # 已弃用：真亚克力需透明分层窗口，拖动会触发 DWM 崩溃
GLASS_TINT = (24, 24, 30)     # 亚克力叠加色（仅作配色基准，不再套用）
FALLBACK_BG = "#171B22"       # 稳定实底窗色（不再依赖亚克力，绝不黑屏/闪退）

ACCENT = "#4FC3F7"            # 强调色：水（进度条/开关/图标）
BTN_BLUE = "#1E88E5"          # 主按钮：比强调色沉稳一档，白字
ACCENT_LIGHT = "#8BE9FF"
ACCENT_DEEP = "#2196F3"
OK_GREEN = "#7BD88F"


def _pick_font_family():
    try:
        fams = set(tkfont.families())
    except Exception:
        return "Microsoft YaHei"
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "SimHei"):
        if name in fams:
            return name
    return "System"


FONT_FAMILY = _pick_font_family()


def F(size, weight="normal"):
    """像素字体（负号 = 像素单位），按 DPI 缩放因子放大。"""
    px = max(9, int(round(size * win32ext.DPI_SCALE * 4.0 / 3.0)))
    return (FONT_FAMILY, -px, weight)


_FONT_CACHE = {}


def font_obj(size, weight="normal"):
    """
    拿一个可复用的 tkfont.Font（用来量文字宽度）。
    实测每刷新新建一个 Font 并不会真的漏句柄，但每次 measure 都重建对象没必要，
    进度条那个大数字刷新频率最高，这里缓存一次。
    """
    key = (size, weight)
    f = _FONT_CACHE.get(key)
    if f is None:
        f = _FONT_CACHE[key] = tkfont.Font(font=F(size, weight))
    return f


def rgb_mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def to_hex(rgb):
    return "#%02X%02X%02X" % rgb


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def fmt_int(n):
    """12345 -> '12,345'（千分位）。毫升数上千后不带分隔符很难一眼读出量级。"""
    try:
        return "{:,}".format(int(n))
    except Exception:
        return str(n)


def weekday_label(date_key):
    """'2026-10-08' -> '三'；解析失败返回空串。"""
    try:
        y, m, d = (int(x) for x in date_key.split("-"))
        return "一二三四五六日"[dt.date(y, m, d).weekday()]
    except Exception:
        return ""


# 进度条渐变色表：预先算好 32 档，画的时候只查表，不再每段做一次混色
GRADIENT_STEPS = 32
_GRADIENT = [
    to_hex(rgb_mix(hex_to_rgb(ACCENT_LIGHT), hex_to_rgb(ACCENT_DEEP),
                   i / float(GRADIENT_STEPS - 1)))
    for i in range(GRADIENT_STEPS)
]


def make_palette(glass):
    """
    生成配色。半透明一律换算成"叠在底色上的等效实色"。
    采用稳定的玻璃拟态（glassmorphism）方案：实色窗口主体 + 亮边 + 顶部高光，
    不依赖任何透明分层窗口，因此不会黑屏、不会点击穿透、拖动不会闪退。
    """
    base = GLASS_TINT if glass else hex_to_rgb(FALLBACK_BG)
    white = (255, 255, 255)
    black = (0, 0, 0)

    def over(color, alpha):
        return to_hex(rgb_mix(base, color, alpha))

    return {
        "text": to_hex(rgb_mix(base, white, 0.94)),      # 主文字
        "sub": to_hex(rgb_mix(base, white, 0.62)),        # 次要文字
        "faint": to_hex(rgb_mix(base, white, 0.34)),      # 极弱文字
        "surface": over(white, 0.055),                   # 窗口主体（吸收点击）
        "surface_edge": over(white, 0.16),                # 玻璃亮边
        "surface_hi": over(white, 0.30),                  # 顶部高光带
        "card": over(white, 0.10),                        # 卡片填充
        "card_edge": over(white, 0.14),                   # 卡片亮边
        "card_hi": over(white, 0.20),                     # 顶部高光
        "ctrl": over(white, 0.07),                        # 幽灵按钮
        "ctrl_hover": over(white, 0.14),
        "ctrl_press": over(black, 0.18),
        "ctrl_edge": over(white, 0.16),
        "field": over(black, 0.34),                       # 输入框（凹陷）
        "field_edge": over(white, 0.10),
        "track": over(white, 0.08),                       # 进度槽
        "toggle_off": over(white, 0.13),
        "line": over(white, 0.16),                        # 分隔线
    }


def round_rect_points(x0, y0, x1, y1, r):
    """圆角矩形顶点（顺时针），配合 smooth=True。"""
    r = max(0, min(r, (x1 - x0) / 2.0, (y1 - y0) / 2.0))
    pts = []
    corners = (
        (x1 - r, y1 - r, 0.0, 90.0),
        (x0 + r, y1 - r, 90.0, 180.0),
        (x0 + r, y0 + r, 180.0, 270.0),
        (x1 - r, y0 + r, 270.0, 360.0),
    )
    for cx, cy, a0, a1 in corners:
        for i in range(8):
            a = math.radians(a0 + (a1 - a0) * i / 7.0)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


def drop_points(cx, cy, size):
    """水滴形状顶点（下部圆 + 上部收成尖），用于画小图标。"""
    r = size * 0.30
    circle_cy = cy + size * 0.20
    top = cy - size * 0.50
    bottom = circle_cy + r
    left, right = [], []
    steps = 26
    for i in range(steps + 1):
        y = top + (bottom - top) * i / float(steps)
        if y <= circle_cy:
            t = (y - top) / max(1e-6, (circle_cy - top))
            w = r * (t ** 0.62)
        else:
            dy = y - circle_cy
            w = math.sqrt(max(0.0, r * r - dy * dy))
        left.append((cx - w, y))
        right.append((cx + w, y))
    return left + right[::-1]


# ---------------------------------------------------------------- 玻璃窗口


class GlassWindow(tk.Toplevel):
    """无边框 + 稳定玻璃拟态背景的窗口。width/height 传 96dpi 设计值。"""

    PAD = 24
    MAX_SEGMENTS = 24   # 渐变进度条最多画这么多段

    def __init__(self, master, width, height, tint=GLASS_TINT, alpha=196,
                 topmost=False, center=False):
        tk.Toplevel.__init__(self, master)
        self.S = win32ext.DPI_SCALE
        self.u = lambda v: int(v * self.S)
        self.width = self.u(width)
        self.height = self.u(height)
        self.overrideredirect(True)
        # 稳定方案：纯实色窗口。不使用 -transparentcolor / 亚克力，
        # 避免"透明分层窗口 + DWM 合成"在拖动时崩溃（一挪就闪退）。
        self.glass = False
        self.configure(bg=FALLBACK_BG)
        self.canvas = tk.Canvas(
            self,
            width=self.width,
            height=self.height,
            bg=FALLBACK_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.update_idletasks()

        self.hwnd = win32ext.get_hwnd(self)
        # 圆角 + 投影均为系统原生效果，单独使用稳定，不会引起闪退
        win32ext.set_round_corner(self.hwnd)
        win32ext.set_window_shadow(self.hwnd)
        self.C = make_palette(self.glass)
        self._draw_surface()

        if center:
            self.center_on_screen()
        if topmost:
            win32ext.set_topmost(self.hwnd, True)

    def _draw_surface(self):
        """
        稳定的玻璃拟态实体卡片：实色主体（吸收全部点击，绝不穿透）
        + 1px 亮边 + 顶部高光带，模拟玻璃受光质感。
        """
        c = self.C
        r = self.u(16)
        W, H = self.width, self.height
        self.canvas.create_polygon(
            round_rect_points(1, 1, W - 1, H - 1, r),
            smooth=True,
            fill=c["surface"],
            outline=c["surface_edge"],
            width=max(1, int(self.S)),
            tags=("surface",),
        )
        # 顶部高光带：模拟玻璃从上往下受光的微亮过渡
        self.canvas.create_polygon(
            round_rect_points(self.u(5), self.u(3), W - self.u(5), self.u(11),
                             self.u(8)),
            smooth=True,
            fill=c["surface_hi"],
            outline="",
        )

    def center_on_screen(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = int((sw - self.width) / 2)
        y = int((sh - self.height) / 2)
        self.geometry("+%d+%d" % (x, y))

    def place_bottom_right(self, margin=16):
        """
        放到"当前光标所在那块屏"的右下角。
        work_area() 早期用的是 SPI_GETWORKAREA，只有主屏 —— 双屏机器上
        人在副屏干活，提醒永远弹在没人看的主屏上，看起来就像"从来不提醒"。
        """
        left, top, right, bottom = win32ext.work_area()
        self.update_idletasks()
        x = right - self.width - self.u(margin)
        y = bottom - self.height - self.u(margin)
        return self._move_to(x, y)

    # -- 绘制（坐标传 96dpi 设计值）--
    def card(self, x0, y0, x1, y1, r=18):
        """玻璃卡片：半透明白填充 + 1px 亮边 + 顶部高光。"""
        c = self.C
        self.canvas.create_polygon(
            round_rect_points(self.u(x0), self.u(y0), self.u(x1), self.u(y1),
                              self.u(r)),
            smooth=True,
            fill=c["card"],
            outline=c["card_edge"],
            width=max(1, int(self.S)),
        )
        self.canvas.create_line(
            self.u(x0 + r * 0.6), self.u(y0 + 1),
            self.u(x1 - r * 0.6), self.u(y0 + 1),
            fill=c["card_hi"],
        )

    def text(self, x, y, content, size=12, color=None, weight="normal",
             anchor="w", width=None):
        color = color or self.C["text"]
        if width is not None:
            width = self.u(width)
        return self.canvas.create_text(
            self.u(x),
            self.u(y),
            text=content,
            font=F(size, weight),
            fill=color,
            anchor=anchor,
            width=width,
        )

    def line(self, x0, y0, x1, y1):
        self.canvas.create_line(self.u(x0), self.u(y0), self.u(x1), self.u(y1),
                                fill=self.C["line"])

    def drop(self, cx, cy, size, color=ACCENT):
        """画一个水滴小图标。"""
        pts = []
        for px, py in drop_points(self.u(cx), self.u(cy), self.u(size)):
            pts += [px, py]
        self.canvas.create_polygon(pts, smooth=True, fill=color, outline="")

    def progress(self, x0, y0, x1, y1, ratio):
        """
        进度条：暗槽 + 渐变填充，两端收圆。

        段数封顶：以前按 3px 一段算，200% 缩放的宽条一次就画上百个矩形，
        设置窗口每刷新一次就来一遍（刷新还会被记录写入触发）。
        渐变色表预先算好，这里只查表。
        """
        x0, y0, x1, y1 = self.u(x0), self.u(y0), self.u(x1), self.u(y1)
        h = y1 - y0
        r = h / 2.0
        self.canvas.create_polygon(
            round_rect_points(x0, y0, x1, y1, r),
            smooth=True,
            fill=self.C["track"],
            outline="",
            tags=("progress_track",),
        )
        ratio = max(0.0, min(1.0, ratio))
        if ratio <= 0.02:
            return
        w = (x1 - x0) * ratio
        bar = max(1.0, x1 - x0)
        steps = max(1, min(self.MAX_SEGMENTS, int(w / max(1.0, self.u(4)))))
        cap = min(r, w / 2.0)
        first_idx = 0
        last_idx = GRADIENT_STEPS - 1
        for i in range(steps):
            sx = x0 + w * i / float(steps)
            ex = x0 + w * (i + 1) / float(steps) + 1
            mid = (sx + ex) / 2.0
            t = (mid - x0) / bar
            idx = int(max(0.0, min(1.0, t)) * (GRADIENT_STEPS - 1))
            self.canvas.create_rectangle(
                sx, y0, ex, y1,
                fill=_GRADIENT[idx], outline="", tags=("progress_fill",),
            )
        if cap > 0.5:
            self.canvas.create_oval(
                x0, y0, x0 + cap * 2, y1,
                fill=_GRADIENT[first_idx], outline="", tags=("progress_fill",),
            )
            if ratio >= 0.995:   # 只有满槽时右端才收圆；没满是"液面"，切平更像水
                self.canvas.create_oval(
                    x0 + w - cap * 2, y0, x0 + w, y1,
                    fill=_GRADIENT[last_idx], outline="", tags=("progress_fill",),
                )
        # 顶部一条高光，让填充看起来是"液体"而不是一块纯色矩形
        self.canvas.create_line(
            x0 + cap, y0 + max(1, int(h * 0.22)),
            x0 + w - cap, y0 + max(1, int(h * 0.22)),
            fill=to_hex(rgb_mix(hex_to_rgb(ACCENT_LIGHT), (255, 255, 255), 0.5)),
            tags=("progress_fill",),
        )

    def enable_drag(self, widget=None):
        """
        无边框窗口按住拖动 —— 只用 Tk 自己的事件，绝不碰 Win32 消息。

        踩过的坑（别再改回去）：以前用
            ReleaseCapture() + SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION)
        这个"经典写法"，它会在 Tk 的事件回调内部进入 Windows 的**模态移动循环**，
        Tcl 解释器被重入，直接
            Fatal Python error: PyEval_RestoreThread: NULL tstate
        → abort（退出码 0xC0000409），表现为"鼠标一按在窗口上就闪退"。
        实测与 -transparentcolor / 亚克力无关，纯粹是这个调用。
        """
        target = widget or self.canvas
        target.bind("<ButtonPress-1>", self._on_drag_start, add="+")

    def _on_drag_start(self, event):
        if self._press_on_control(event):
            return  # 按在按钮/输入框上，不进入拖动
        # 记录指针与窗口左上角的偏移，移动时用偏移反推窗口位置（跟手不抖）
        dx = event.x_root - self.winfo_x()
        dy = event.y_root - self.winfo_y()

        def on_move(ev):
            self._move_to(ev.x_root - dx, ev.y_root - dy)

        def on_release(_ev):
            self._drag_handlers = None
            for w in self._drag_widgets():
                w.unbind("<B1-Motion>")
                w.unbind("<ButtonRelease-1>")

        self._drag_handlers = (on_move, on_release)
        for w in self._drag_widgets():
            w.bind("<B1-Motion>", on_move)
            w.bind("<ButtonRelease-1>", on_release)

    def _move_to(self, x, y):
        """
        统一的位置出口：先把坐标钳制进虚拟桌面，再挪过去，返回真正落点。
        无边框窗口不进任务栏也不进 Alt-Tab，一旦被拖到屏幕外就再也找不回来了
        （实测拖到副屏外侧只能靠杀进程），所以每一次挪都过这一道。
        返回落点而不是回头读 winfo_x()：geometry() 请求要等 Tk 处理，
        刚设完就读会拿到 0，弹窗因此被摆到屏幕左上角（实测踩过）。
        """
        x, y = win32ext.clamp_into_view(x, y, self.width, self.height)
        try:
            self.geometry("+%d+%d" % (x, y))
        except Exception:
            pass
        return x, y

    def keep_in_view(self):
        """从托盘重新唤起时，把可能已经跑到屏幕外的窗口拉回来。"""
        self._move_to(self.winfo_x(), self.winfo_y())

    def _drag_widgets(self):
        """拖动要同时挂在窗口和画布上：指针在两者之上都能收到 B1-Motion。"""
        return [w for w in (self, getattr(self, "canvas", None)) if w is not None]

    def _press_on_control(self, event):
        """按下位置是不是落在可交互的画布元素上（按钮等）——是则不拖动。"""
        canvas = getattr(self, "canvas", None)
        if canvas is None:
            return False
        try:
            item = canvas.find_closest(event.x, event.y)
            if not item:
                return False
            tags = canvas.gettags(item[0])
            if any(str(t).startswith("btn_") for t in tags):
                return True
            return canvas.type(item[0]) in ("window",)
        except Exception:
            return False


class GlassButton(object):
    """自绘按钮。primary = 强调色实心；其余为玻璃质感幽灵按钮。"""

    def __init__(self, window, x, y, w, h, text, command,
                 primary=True, font_size=11):
        u = window.u
        C = window.C
        self.win = window
        self.canvas = window.canvas
        self.command = command
        self.primary = primary
        self.enabled = True
        self.tag = "btn_%d" % id(self)

        if primary:
            base = hex_to_rgb(BTN_BLUE)
            self.colors = {
                "normal": to_hex(base),
                "hover": to_hex(rgb_mix(base, (255, 255, 255), 0.18)),
                "press": to_hex(rgb_mix(base, (0, 0, 0), 0.22)),
            }
            fill = self.colors["normal"]
            outline = ""
            fg, fweight = "#FFFFFF", "normal"
        else:
            self.colors = {
                "normal": C["ctrl"],
                "hover": C["ctrl_hover"],
                "press": C["ctrl_press"],
            }
            fill = C["ctrl"]
            outline = C["ctrl_edge"]
            fg, fweight = C["text"], "normal"

        radius = min(u(8), u(h) / 2.0)  # 小圆角矩形，参考 CustomTkinter
        self.shape = self.canvas.create_polygon(
            round_rect_points(u(x), u(y), u(x + w), u(y + h), radius),
            smooth=True,
            fill=fill,
            outline=outline,
            width=max(1, int(window.S)),
            tags=self.tag,
        )
        self.label = self.canvas.create_text(
            u(x + w / 2.0),
            u(y + h / 2.0),
            text=text,
            font=F(font_size, fweight),
            fill=fg,
            tags=self.tag,
        )
        self.canvas.tag_bind(self.tag, "<Enter>", self._enter)
        self.canvas.tag_bind(self.tag, "<Leave>", self._leave)
        self.canvas.tag_bind(self.tag, "<ButtonPress-1>", self._press)
        self.canvas.tag_bind(self.tag, "<ButtonRelease-1>", self._release)
        self._pressed = False

    def set_text(self, text):
        self.canvas.itemconfigure(self.label, text=text)

    def _paint(self, state):
        self.canvas.itemconfigure(self.shape, fill=self.colors[state])

    def _enter(self, _event=None):
        if not self.enabled:
            return
        self._paint("hover")
        self.canvas.configure(cursor="hand2")

    def _leave(self, _event=None):
        if self._pressed:
            return
        self._paint("normal")
        self.canvas.configure(cursor="")

    def _press(self, _event=None):
        self._pressed = True
        self._paint("press")

    def _release(self, _event=None):
        self._pressed = False
        self._paint("hover")
        if self.command:
            self.command()


class Toggle(object):
    """自绘开关。"""

    def __init__(self, window, x, y, value, command=None):
        u = window.u
        self.win = window
        self.canvas = window.canvas
        self.value = bool(value)
        self.command = command
        self.w, self.h = u(46), u(24)
        self.x, self.y = u(x), u(y)
        self.tag = "tgl_%d" % id(self)

        self.track = self.canvas.create_polygon(
            round_rect_points(self.x, self.y, self.x + self.w, self.y + self.h,
                              self.h / 2.0),
            smooth=True,
            fill="",
            outline="",
            tags=self.tag,
        )
        k = self.h / 6.0
        self.knob = self.canvas.create_oval(
            self.x + k, self.y + k, self.x + self.h - k, self.y + self.h - k,
            fill="#FFFFFF", outline="", tags=self.tag,
        )
        self.canvas.tag_bind(self.tag, "<Button-1>", self._click)
        self.canvas.tag_bind(self.tag, "<Enter>",
                             lambda e: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind(self.tag, "<Leave>",
                             lambda e: self.canvas.configure(cursor=""))
        self.render()

    def _click(self, _event=None):
        self.value = not self.value
        self.render()
        if self.command:
            self.command(self.value)

    def render(self):
        k = self.h / 6.0
        if self.value:
            self.canvas.itemconfigure(self.track, fill=ACCENT, outline="")
            self.canvas.itemconfigure(self.knob, fill="#FFFFFF")
            self.canvas.coords(
                self.knob,
                self.x + self.w - self.h + k,
                self.y + k,
                self.x + self.w - k,
                self.y + self.h - k,
            )
        else:
            self.canvas.itemconfigure(self.track, fill=self.win.C["toggle_off"],
                                      outline="")
            self.canvas.itemconfigure(self.knob, fill=self.win.C["faint"])
            self.canvas.coords(
                self.knob, self.x + k, self.y + k, self.x + self.h - k, self.y + self.h - k
            )


# ---------------------------------------------------------------- 提醒弹窗


class ReminderWindow(GlassWindow):
    WIDTH = 400
    BASE_HEIGHT = 188     # 标题一行时的总高
    LINE_H = 20           # 标题每多一行往下长的量（96dpi 设计值）
    AUTO_CLOSE_MS = 30000

    def __init__(self, master, app, title, total, goal, amount, snooze,
                 missable=True):
        text = core.strip_emoji(title)
        # 文案最长的那几条会换成两行。以前标题是居中排的，两行时第二行直接被
        # 窗口上沿切掉；固定留两行的位置又会让一行文案下面空一大截。
        # 所以先量一下要几行，再决定窗口多高。
        avail = (self.WIDTH - self.PAD * 2 - 30) * win32ext.DPI_SCALE
        lines = 1
        try:
            lines = max(1, int(math.ceil(font_obj(13, "bold").measure(text)
                                         / max(1.0, float(avail)))))
        except Exception:
            pass
        lines = max(1, min(3, lines))
        height = self.BASE_HEIGHT + (lines - 1) * self.LINE_H

        GlassWindow.__init__(
            self, master, self.WIDTH, height, tint=(26, 26, 34), alpha=205,
            topmost=True,
        )
        self.app = app
        self.amount = amount
        self.snooze = snooze
        self.missable = bool(missable)   # 达标祝贺那种弹窗不用"追提醒"
        self._after_id = None
        self._secs_left = self.AUTO_CLOSE_MS // 1000
        self._tick_id = None

        p = self.PAD
        W = self.WIDTH

        self.drop(p + 11, 16 + lines * self.LINE_H / 2.0, 24, ACCENT)
        self.text(p + 30, 16, text, size=13, weight="bold", anchor="nw",
                  width=W - p * 2 - 30)

        ty = 16 + lines * self.LINE_H        # 标题块下沿
        ratio = float(total) / float(goal) if goal else 0.0
        self.progress(p, ty + 8, W - p, ty + 20, ratio)
        left = max(0, goal - total)
        tail = "今日 %s / %s ml" % (fmt_int(total), fmt_int(goal))
        tail += "，还差 %s ml" % fmt_int(left) if left else "，已达标"
        self.text(p, ty + 40, tail, size=10, color=self.C["sub"])

        by = ty + 60
        GlassButton(self, p, by, 132, 36, "喝了 %d ml" % amount,
                    self.on_drink, primary=True)
        GlassButton(self, p + 142, by, 106, 36, "%d 分钟后" % snooze,
                    self.on_snooze, primary=False, font_size=10)
        GlassButton(self, p + 258, by, 96, 36, "今天不提醒",
                    self.on_skip, primary=False, font_size=10)

        # 倒计时可见：以前窗口 30 秒自己关掉却什么都不说，
        # 用户会以为"它根本没提醒"，其实是他看手机的那 30 秒过去了。
        self.txt_count = self.text(p, by + 56, "", size=9, color=self.C["faint"])
        GlassButton(self, W - p - 34, by + 44, 34, 24, "×", self.close,
                    primary=False, font_size=11)
        self._countdown()

        self.enable_drag()
        self.canvas.bind("<ButtonPress-1>", self._reset_timer, add="+")
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Return>", lambda e: self.on_drink())
        self.bind("<KP_Enter>", lambda e: self.on_drink())
        x, y = self.place_bottom_right()
        self.slide_in(x, y)
        self._arm_close()

    def _arm_close(self):
        """倒计时到点走 close(auto=True)：没人响应 = 没看见，由 App 决定要不要追提醒。"""
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self._after_id = self.after(self.AUTO_CLOSE_MS,
                                    lambda: self.close(auto=True))

    def _countdown(self):
        tail = ("没点的话 %d 分钟后再提醒一次" % self.snooze
                if self.missable else "达标了，不用再回我")
        self.canvas.itemconfigure(
            self.txt_count,
            text="%d 秒后自动收起 · %s" % (self._secs_left, tail),
        )
        self._secs_left -= 1
        if self._secs_left < 0:
            self._tick_id = None
            return
        self._tick_id = self.after(1000, self._countdown)

    def _cancel_countdown(self):
        if self._tick_id:
            try:
                self.after_cancel(self._tick_id)
            except Exception:
                pass
            self._tick_id = None

    def slide_in(self, x, y):
        frames = 12
        start_offset = self.u(60)
        for i in range(frames + 1):
            t = i / float(frames)
            ease = 1 - (1 - t) * (1 - t)
            offset = int(start_offset * (1 - ease))
            self.after(i * 16, lambda o=offset: self._move_to(x, y + o))

    def _reset_timer(self, _event=None):
        self._arm_close()
        self._cancel_countdown()
        self._secs_left = self.AUTO_CLOSE_MS // 1000
        self._countdown()

    def on_drink(self):
        self.app.record_drink(self.amount)
        self.close()

    def on_snooze(self):
        self.app.snooze(self.snooze)
        self.close()

    def on_skip(self):
        self.app.skip_today()
        self.close()

    def close(self, auto=False):
        self._cancel_countdown()
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            if auto and self.missable:
                self.app.on_reminder_missed(self.snooze)
            else:
                self.app.on_reminder_answered()
        except Exception:
            pass
        try:
            self.app.on_reminder_closed(self)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------- 设置窗口


class SettingsWindow(GlassWindow):
    WIDTH = 580
    HEIGHT = 768

    def __init__(self, master, app):
        GlassWindow.__init__(self, master, self.WIDTH, self.HEIGHT,
                             tint=GLASS_TINT, alpha=196, center=True)
        self.app = app
        self.cfg = app.cfg
        self.entries = {}
        self.toggles = {}
        self.notes = []
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.build()
        self.refresh()
        # 无边框窗口本来没有键盘通路：Ctrl+S 保存、Esc 收起、输入框回车保存
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-S>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.hide())
        unclamped = self.cfg.clamp_report()
        if unclamped:
            self.flash("配置里越界的值已按上限/下限生效：" + "，".join(
                "%s %s→%s" % (k, raw, eff) for k, raw, eff in unclamped))

    def _entry(self, x, cy, name, default, w_px=72):
        C = self.C
        var = tk.StringVar(value=str(default))
        ent = tk.Entry(
            self, textvariable=var, justify="center",
            bg=C["field"], fg=C["text"], insertbackground=C["text"],
            relief="flat", highlightthickness=max(1, int(self.S)),
            highlightbackground=C["field"], highlightcolor=ACCENT,
        )
        ent.configure(font=F(10))
        ent.bind("<Return>", lambda e: self.save())
        self.canvas.create_window(self.u(x), self.u(cy), window=ent,
                                  width=self.u(w_px), height=self.u(28),
                                  anchor="w")
        self.entries[name] = var
        return ent

    def _label(self, x, y, text, size=10, anchor="w"):
        return self.text(x, y, text, size=size, color=self.C["sub"], anchor=anchor)

    def build(self):
        p = self.PAD
        W = self.WIDTH
        H = self.HEIGHT

        # ---- 标题栏 ----
        self.drop(p + 11, 26, 24, ACCENT)
        self.text(p + 30, 20, "喝水提醒", size=15, weight="bold")
        self.text(p + 30, 42, "只在工作时段打扰你", size=9, color=self.C["sub"])
        # — 收进托盘（窗口留着，再点秒开）；× 关掉窗口（释放内存，再点重建）
        # 两个按钮以前做同一件事，看着像个 bug。谁都不会因为点这里就把程序退出，
        # 退出只走右下角那颗"退出程序"。
        GlassButton(self, W - p - 78, 18, 34, 26, "—", self.hide,
                    primary=False, font_size=11)
        GlassButton(self, W - p - 40, 18, 34, 26, "×", self.close_to_tray,
                    primary=False, font_size=12)
        self.enable_drag()

        # ---- 今日进度卡片 ----
        self.card(p, 72, W - p, 250, r=18)
        self._label(p + 20, 94, "今日进度")
        self.txt_total = self.text(p + 20, 128, "0", size=32, weight="bold")
        self.txt_goal = self.text(p + 104, 140, "/ 2000 ml", size=11,
                                  color=self.C["sub"])
        self.txt_left = self.text(W - p - 20, 140, "", size=11, color=ACCENT,
                                  anchor="e")
        self._progress_bar = (p + 20, 164, W - p - 20, 176)
        self._label(p + 20, 208, "最近 7 天")
        self._chart_box = (p + 104, 186, W - p - 20, 224)
        self._chart_label_y = 236

        # ---- 设置区 ----
        self.line(p, 268, W - p, 268)
        self.text(p, 288, "设置", size=12, weight="bold")

        rows = {"r1": 322, "r2": 360, "r3": 398, "r4": 436, "r5": 474}
        cy = lambda r: rows[r] + 14

        self._label(p, cy("r1"), "工作时段")
        self._entry(p + 88, cy("r1"), "workStart", self.cfg.get("workStart"))
        self._label(p + 170, cy("r1"), "—")
        self._entry(p + 196, cy("r1"), "workEnd", self.cfg.get("workEnd"))
        self._label(p + 296, cy("r1"), "提醒间隔")
        self._entry(p + 382, cy("r1"), "intervalMinutes",
                    self.cfg.get("intervalMinutes"), 60)
        self._label(p + 450, cy("r1"), "分钟")

        self._label(p, cy("r2"), "每次喝水")
        self._entry(p + 88, cy("r2"), "amountPerReminder",
                    self.cfg.get("amountPerReminder"))
        self._label(p + 170, cy("r2"), "ml")
        self._label(p + 296, cy("r2"), "每日目标")
        self._entry(p + 382, cy("r2"), "dailyGoal", self.cfg.get("dailyGoal"), 72)
        self._label(p + 462, cy("r2"), "ml")

        self._label(p, cy("r3"), "稍后提醒")
        self._entry(p + 88, cy("r3"), "snoozeMinutes", self.cfg.get("snoozeMinutes"))
        self._label(p + 170, cy("r3"), "分钟")
        self._label(p + 296, cy("r3"), "只在周一至周五")
        self.toggles["workdaysOnly"] = Toggle(self, p + 412, rows["r3"],
                                              self.cfg.get("workdaysOnly", False))

        self._label(p, cy("r4"), "午休免打扰")
        self._entry(p + 88, cy("r4"), "lunchStart",
                    self.cfg.get("lunchBreak", {}).get("start"))
        self._label(p + 170, cy("r4"), "—")
        self._entry(p + 196, cy("r4"), "lunchEnd",
                    self.cfg.get("lunchBreak", {}).get("end"))
        self._label(p + 296, cy("r4"), "午休不打扰")
        self.toggles["lunchBreak"] = Toggle(self, p + 412, rows["r4"],
                                            self.cfg.get("lunchBreak", {}).get("enabled", True))

        self._label(p, cy("r5"), "开机自启")
        self.toggles["autoStart"] = Toggle(self, p + 88, rows["r5"],
                                           self.cfg.get("autoStart", False))
        self._label(p + 150, cy("r5"), "开着才会在你没注意时提醒")
        # 关掉自启时把后果写在脸上，而不是让用户自己猜
        self.txt_alive = self.text(p, 506, "", size=9, color=self.C["faint"])

        # ---- 提醒文案 ----
        self.text(p, 536, "提醒文案（一行一条，随机显示）", size=10,
                  color=self.C["sub"])
        self.msg_box = tk.Text(
            self, height=6, bg=self.C["field"], fg=self.C["text"],
            insertbackground=self.C["text"], relief="flat", wrap="word",
            highlightthickness=max(1, int(self.S)),
            highlightbackground=self.C["field_edge"], highlightcolor=ACCENT,
            padx=int(12 * self.S), pady=int(9 * self.S),
        )
        self.msg_box.configure(font=F(10))
        # 不指定 height，让 Text 按 6 行自适应，避免出现被切一半的行
        self.canvas.create_window(self.u(p), self.u(556), window=self.msg_box,
                                  anchor="nw", width=self.u(W - p * 2))
        self.msg_box.insert("1.0", "\n".join(self.cfg.get("messages", [])))

        # ---- 底部按钮（贴着窗口下沿，改上面的行距不会把它们顶出屏幕）----
        by = H - self.PAD - 38
        GlassButton(self, p, by, 108, 38, "保存", self.save, primary=True)
        GlassButton(self, p + 120, by, 108, 38, "测试提醒", self.test, primary=False)
        GlassButton(self, p + 240, by, 108, 38, "打开数据目录", self.open_dir,
                    primary=False, font_size=10)
        GlassButton(self, W - p - 92, by, 92, 38, "退出程序", self.quit_app,
                    primary=False, font_size=10)

    def update_alive_hint(self):
        """自启没开就常驻一句提醒：这是"到点不提醒"最常见的原因。"""
        on = bool(self.toggles["autoStart"].value)
        txt = ("开机自启已开：重启电脑后会自动在后台运行" if on else
               "未开机自启：重启或注销后要手动打开才会提醒；进程没在跑的时候不会有任何提醒")
        self.canvas.itemconfigure(self.txt_alive, text=txt,
                                  fill=self.C["sub"] if on else "#E0A458")
        self.toggles["autoStart"].command = lambda v: (
            self.update_alive_hint(), self.set_autostart_now(v))

    def set_autostart_now(self, want):
        """自启开关即时生效，不用等"保存"。"""
        try:
            win32ext.set_autostart(bool(want))
            self.cfg.data["autoStart"] = bool(want)
            self.cfg.save()
            core.log("开机自启：%s" % ("开" if want else "关"))
        except Exception as exc:
            core.log("开机自启设置失败：%s" % exc)


    def refresh(self):
        total = self.app.store.total()
        goal = self.cfg.get("dailyGoal", 2000)
        self.canvas.itemconfigure(self.txt_total, text=fmt_int(total))
        self.canvas.itemconfigure(self.txt_goal, text="/ %s ml" % fmt_int(goal))
        # 大数字宽度随位数变化，目标文字要跟着挪
        gap = self.u(10)
        self.canvas.coords(
            self.txt_goal,
            self.u(self.PAD + 20) + font_obj(32, "bold").measure(fmt_int(total)) + gap,
            self.u(140),
        )
        left = max(0, goal - total)
        self.canvas.itemconfigure(
            self.txt_left,
            text=("还差 %s ml" % fmt_int(left)) if left else "今日达标",
            fill=ACCENT if left else OK_GREEN,
        )
        for tag in ("progress_fill", "progress_track"):
            for item in self.canvas.find_withtag(tag):
                self.canvas.delete(item)
        x0, y0, x1, y1 = self._progress_bar
        self.progress(x0, y0, x1, y1, float(total) / float(goal) if goal else 0)
        for item in self.canvas.find_withtag("chart"):
            self.canvas.delete(item)
        self.draw_chart()
        self.update_alive_hint()

    def draw_chart(self):
        """
        7 天柱状图：达标绿、今天蓝色、没数据留灰；带目标虚线和星期标签。
        以前只有一排等高的小色块，既看不出哪天达标，也看不出哪根是今天。
        """
        x0, y0, x1, y1 = (self.u(v) for v in self._chart_box)
        hist = self.app.store.history(7)
        goal = float(self.cfg.get("dailyGoal", 2000) or 2000)
        n = len(hist)
        if not n:
            return
        gap = self.u(6)
        bw = max(self.u(4), ((x1 - x0) - gap * (n - 1)) / float(n))
        scale = y1 - y0
        peak = max(goal, max([v for _, v in hist] or [0])) or 1.0

        # 目标线
        gy = y1 - scale * (goal / peak)
        self.canvas.create_line(x0, gy, x1, gy, fill=self.C["line"],
                                dash=(3, 3), tags="chart")
        today = hist[-1][0]
        for i, (day, val) in enumerate(hist):
            bx = x0 + i * (bw + gap)
            met = goal and val >= goal
            is_today = day == today
            if val <= 0:
                color, ratio = self.C["field_edge"], 0.06
            elif met:
                color, ratio = OK_GREEN, min(1.0, val / peak)
            else:
                color = ACCENT if is_today else self.C["card_hi"]
                ratio = max(0.08, val / peak)
            h = scale * min(1.0, ratio)
            r = min(bw / 2.0, self.u(3))
            self.canvas.create_polygon(
                round_rect_points(bx, y1 - h, bx + bw, y1, r),
                smooth=True, fill=color, outline="", tags="chart",
            )
            label_color = self.C["text"] if is_today else self.C["faint"]
            self.canvas.create_text(
                bx + bw / 2.0, self.u(self._chart_label_y),
                text=("今" if is_today else weekday_label(day)),
                font=F(8, "bold" if is_today else "normal"),
                fill=label_color, tags="chart",
            )

    def collect(self):
        """
        读回输入。越界/写错的值会被收进合法范围，同时把"你填的 → 实际用的"
        记在 self.notes 里，保存后回显。
        以前这里是静默 clamp：填了 9999 分钟间隔，程序变成 480，界面上却什么都不说，
        用户只会觉得"这软件不干活"。
        """
        self.notes = []

        def int_of(name, label, default, low, high):
            raw = str(self.entries[name].get()).strip()
            try:
                v = int(raw)
                ok = True
            except Exception:
                v, ok = default, False
            c = max(low, min(high, v))
            if not ok or c != v:
                self.notes.append("%s %s→%s" % (label, raw or "空", c))
            return c

        def hm_of(name, label, default):
            raw = str(self.entries[name].get()).strip()
            try:
                h, m = raw.split(":")
                h, m = int(h), int(m)
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
                fixed = "%02d:%02d" % (h, m)
            except Exception:
                fixed = default
            if fixed != raw:
                self.notes.append("%s %s→%s" % (label, raw or "空", fixed))
            return fixed

        data = self.cfg.data
        data["workStart"] = hm_of("workStart", "开始", "09:00")
        data["workEnd"] = hm_of("workEnd", "结束", "18:00")
        data["intervalMinutes"] = int_of("intervalMinutes", "间隔", 60, 5, 480)
        data["amountPerReminder"] = int_of("amountPerReminder", "每次", 250, 50, 2000)
        data["dailyGoal"] = int_of("dailyGoal", "目标", 2000, 200, 10000)
        data["snoozeMinutes"] = int_of("snoozeMinutes", "稍后", 15, 5, 120)
        lb = self.cfg.get("lunchBreak", {})
        lb["enabled"] = bool(self.toggles["lunchBreak"].value)
        lb["start"] = hm_of("lunchStart", "午休起", "12:00")
        lb["end"] = hm_of("lunchEnd", "午休止", "13:00")
        data["lunchBreak"] = lb
        data["workdaysOnly"] = bool(self.toggles["workdaysOnly"].value)
        data["autoStart"] = bool(self.toggles["autoStart"].value)

        raw = self.msg_box.get("1.0", "end").strip()
        msgs = [line.strip() for line in raw.splitlines() if line.strip()]
        if msgs:
            data["messages"] = msgs
        # 写回 raw，clamp_report() 只报"文件里那一次"的越界，不重复报本次
        self.cfg.raw = dict(data)
        return data

    def save(self):
        self.collect()
        self.cfg.save()
        self.app.on_config_saved()
        self.refresh()
        if self.notes:
            self.flash("已保存（已修正：" + "，".join(self.notes) + "）", warn=True)
        else:
            self.flash("已保存，设置立即生效")

    def flash(self, message, warn=False):
        item = self.text(self.PAD, self.HEIGHT - self.PAD - 54, message, size=9,
                         color="#E0A458" if warn else OK_GREEN)
        self.after(2200, lambda: self.canvas.delete(item))

    def test(self):
        self.app.show_reminder_now()

    def open_dir(self):
        self.app.open_data_dir()

    def quit_app(self):
        self.app.quit()

    def hide(self):
        """— 收进托盘：窗口保留，再点图标秒开。"""
        self.withdraw()
        self.app.on_settings_hidden()

    def close_to_tray(self):
        """× 关窗口：真的销毁，释放画布对象；程序继续在后台提醒。"""
        self.app.on_settings_closing()
        try:
            self.destroy()
        except Exception:
            pass


class HintWindow(GlassWindow):
    """
    贴在托盘上方的短提示。用来把"程序还在后台"这件事说出口 ——
    喝水提醒是常驻工具，用户收起窗口后最常见的误解就是"它没提醒"。
    """

    def __init__(self, master, text, seconds=6, width=340, lift=0):
        GlassWindow.__init__(self, master, width, 56, topmost=True)
        self.canvas.delete("surface")
        c = self.C
        r = self.u(14)
        self.canvas.create_polygon(
            round_rect_points(1, 1, self.width - 1, self.height - 1, r),
            smooth=True, fill=c["card"], outline=c["card_edge"],
            width=max(1, int(self.S)),
        )
        self.text(width / 2.0, 18, core.strip_emoji(text), size=10,
                  color=c["text"], anchor="c", width=width - 24)
        left, top, right, bottom = win32ext.work_area()
        # lift：右下角已经有提醒弹窗时，提示条往上让开，别叠在一起
        self._move_to(right - self.width - self.u(12),
                      bottom - self.height - self.u(8) - int(lift))
        self._job = self.after(int(seconds * 1000), self.close)

    def close(self):
        try:
            self.destroy()
        except Exception:
            pass
