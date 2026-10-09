# -*- coding: utf-8 -*-
"""
ui.py —— 玻璃质感界面

设计参考 glassmorphism 通行规范（半透明卡片 + 1px 亮边 + 柔和投影）与
Windows 11 的 Mica 视觉：

  * Tk Canvas 没有 alpha 色，所以"半透明白"用等效混色实现：
    卡片色 = 窗口底色 与 白色 按比例混合，视觉上等同于叠在玻璃上的半透明白
  * 层次：窗口底 < 卡片 < 控件 < 主按钮（强调色只给主要动作）
  * 进程必须先 win32ext.enable_dpi_awareness()，否则高分屏整体发糊
  * 布局坐标一律传 96dpi 设计值，由 self.u() 换算（只换算一次！）
  * 字体同理走 self.fs()：窗口缩放因子包含"自适应 fit"，
    字体不跟着缩的话，缩完的框里会挤出一半的字
  * 不再使用 `-transparentcolor` + 亚克力：真透明分层窗口一拖就触发 DWM 崩溃
"""

import datetime as dt
import math
import os
import tkinter as tk
from tkinter import font as tkfont

import core
import win32ext

DARK_BG = "#171B22"      # 深色主题的稳定实底窗色（不依赖任何透明效果）
LIGHT_BG = "#E9EDF2"     # 浅色主题的稳定实底窗色

ACCENT = "#4FC3F7"            # 强调色（深色底上的水蓝）
ACCENT_ON_LIGHT = "#1976D2"   # 浅色底要深一档，浅蓝写在白卡片上等于没写
BTN_BLUE = "#1E88E5"          # 主按钮：比强调色沉稳一档，白字
BTN_BLUE_ON_LIGHT = "#1565C0"
DANGER = "#C4524A"            # 破坏性动作（退出程序）：不能再长得像普通按钮
DANGER_ON_LIGHT = "#B03A31"
ACCENT_LIGHT = "#8BE9FF"
ACCENT_DEEP = "#2196F3"
OK_GREEN = "#7BD88F"
OK_GREEN_ON_LIGHT = "#2E7D46"
WARN = "#E0A458"
WARN_ON_LIGHT = "#9A5B0C"


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


def F(size, weight="normal", scale=None):
    """
    像素字体（负号 = 像素单位），按窗口自己的缩放因子放大。
    scale 不传就用全局 DPI 因子；窗口里一律传 self.S，
    因为 self.S 里还叠了"窗口放不下时的自适应系数"。
    """
    s = win32ext.DPI_SCALE if scale is None else scale
    px = max(9, int(round(size * s * 4.0 / 3.0)))
    return (FONT_FAMILY, -px, weight)


_FONT_CACHE = {}


def font_obj(size, weight="normal", scale=None):
    """
    拿一个可复用的 tkfont.Font（用来量文字宽度）。
    实测每刷新新建一个 Font 并不会真的漏句柄，但每次 measure 都重建对象没必要，
    进度条那个大数字刷新频率最高，这里缓存一次（缓存键要带上缩放因子）。
    """
    s = win32ext.DPI_SCALE if scale is None else scale
    key = (size, weight, round(s, 4))
    f = _FONT_CACHE.get(key)
    if f is None:
        f = _FONT_CACHE[key] = tkfont.Font(font=F(size, weight, s))
    return f


def rgb_mix(c1, c2, t):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def to_hex(rgb):
    return "#%02X%02X%02X" % rgb


def hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


fmt_int = core.fmt_int      # 千分位；实现在 core，别在这里留第二份


def weekday_label(date_key):
    """'2026-10-08' -> '三'；解析失败返回空串。"""
    try:
        y, m, d = (int(x) for x in date_key.split("-"))
        return "一二三四五六日"[dt.date(y, m, d).weekday()]
    except Exception:
        return ""


# 进度条渐变色表：预先算好 32 档，画的时候只查表，不再每段做一次混色。
# 深色/浅色各一套：浅底上原来那档亮蓝几乎看不出来。
GRADIENT_STEPS = 32


def _gradient(c_from, c_to):
    a, b = hex_to_rgb(c_from), hex_to_rgb(c_to)
    return [to_hex(rgb_mix(a, b, i / float(GRADIENT_STEPS - 1)))
            for i in range(GRADIENT_STEPS)]


_GRADIENT_DARK = _gradient(ACCENT_LIGHT, ACCENT_DEEP)
_GRADIENT_LIGHT = _gradient("#8FD3FF", "#1565C0")


def resolve_dark(cfg):
    """配置 theme=auto 时跟随系统的"应用深色模式"；dark/light 直接钉死。"""
    theme = str((cfg.data if cfg else {}).get("theme", "auto")).lower()
    if theme == "dark":
        return True
    if theme == "light":
        return False
    return not win32ext.apps_use_light_theme()


def make_palette(dark=True):
    """
    生成配色。半透明一律换算成"叠在底色上的等效实色"。
    采用稳定的玻璃拟态（glassmorphism）方案：实色窗口主体 + 1px 亮边 + 顶部高光，
    不依赖任何透明分层窗口，因此不会黑屏、不会点击穿透、拖动不会闪退。

    浅色主题不是"把深色反个色"：白底上"亮边"没有意义，卡片要靠
    "更白 + 一圈灰边框"浮起来，文字是混黑而不是混白，所以两套配方分开写。
    """
    W, K = (255, 255, 255), (0, 0, 0)
    if dark:
        bg, spec = DARK_BG, {
            "text": (W, 0.94), "sub": (W, 0.62), "faint": (W, 0.46),
            "surface": (W, 0.055), "surface_edge": (W, 0.16), "surface_hi": (W, 0.30),
            "card": (W, 0.10), "card_edge": (W, 0.14), "card_hi": (W, 0.20),
            "ctrl": (W, 0.07), "ctrl_hover": (W, 0.14), "ctrl_press": (K, 0.18),
            "ctrl_edge": (W, 0.16),
            "field": (K, 0.34), "field_edge": (W, 0.10),
            "track": (W, 0.08), "toggle_off": (W, 0.13), "line": (W, 0.16),
            "bar": (W, 0.22), "bar_empty": (W, 0.14),
        }
    else:
        bg, spec = LIGHT_BG, {
            "text": (K, 0.88), "sub": (K, 0.62), "faint": (K, 0.46),
            "surface": (W, 0.78), "surface_edge": (K, 0.18), "surface_hi": (W, 0.98),
            "card": (W, 0.88), "card_edge": (K, 0.14), "card_hi": (W, 1.00),
            "ctrl": (K, 0.055), "ctrl_hover": (K, 0.11), "ctrl_press": (K, 0.18),
            "ctrl_edge": (K, 0.16),
            "field": (W, 0.99), "field_edge": (K, 0.20),
            "track": (K, 0.10), "toggle_off": (K, 0.16), "line": (K, 0.12),
            # 柱状图：浅色主题上 card_hi 是"更白"，白底上等于隐形，
            # 所以柱子/空柱单独给一档（深色那套同理，反过来而已）。
            "bar": (K, 0.24), "bar_empty": (K, 0.09),
        }
    base = hex_to_rgb(bg)
    out = {}
    for key, (color, alpha) in spec.items():
        out[key] = to_hex(rgb_mix(base, color, alpha))
    out.update({
        "dark": dark,
        "bg": bg,
        "accent": ACCENT if dark else ACCENT_ON_LIGHT,
        "accent_deep": ACCENT_DEEP if dark else "#0D47A1",
        "ok": OK_GREEN if dark else OK_GREEN_ON_LIGHT,
        "warn": WARN if dark else WARN_ON_LIGHT,
        "btn_blue": BTN_BLUE if dark else BTN_BLUE_ON_LIGHT,
        "danger": DANGER if dark else DANGER_ON_LIGHT,
        "gradient": _GRADIENT_DARK if dark else _GRADIENT_LIGHT,
        # 进度条顶端那条"液面高光"：深色上偏白，浅色上反过来压深一点才看得见
        "gloss": to_hex(rgb_mix(hex_to_rgb(ACCENT_LIGHT), W, 0.5)) if dark
                 else to_hex(rgb_mix(hex_to_rgb("#8FD3FF"), K, 0.18)),
    })
    return out


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
    """
    无边框 + 稳定玻璃拟态背景的窗口。width/height 传 96dpi 设计值。

    self.S = 系统 DPI 因子 × 自适应系数：
    150% 缩放的 1080p 笔记本上，580×768 的设置窗物理高是 1152，比工作区还高，
    底部那一排"保存 / 退出程序"会被整条切掉 —— 用户装完第一次打开就点不到保存。
    所以构造时先按当前显示器的工作区算一个 fit（缩到放不下为止，最低 MIN_FIT），
    坐标和字体都过这一个因子，不会出现"框缩了字没缩"的对不齐。
    """

    PAD = 24
    MAX_SEGMENTS = 24   # 渐变进度条最多画这么多段
    MIN_FIT = 0.62      # 自适应最多缩到这里，再小字号就看不清了
    CORNER = 8.0        # 与 Win11 系统圆角对齐（物理像素），别再画两层圆角

    def __init__(self, master, width, height, dark=True, topmost=False,
                 center=False):
        tk.Toplevel.__init__(self, master)
        base = win32ext.DPI_SCALE
        left, top_, right, bottom = win32ext.work_area()
        margin = int(self.PAD * base)
        fit = 1.0
        try:
            if width * base > 0 and height * base > 0:
                fit = min(1.0,
                          float(right - left - margin) / float(width * base),
                          float(bottom - top_ - margin) / float(height * base))
        except Exception:
            fit = 1.0
        self.fit = max(self.MIN_FIT, min(1.0, fit))
        self.S = base * self.fit
        self.u = lambda v: int(v * self.S)
        self.width = self.u(width)
        self.height = self.u(height)
        self.overrideredirect(True)
        # 稳定方案：纯实色窗口。不使用 -transparentcolor / 亚克力，
        # 避免"透明分层窗口 + DWM 合成"在拖动时崩溃（一挪就闪退）。
        self.configure(bg=DARK_BG if dark else LIGHT_BG)
        self.canvas = tk.Canvas(
            self,
            width=self.width,
            height=self.height,
            bg=DARK_BG if dark else LIGHT_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        self.update_idletasks()

        self.hwnd = win32ext.get_hwnd(self)
        # 圆角 + 投影均为系统原生效果，单独使用稳定，不会引起闪退
        win32ext.set_round_corner(self.hwnd)
        win32ext.set_window_shadow(self.hwnd)
        self.C = make_palette(dark)
        self._draw_surface()

        if center:
            self.center_on_screen()
        if topmost:
            win32ext.set_topmost(self.hwnd, True)

    def fs(self, size, weight="normal"):
        """这个窗口自己的字体（跟着自适应系数一起缩）。"""
        return F(size, weight, self.S)

    def fobj(self, size, weight="normal"):
        return font_obj(size, weight, self.S)

    def _draw_surface(self):
        """
        稳定的玻璃拟态实体卡片：实色主体（吸收全部点击，绝不穿透）
        + 1px 亮边 + 顶部高光带，模拟玻璃受光质感。

        圆角半径跟的是系统 DWM 那一圈（物理 8px）。以前画的是 16 设计值，
        DWM 又按自己的半径裁一次，四角能看到两条弧线 —— 就是"廉价感"的来源。
        """
        c = self.C
        W, H = self.width, self.height
        r = min(self.CORNER, W / 4.0, H / 4.0)
        self.canvas.create_polygon(
            round_rect_points(0.5, 0.5, W - 0.5, H - 0.5, r),
            smooth=True,
            fill=c["surface"],
            outline=c["surface_edge"],
            width=1,
            tags=("surface",),
        )
        # 顶部高光带：模拟玻璃从上往下受光的微亮过渡
        self.canvas.create_polygon(
            round_rect_points(self.u(5), 2, W - self.u(5), self.u(9),
                             self.u(5)),
            smooth=True,
            fill=c["surface_hi"],
            outline="",
        )

    def center_on_screen(self):
        """
        居中到"光标所在那块屏"的工作区，而不是 Tk 报的屏幕尺寸：
        双屏下主屏往往不是干活那块；而且窗口还没 map 时 winfo_screenwidth()
        会给出 0，居中结果直接变成左上角 (0,0)（换主题重建窗口时实测踩过）。
        """
        left, top, right, bottom = win32ext.work_area()
        self._move_to(int(left + ((right - left) - self.width) / 2.0),
                      int(top + ((bottom - top) - self.height) / 2.0))

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
        """玻璃卡片：等效半透明白填充 + 1px 亮边 + 顶部高光。"""
        c = self.C
        self.canvas.create_polygon(
            round_rect_points(self.u(x0), self.u(y0), self.u(x1), self.u(y1),
                              self.u(r)),
            smooth=True,
            fill=c["card"],
            outline=c["card_edge"],
            width=1,
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
            font=self.fs(size, weight),
            fill=color,
            anchor=anchor,
            width=width,
        )

    def line(self, x0, y0, x1, y1):
        self.canvas.create_line(self.u(x0), self.u(y0), self.u(x1), self.u(y1),
                                fill=self.C["line"])

    def drop(self, cx, cy, size, color=None):
        """画一个水滴小图标。"""
        pts = []
        for px, py in drop_points(self.u(cx), self.u(cy), self.u(size)):
            pts += [px, py]
        self.canvas.create_polygon(pts, smooth=True,
                                   fill=color or self.C["accent"], outline="")

    def progress(self, x0, y0, x1, y1, ratio):
        """
        进度条：暗槽 + 渐变填充，两端收圆。

        段数封顶：以前按 3px 一段算，200% 缩放的宽条一次就画上百个矩形，
        设置窗口每刷新一次就来一遍（刷新还会被记录写入触发）。
        渐变色表预先算好，这里只查表。
        """
        x0, y0, x1, y1 = self.u(x0), self.u(y0), self.u(x1), self.u(y1)
        grad = self.C["gradient"]
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
                fill=grad[idx], outline="", tags=("progress_fill",),
            )
        if cap > 0.5:
            self.canvas.create_oval(
                x0, y0, x0 + cap * 2, y1,
                fill=grad[first_idx], outline="", tags=("progress_fill",),
            )
            if ratio >= 0.995:   # 只有满槽时右端才收圆；没满是"液面"，切平更像水
                self.canvas.create_oval(
                    x0 + w - cap * 2, y0, x0 + w, y1,
                    fill=grad[last_idx], outline="", tags=("progress_fill",),
                )
        # 顶部一条高光，让填充看起来是"液体"而不是一块纯色矩形
        self.canvas.create_line(
            x0 + cap, y0 + max(1, int(h * 0.22)),
            x0 + w - cap, y0 + max(1, int(h * 0.22)),
            fill=self.C["gloss"],
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
        """
        从托盘重新唤起时，把可能已经跑到屏幕外的窗口整扇拉回它所在的那块屏幕。

        _move_to 只保证"至少露出 40 像素、还能拖着回来"，那是给拖动用的；
        用户点托盘是要看界面的，留 40 像素等于没找回来。所以这里按
        窗口中心所在显示器的工作区，把整扇窗口夹进去（窗口比工作区还大时
        对齐左上角，底部交给构造时算好的自适应缩放）。
        """
        x, y = self.winfo_x(), self.winfo_y()
        try:
            left, top, right, bottom = win32ext.monitor_work_area(
                x + self.width // 2, y + self.height // 2)
        except Exception:
            return self._move_to(x, y)
        x = min(max(x, left), max(left, right - self.width))
        y = min(max(y, top), max(top, bottom - self.height))
        return self._move_to(x, y)

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
    """
    自绘按钮。
      primary = 强调色实心（主要动作，一个窗口里最多一个）
      danger  = 破坏性动作（退出程序）：以前它和"打开数据目录"长得一模一样，
                手滑一下常驻程序就没了，得给它另一种颜色
      其余    = 玻璃质感幽灵按钮
    """

    def __init__(self, window, x, y, w, h, text, command,
                 primary=True, font_size=11, danger=False):
        u = window.u
        C = window.C
        self.win = window
        self.canvas = window.canvas
        self.command = command
        self.primary = primary
        self.danger = danger
        self.enabled = True
        self.tag = "btn_%d" % id(self)

        if danger:
            base = hex_to_rgb(C["danger"])
            self.colors = {
                "normal": to_hex(base),
                "hover": to_hex(rgb_mix(base, (255, 255, 255), 0.16)),
                "press": to_hex(rgb_mix(base, (0, 0, 0), 0.22)),
            }
            fill = self.colors["normal"]
            outline = ""
            fg, fweight = "#FFFFFF", "normal"
        elif primary:
            base = hex_to_rgb(C["btn_blue"])
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
            width=1,
            tags=self.tag,
        )
        self.label = self.canvas.create_text(
            u(x + w / 2.0),
            u(y + h / 2.0),
            text=text,
            font=window.fs(font_size, fweight),
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
        accent = self.win.C["accent"]
        if self.value:
            self.canvas.itemconfigure(self.track, fill=accent, outline="")
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


# ---------------------------------------------------------------- 小分段选择器


class Segments(object):
    """
    "二选一 / 三选一"的小分段控件（单位、外观）。
    用 tk 自带 radio/OptionMenu 的话，样式跟这套自绘玻璃界面完全对不上，
    所以按同一套配色自己画：选中项 = 强调色实心，其余 = 幽灵按钮。
    """

    def __init__(self, window, x, y, options, value, command=None,
                 w=44, h=26, font_size=9, gap=4):
        self.win = window
        self.canvas = window.canvas
        self.options = list(options)          # [(值, 文字), ...]
        self.value = value
        self.command = command
        self.h = h
        self.tag = "seg_%d" % id(self)
        self._items = []
        for val, label in self.options:
            sx, sy, sw, sh = window.u(x), window.u(y), window.u(w), window.u(h)
            shape = self.canvas.create_polygon(
                round_rect_points(sx, sy, sx + sw, sy + sh,
                                  min(sw / 2.0, sh / 2.0)),
                smooth=True, fill="", outline="", width=1, tags=self.tag)
            text = self.canvas.create_text(
                sx + sw / 2.0, sy + sh / 2.0, text=label, fill="", tags=self.tag)
            self._items.append((val, sx, sy, sw, sh, shape, text))
            x += w + gap
        self.width_design = w + (w + gap) * (len(self.options) - 1)
        self.canvas.tag_bind(self.tag, "<Button-1>", self._click)
        self.canvas.tag_bind(self.tag, "<Enter>",
                             lambda e: self.canvas.configure(cursor="hand2"))
        self.canvas.tag_bind(self.tag, "<Leave>",
                             lambda e: self.canvas.configure(cursor=""))
        self.render()

    def set(self, value):
        self.value = value
        self.render()

    def _hit(self, x, y):
        """画布坐标（物理像素）→ 点中了哪一段。"""
        for val, sx, sy, sw, sh, _shape, _text in self._items:
            if sx <= x <= sx + sw and sy <= y <= sy + sh:
                return val
        return None

    def _click(self, event):
        val = self._hit(event.x, event.y)
        if val is None or val == self.value:
            return
        self.value = val
        self.render()
        if self.command:
            self.command(val)

    def render(self):
        C = self.win.C
        for val, _sx, _sy, _sw, _sh, shape, text in self._items:
            on = (val == self.value)
            self.canvas.itemconfigure(
                shape, fill=C["accent"] if on else C["ctrl"],
                outline="" if on else C["ctrl_edge"])
            self.canvas.itemconfigure(
                text, fill="#FFFFFF" if on else C["sub"],
                font=self.win.fs(9, "bold" if on else "normal"))


# ---------------------------------------------------------------- 提醒弹窗


class ReminderWindow(GlassWindow):
    WIDTH = 400
    BASE_HEIGHT = 196     # 标题一行时的总高
    LINE_H = 20           # 标题每多一行往下长的量（96dpi 设计值）
    AUTO_CLOSE_MS = 30000

    def __init__(self, master, app, title, total, goal, amount, snooze,
                 missable=True, tail=None):
        cfg = app.cfg
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

        GlassWindow.__init__(self, master, self.WIDTH, height,
                             dark=resolve_dark(cfg), topmost=True)
        self.app = app
        self.amount = amount
        self.snooze = snooze
        self.missable = bool(missable)   # 达标祝贺那种弹窗不用"追提醒"
        # 倒计时后面那半句：不追提醒的原因有好几种，别说成一句万金油
        self.tail = tail or ("没点的话 %d 分钟后再提醒一次" % snooze
                             if missable else "这条不会再追提醒")
        self._after_id = None
        self._secs_left = self.AUTO_CLOSE_MS // 1000
        self._tick_id = None

        p = self.PAD
        W = self.WIDTH
        unit = core.unit_label(cfg)

        self.drop(p + 11, 16 + lines * self.LINE_H / 2.0, 24)
        self.text(p + 30, 16, text, size=13, weight="bold", anchor="nw",
                  width=W - p * 2 - 30)

        ty = 16 + lines * self.LINE_H        # 标题块下沿
        ratio = float(total) / float(goal) if goal else 0.0
        self.progress(p, ty + 8, W - p, ty + 20, ratio)
        left = max(0, goal - total)
        tail = "今日 %s / %s %s" % (core.to_display(cfg, total),
                                    core.to_display(cfg, goal), unit)
        tail += ("，还差 %s %s" % (core.to_display(cfg, left), unit)) if left else "，已达标"
        self.text(p, ty + 40, tail, size=10, color=self.C["sub"])

        by = ty + 60
        GlassButton(self, p, by, 132, 36, "喝了 %s" % core.fmt_vol(cfg, amount),
                    self.on_drink, primary=True)
        GlassButton(self, p + 142, by, 106, 36, "%d 分钟后" % snooze,
                    self.on_snooze, primary=False, font_size=10)
        GlassButton(self, p + 258, by, 96, 36, "今天不提醒",
                    self.on_skip, primary=False, font_size=10)

        # 倒计时可见：以前窗口 30 秒自己关掉却什么都不说，
        # 用户会以为"它根本没提醒"，其实是他看手机的那 30 秒过去了。
        # 右边留给 × 按钮，文案长了要换行，不能压到按钮上。
        self.txt_count = self.text(p, by + 56, "", size=10, color=self.C["sub"],
                                   width=W - p * 2 - 46)
        GlassButton(self, W - p - 34, by + 44, 34, 24, "×", self.close,
                    primary=False, font_size=11)
        self._countdown()

        self.enable_drag()
        self.canvas.bind("<ButtonPress-1>", self._on_first_click, add="+")
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Return>", lambda e: self.on_drink())
        self.bind("<KP_Enter>", lambda e: self.on_drink())
        x, y = self.place_bottom_right()
        self.slide_in(x, y)
        self._arm_close()

    def _on_first_click(self, _event=None):
        """
        键盘通路的真话：无边框（overrideredirect）窗口不进任务栏也不进 Alt-Tab，
        系统不会主动把键盘给它；而我们也**不该**去抢前台 ——
        用户正在文档里打字，弹一条提醒就把键盘抢走，比"没有快捷键"恶劣得多。
        所以这里的约定是：鼠标点过这个窗口之后，回车 / Esc 才生效。
        """
        try:
            self.focus_force()
        except Exception:
            pass
        self._reset_timer()

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
        self.canvas.itemconfigure(
            self.txt_count,
            text="%d 秒后自动收起 · %s" % (self._secs_left, self.tail),
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
                             dark=resolve_dark(app.cfg), center=True)
        self.app = app
        self.cfg = app.cfg
        self.entries = {}
        self.toggles = {}
        self.segments = {}
        self.notes = []
        # 记下开窗时是哪套配色：保存时对比一下，改外观要重建窗口才能让卡片颜色生效
        self._dark = resolve_dark(app.cfg)
        # 加载期被 core 纠正过的项，开窗时回显一次（保存后的回显走 self.notes）
        self.notes_from_load = list(self.cfg.clamp_report())
        self.protocol("WM_DELETE_WINDOW", self.to_tray)
        self.build()
        self.wire_controls()
        self.refresh()
        # 无边框窗口本来没有键盘通路：Ctrl+S 保存、Esc 收起、输入框回车保存。
        # 前提是这个窗口拿到过焦点 —— 由 main 的 open_settings() 在用户主动
        # 点开时负责把它带到前台（提醒弹窗不抢，见 ReminderWindow）。
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-S>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.to_tray())
        if self.cfg.corrupt_backup:
            self.flash("配置文件读取失败，这次用的是默认值；原文件已另存 %s"
                       % os.path.basename(self.cfg.corrupt_backup), warn=True)
        elif self.notes_from_load:
            self.flash("配置里已修正：" + "，".join(
                "%s %s→%s" % (core.fix_label(k), raw, eff)
                for k, raw, eff in self.notes_from_load), warn=True)

    def _entry(self, x, cy, name, default, w_px=72):
        C = self.C
        var = tk.StringVar(value=str(default))
        ent = tk.Entry(
            self, textvariable=var, justify="center",
            bg=C["field"], fg=C["text"], insertbackground=C["text"],
            relief="flat", highlightthickness=1,
            highlightbackground=C["field_edge"], highlightcolor=self.C["accent"],
        )
        ent.configure(font=self.fs(10))
        ent.bind("<Return>", lambda e: self.save())
        self.canvas.create_window(self.u(x), self.u(cy), window=ent,
                                  width=self.u(w_px), height=self.u(28),
                                  anchor="w")
        self.entries[name] = var
        return ent

    def _label(self, x, y, text, size=10, anchor="w", color=None):
        return self.text(x, y, text, size=size,
                         color=color or self.C["sub"], anchor=anchor)

    def build(self):
        """
        布局按 8px 网格走：卡片 72/258，行距 36，控件高度 26~28。
        以前上半张卡片是 94/128/164/208 一套节奏、设置区又是 38 一套，
        看着就是"两块东西拼在一起"，不是同一个界面。
        """
        p = self.PAD
        W = self.WIDTH
        H = self.HEIGHT

        # ---- 标题栏 ----
        self.drop(p + 11, 26, 24)
        self.text(p + 30, 20, "喝水提醒", size=15, weight="bold")
        self.txt_sub = self.text(p + 30, 42, "", size=9, color=self.C["sub"])
        # — 收进托盘（窗口留着，再点秒开）；× 关掉窗口（释放内存，再点重建）
        # 两个按钮以前做同一件事，看着像个 bug。谁都不会因为点这里就把程序退出，
        # 退出只走右下角那颗"退出程序"。
        GlassButton(self, W - p - 78, 18, 34, 26, "—", self.to_tray,
                    primary=False, font_size=11)
        GlassButton(self, W - p - 40, 18, 34, 26, "×", self.close_window,
                    primary=False, font_size=12)
        self.enable_drag()

        # ---- 今日进度卡片 ----
        self.card(p, 72, W - p, 258, r=16)
        self._label(p + 20, 92, "今日进度")
        self.txt_total = self.text(p + 20, 126, "0", size=32, weight="bold")
        self.txt_goal = self.text(p + 104, 138, "/ 2,000 ml", size=11,
                                  color=self.C["sub"])
        self.txt_left = self.text(W - p - 20, 138, "", size=11,
                                  color=self.C["accent"], anchor="e")
        self._progress_bar = (p + 20, 162, W - p - 20, 174)
        self._label(p + 20, 210, "最近 7 天", size=9)
        self._chart_box = (p + 112, 188, W - p - 20, 222)
        self._chart_label_y = 234
        self.txt_streak = self.text(W - p - 20, 210, "", size=9,
                                    color=self.C["sub"], anchor="e")

        # ---- 状态条：现在到底是个什么状态 ----
        # 以前"暂停中""今天不提醒了"在界面上完全看不出来，用户只能猜，
        # 最常见的误解还是那句"它怎么不提醒我"。
        self.line(p, 274, W - p, 274)
        self.txt_state = self.text(p, 292, "", size=10, color=self.C["text"])
        self.txt_alive = self.text(p, 310, "", size=9, color=self.C["faint"])

        # ---- 设置区 ----
        self.text(p, 336, "设置", size=12, weight="bold")
        # 单位只改"显示"，输入框里的数永远是毫升。切到盎司的人如果看不到这句，
        # 会以为下面的 2000 也是盎司。
        self.txt_unit_note = self.text(p + 44, 339, "", size=9, color=self.C["faint"])
        rows = {"r1": 360, "r2": 396, "r3": 432, "r4": 468, "r5": 504, "r6": 540}
        cy = lambda r: rows[r] + 14

        self._label(p, cy("r1"), "工作时段")
        self._entry(p + 88, cy("r1"), "workStart", self.cfg.get("workStart"))
        self._label(p + 170, cy("r1"), "—")
        self._entry(p + 196, cy("r1"), "workEnd", self.cfg.get("workEnd"))
        self._label(p + 306, cy("r1"), "提醒间隔")
        self._entry(p + 392, cy("r1"), "intervalMinutes",
                    self.cfg.get("intervalMinutes"), 60)
        self._label(p + 458, cy("r1"), "分钟")

        self._label(p, cy("r2"), "每次喝水")
        self._entry(p + 88, cy("r2"), "amountPerReminder",
                    self.cfg.get("amountPerReminder"))
        self._label(p + 170, cy("r2"), "ml")
        self._label(p + 306, cy("r2"), "每日目标")
        self._entry(p + 392, cy("r2"), "dailyGoal", self.cfg.get("dailyGoal"), 72)
        self._label(p + 470, cy("r2"), "ml")

        self._label(p, cy("r3"), "稍后提醒")
        self._entry(p + 88, cy("r3"), "snoozeMinutes", self.cfg.get("snoozeMinutes"))
        self._label(p + 170, cy("r3"), "分钟")
        self._label(p + 306, cy("r3"), "只在周一至周五")
        self.toggles["workdaysOnly"] = Toggle(self, p + 430, rows["r3"],
                                              self.cfg.get("workdaysOnly", False))

        self._label(p, cy("r4"), "午休免打扰")
        self._entry(p + 88, cy("r4"), "lunchStart",
                    self.cfg.get("lunchBreak", {}).get("start"))
        self._label(p + 170, cy("r4"), "—")
        self._entry(p + 196, cy("r4"), "lunchEnd",
                    self.cfg.get("lunchBreak", {}).get("end"))
        self._label(p + 306, cy("r4"), "午休不打扰")
        self.toggles["lunchBreak"] = Toggle(self, p + 430, rows["r4"],
                                            self.cfg.get("lunchBreak", {}).get("enabled", True))

        self._label(p, cy("r5"), "开机自启")
        self.toggles["autoStart"] = Toggle(self, p + 88, rows["r5"],
                                           self.cfg.get("autoStart", False))
        self._label(p + 150, cy("r5"), "开着才会在你没注意时提醒")

        self._label(p, cy("r6"), "提醒声音")
        self.toggles["sound"] = Toggle(self, p + 88, rows["r6"],
                                       self.cfg.get("sound", True))
        self._label(p + 160, cy("r6"), "单位")
        self.segments["unit"] = Segments(self, p + 202, rows["r6"],
                                         [("ml", "ml"), ("oz", "oz")],
                                         self.cfg.get("unit", "ml"))
        self._label(p + 330, cy("r6"), "外观")
        self.segments["theme"] = Segments(self, p + 374, rows["r6"],
                                          [("auto", "跟随"), ("dark", "深色"),
                                           ("light", "浅色")],
                                          self.cfg.get("theme", "auto"), w=40)

        # ---- 提醒文案 ----
        self.text(p, 576, "提醒文案（一行一条，轮流显示不重样）", size=10,
                  color=self.C["sub"])
        self.msg_box = tk.Text(
            self, height=4, bg=self.C["field"], fg=self.C["text"],
            insertbackground=self.C["text"], relief="flat", wrap="word",
            highlightthickness=1,
            highlightbackground=self.C["field_edge"], highlightcolor=self.C["accent"],
            padx=int(12 * self.S), pady=int(8 * self.S),
        )
        self.msg_box.configure(font=self.fs(10))
        self.canvas.create_window(self.u(p), self.u(592), window=self.msg_box,
                                  anchor="nw", width=self.u(W - p * 2))
        self.msg_box.insert("1.0", "\n".join(self.cfg.get("messages", [])))

        # ---- 底部按钮（贴着窗口下沿，改上面的行距不会把它们顶出屏幕）----
        by = H - self.PAD - 38
        GlassButton(self, p, by, 108, 38, "保存", self.save, primary=True)
        GlassButton(self, p + 120, by, 108, 38, "测试提醒", self.test, primary=False)
        GlassButton(self, p + 240, by, 108, 38, "打开数据目录", self.open_dir,
                    primary=False, font_size=10)
        # 破坏性动作给单独一档颜色：以前它和"打开数据目录"长得一模一样，
        # 手滑一下常驻程序就没了，还没有任何地方告诉用户"关了就不提醒"。
        GlassButton(self, W - p - 92, by, 92, 38, "退出程序", self.quit_app,
                    primary=False, font_size=10, danger=True)


    def update_alive_hint(self):
        """自启没开就常驻一句提醒：这是"到点不提醒"最常见的原因。"""
        on = bool(self.toggles["autoStart"].value)
        txt = ("开机自启已开：重启电脑后会自动在后台运行" if on else
               "未开机自启：重启或注销后要手动打开才会提醒；进程没在跑的时候不会有任何提醒")
        self.canvas.itemconfigure(self.txt_alive, text=txt,
                                  fill=self.C["sub"] if on else self.C["warn"])

    def wire_controls(self):
        """
        把"改了立刻生效"的控件接上线：自启、声音、单位、外观。
        单位/外观是纯显示，改了当场重画就行；自启要写注册表；声音下次提醒生效。
        """
        self.toggles["autoStart"].command = self.on_autostart_toggle
        self.toggles["sound"].command = lambda v: self.save(quiet=True)
        self.segments["unit"].command = lambda v: self.save(quiet=True)
        self.segments["theme"].command = lambda v: self.save(quiet=True)

    def on_autostart_toggle(self, want):
        """自启只走 App 那一条通路：写失败要把开关收回，不能界面显示"已开"。"""
        actual = self.app.set_autostart(want)
        if bool(actual) != bool(want):
            self.flash("开机自启没能写进注册表（可能被安全软件拦了），开关已收回", warn=True)

    def sync_controls(self):
        """
        把控件状态对齐到配置。
        托盘菜单也能改自启，改完只刷新数字的话开关还停在旧位置，
        用户会以为"两边有一个是坏的"，于是又点一次把它翻回去。
        """
        lb = self.cfg.get("lunchBreak", {}) or {}
        values = {
            "lunchBreak": bool(lb.get("enabled", True)),
            "workdaysOnly": bool(self.cfg.get("workdaysOnly")),
            "autoStart": bool(self.cfg.get("autoStart")),
            "sound": bool(self.cfg.get("sound", True)),
        }
        for name, value in values.items():
            tog = self.toggles.get(name)
            if tog is None or tog.value == value:
                continue
            tog.value = value
            tog.render()
        for name, fallback in (("unit", "ml"), ("theme", "auto")):
            seg = self.segments.get(name)
            if seg is not None and seg.value != self.cfg.get(name, fallback):
                seg.set(self.cfg.get(name, fallback))

    def refresh(self):
        cfg, unit = self.cfg, core.unit_label(self.cfg)
        self.sync_controls()
        total = self.app.store.total()
        goal = cfg.get("dailyGoal", 2000)
        self.canvas.itemconfigure(self.txt_sub,
                                  text="v%s · 只在工作时段打扰你 · %s" % (
                                      core.VERSION, core.fmt_window(cfg)),
                                  fill=self.C["sub"])
        self.canvas.itemconfigure(
            self.txt_unit_note,
            text="" if unit == "ml" else "下面的数字按毫升填，进度和提醒按 %s 显示" % unit,
            fill=self.C["faint"])
        self.canvas.itemconfigure(self.txt_total, text=core.to_display(cfg, total))
        self.canvas.itemconfigure(self.txt_goal,
                                  text="/ %s %s" % (core.to_display(cfg, goal), unit))
        # 大数字宽度随位数变化，目标文字要跟着挪
        gap = self.u(10)
        self.canvas.coords(
            self.txt_goal,
            self.u(self.PAD + 20) + self.fobj(32, "bold").measure(
                core.to_display(cfg, total)) + gap,
            self.u(138),
        )
        left = max(0, goal - total)
        self.canvas.itemconfigure(
            self.txt_left,
            text=("还差 %s %s" % (core.to_display(cfg, left), unit)) if left
            else "今日达标",
            fill=self.C["accent"] if left else self.C["ok"],
        )
        streak = self.app.store.streak(goal)
        self.canvas.itemconfigure(
            self.txt_streak,
            text=("连续达标 %d 天" % streak) if streak else "",
            fill=self.C["ok"] if streak >= 3 else self.C["sub"],
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
        self.update_state_line()

    def update_state_line(self):
        """
        一句话说清"现在的状态 + 下一杯什么时候"。
        这条是整轮排查里最便宜也最值钱的一处：所有"它怎么不提醒我"最后
        都是用户看不见程序处于什么状态。
        """
        self.canvas.itemconfigure(
            self.txt_state,
            text=self.app.status_line(now=dt.datetime.now()),
            fill=self.C["text"],
        )

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
                color, ratio = self.C["bar_empty"], 0.06
            elif met:
                color, ratio = self.C["ok"], min(1.0, val / peak)
            else:
                color = self.C["accent"] if is_today else self.C["bar"]
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
                font=self.fs(8, "bold" if is_today else "normal"),
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
                # 把纠正后的值写回输入框：不然框里一直是 9999，
                # 用户以为"它把我的值存下来了"，下次保存又被纠正一遍、再闪一次。
                self.entries[name].set(str(c))
            return c

        def hm_of(name, label, default):
            raw = str(self.entries[name].get()).strip()
            fixed = core.valid_hm(raw)
            padded = fixed is not None          # 值本身合法，只是没补零（9:00 → 09:00）
            if fixed is None:
                fixed = default
            if fixed != raw:
                if not padded:                  # 补零不值得专门闪一条"已修正"
                    self.notes.append("%s %s→%s" % (label, raw or "空", fixed))
                self.entries[name].set(fixed)
            return fixed

        data = self.cfg.data
        data["workStart"] = hm_of("workStart", core.TIME_LABELS["workStart"], "09:00")
        data["workEnd"] = hm_of("workEnd", core.TIME_LABELS["workEnd"], "18:00")
        for key, default, low, high in core.INT_RULES:
            data[key] = int_of(key, core.INT_LABELS[key], default, low, high)
        lb = self.cfg.get("lunchBreak", {})
        lb["enabled"] = bool(self.toggles["lunchBreak"].value)
        lb["start"] = hm_of("lunchStart", core.TIME_LABELS["lunchStart"], "12:00")
        lb["end"] = hm_of("lunchEnd", core.TIME_LABELS["lunchEnd"], "13:00")
        data["lunchBreak"] = lb
        data["workdaysOnly"] = bool(self.toggles["workdaysOnly"].value)
        data["autoStart"] = bool(self.toggles["autoStart"].value)
        data["sound"] = bool(self.toggles["sound"].value)
        data["unit"] = self.segments["unit"].value
        data["theme"] = self.segments["theme"].value

        raw = self.msg_box.get("1.0", "end").strip()
        msgs = [line.strip() for line in raw.splitlines() if line.strip()]
        if msgs:
            data["messages"] = msgs
        # 本次的值当作"原始值"，下次加载时才只报"用户手写的那一次"的越界
        self.cfg.raw = dict(data)
        self.cfg.fixes = []
        return data

    def save(self, quiet=False):
        """
        quiet=True 给"改了立即生效"的控件用（声音/单位/外观）：
        这些不该每次都糊一条"已保存"，闪在原地太吵。
        """
        dark_before = self._dark
        self.collect()
        # 必须在 collect() 之后再比：collect() 才会把控件上的新外观写进 cfg，
        # 之前在这里比的是"改之前"的 cfg，结果选了浅色窗口还是深色，
        # 要再保存一次才换过来。
        changed_theme = resolve_dark(self.cfg) != dark_before
        self.cfg.save()
        self.app.on_config_saved()
        if changed_theme:
            # 配色是建窗时烤进画布的，换主题只能重建窗口
            self.app.reopen_settings()
            return
        self.refresh()
        if self.notes:
            self.flash("已保存（已修正：" + "，".join(self.notes) + "）", warn=True)
        elif not quiet:
            self.flash("已保存，设置立即生效")

    def flash(self, message, warn=False):
        item = self.text(self.PAD, self.HEIGHT - self.PAD - 54, message, size=9,
                         color=self.C["warn"] if warn else self.C["ok"])
        self.after(2600, lambda: self.canvas.delete(item))

    def test(self):
        self.app.show_reminder_now()

    def open_dir(self):
        self.app.open_data_dir()

    def quit_app(self):
        self.app.quit_from_ui()

    def to_tray(self):
        """— 收进托盘：窗口保留，再点图标秒开。"""
        self.withdraw()
        self.app.on_settings_hidden()

    def close_window(self):
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

    def __init__(self, master, text, seconds=6, width=340, lift=0, cfg=None):
        GlassWindow.__init__(self, master, width, 56,
                             dark=resolve_dark(cfg), topmost=True)
        self.canvas.delete("surface")
        c = self.C
        # 提示条比主窗口小，圆角按物理 10px 走，和系统观感一致
        r = max(4, min(int(self.S * 10), (self.height - 2) / 2.0))
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
