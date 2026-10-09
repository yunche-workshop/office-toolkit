# -*- coding: utf-8 -*-
"""
ui.py —— 批量重命名的 Tk 界面

风格跟 water-reminder 统一（深色实底 + 卡片 + 亮边），但用标准窗口，
不做无边框自定义标题栏 —— 拖动那套在 Tk 里踩过崩溃的坑，没必要再踩。

所有尺寸和字号都过 win32ext.px()，200% 缩放的屏上才不会糊。
"""

import os
import tkinter as tk
import webbrowser
from tkinter import ttk, filedialog, messagebox

import brand
import core
import win32ext

BG = "#16161a"
CARD = "#1e1e24"
CARD2 = "#25252d"
LINE = "#2e2e38"
FG = "#e8e8ee"
FG2 = "#9a9aa8"
ACCENT = "#5b8cff"
OK = "#3ecf8e"
WARN = "#f5a623"
BAD = "#ff5f56"

STATUS_COLOR = {"ok": OK, "same": FG2, "dup": BAD,
                "illegal": BAD, "exists": WARN}
STATUS_TEXT = {"ok": "可改", "same": "无变化", "dup": "撞名",
               "illegal": "非法", "exists": "已存在"}


def _font(size, weight="normal"):
    """Tk 负数字号 = 像素高。DPI aware 后必须乘缩放因子，否则字会小得离谱。"""
    return ("Microsoft YaHei UI", -int(round(size * win32ext.DPI_SCALE)), weight)


def _px(v):
    return win32ext.px(v)


# 下拉框显示中文，规则里存英文，两边要能对上
CASE_CN = {"keep": "不变", "lower": "全小写",
           "upper": "全大写", "title": "每个词首字母"}
SIDE_CN = {"left": "开头", "right": "结尾"}
FIELD_CN = {"mtime": "修改时间", "ctime": "创建时间"}


def _pos_cn(kind, value):
    """加前缀/后缀在不同规则下文案不一样，但存的都是 prefix / suffix。"""
    if kind == "insert":
        return "加在前面" if value == "prefix" else "加在后面"
    if kind == "number":
        return "编号在前" if value == "prefix" else "编号在后"
    return "日期在前" if value == "prefix" else "日期在后"


def _checkbutton(parent, var, text=None, command=None, bg=CARD):
    """
    自绘勾选框。

    不用 ttk.Checkbutton 的原因：它的 indicator 是主题固定尺寸，
    在 200% 缩放的屏上不跟着放大（又小又糊），勾选后的颜色变化也不明显，
    真机上根本看不出勾没勾。这里自己画：勾上=蓝底白勾，没勾=深灰描边。
    """
    size = _px(20)
    holder = ttk.Frame(parent, style="Card.TFrame")
    c = tk.Canvas(holder, width=size, height=size, bg=bg,
                  highlightthickness=0, cursor="hand2")
    c.pack(side="left")

    def draw(*_a):
        c.delete("all")
        p = _px(2)
        if var.get():
            c.create_rectangle(p, p, size - p, size - p,
                               fill=ACCENT, outline=ACCENT, width=_px(2))
            c.create_line(size * 0.28, size * 0.52,
                          size * 0.44, size * 0.70,
                          size * 0.74, size * 0.32,
                          fill="#ffffff", width=_px(2))
        else:
            c.create_rectangle(p, p, size - p, size - p,
                               fill=CARD2, outline=LINE, width=_px(2))

    def toggle(_e=None):
        var.set(not var.get())
        if command:
            command()

    c.bind("<Button-1>", toggle)
    if text:
        lb = ttk.Label(holder, text=text, style="Card.TLabel", cursor="hand2")
        lb.pack(side="left", padx=(_px(6), 0))
        lb.bind("<Button-1>", toggle)
    var.trace_add("write", lambda *_a: draw())   # 值被代码改了也要重画
    draw()
    return holder


class App(object):
    def __init__(self, root):
        self.root = root
        self.files = []
        self.rules = core.default_rules()
        self.vars = {}
        self.rows = []
        self.undo_dirs = []
        self.keep_ext = tk.BooleanVar(value=True)
        self.recursive = tk.BooleanVar(value=False)
        self.ext_filter = tk.StringVar(value="")
        self.sort_by = tk.StringVar(value="名称")  # 初值必须是中文，否则下拉框显示裸的 "name"
        self._about = None             # 关于窗：只允许一扇，重复点用 lift

        self._setup_style()
        self._build()

    # ------------------------------------------------------------ 样式

    def _setup_style(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=FG, font=_font(13))
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("TLabel", background=BG, foreground=FG, font=_font(13))
        st.configure("Card.TLabel", background=CARD, foreground=FG)
        st.configure("Dim.TLabel", background=BG, foreground=FG2,
                     font=_font(12))
        st.configure("Head.TLabel", background=BG, foreground=FG,
                     font=_font(19, "bold"))
        st.configure("TCheckbutton", background=BG, foreground=FG,
                     font=_font(12), indicatorcolor=CARD2,
                     indicatorbackground=CARD2)
        st.map("TCheckbutton", background=[("active", BG)],
               indicatorcolor=[("selected", ACCENT)])
        st.configure("Card.TCheckbutton", background=CARD, foreground=FG,
                     font=_font(12), indicatorcolor=CARD2)
        st.map("Card.TCheckbutton", background=[("active", CARD)],
               indicatorcolor=[("selected", ACCENT)])
        st.configure("TEntry", fieldbackground=CARD2, foreground=FG,
                     insertcolor=FG, bordercolor=LINE, lightcolor=LINE,
                     darkcolor=LINE, padding=(6, 4))
        st.configure("TSpinbox", fieldbackground=CARD2, foreground=FG,
                     insertcolor=FG, bordercolor=LINE, arrowcolor=FG)
        st.configure("TCombobox", fieldbackground=CARD2, foreground=FG,
                     background=CARD2, bordercolor=LINE, arrowcolor=FG)
        # 坑：readonly 状态的 Combobox 不吃上面的 configure，必须用 map 单独配，
        # 否则就是系统默认白底 + 白字，肉眼只见一个白块（真机截图抓到过）
        st.map("TCombobox",
               fieldbackground=[("readonly", CARD2), ("disabled", CARD2)],
               foreground=[("readonly", FG), ("disabled", FG2)],
               selectbackground=[("readonly", CARD2)],
               selectforeground=[("readonly", FG)])
        st.configure("TButton", background=CARD2, foreground=FG,
                     bordercolor=LINE, font=_font(12), padding=(10, 6),
                     relief="flat")
        st.map("TButton", background=[("active", LINE), ("pressed", LINE)],
               foreground=[("disabled", FG2)])
        st.configure("Go.TButton", background=ACCENT, foreground="#ffffff",
                     bordercolor=ACCENT, font=_font(13, "bold"),
                     padding=(18, 8))
        st.map("Go.TButton", background=[("active", "#4a7bec")])
        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=FG, bordercolor=LINE, font=_font(12),
                     rowheight=_px(26))
        st.configure("Treeview.Heading", background=CARD2, foreground=FG2,
                     font=_font(12), bordercolor=LINE, relief="flat")
        st.map("Treeview", background=[("selected", "#2c3a5e")],
               foreground=[("selected", FG)])

    # ------------------------------------------------------------ 布局

    def _build(self):
        r = self.root
        r.title("批量重命名")
        r.configure(bg=BG)
        r.geometry("%dx%d" % (_px(980), _px(820)))
        r.minsize(_px(860), _px(620))

        pad = _px(16)
        top = ttk.Frame(r, padding=(pad, _px(14), pad, 0))
        top.pack(fill="x")

        ttk.Label(top, text="批量重命名", style="Head.TLabel").pack(anchor="w")
        ttk.Label(top, text="本地处理，不联网 · 改错了能一键撤销",
                  style="Dim.TLabel").pack(anchor="w", pady=(_px(2), 0))

        self._build_files(r, pad)
        self._build_rules(r, pad)
        # 顺序要紧：底部栏必须先用 side="bottom" 占住位置。
        # 反过来让预览区先 pack 的话，它的 expand=True 会把剩余空间全吃光，
        # 底部那排按钮就被挤出可视区——只能全屏才看得见。
        self._build_bottom(r, pad)
        self._build_preview(r, pad)

    def _build_files(self, parent, pad):
        box = ttk.Frame(parent, style="Card.TFrame",
                        padding=(_px(12), _px(10)))
        box.pack(fill="x", padx=pad, pady=(_px(14), 0))

        bar = ttk.Frame(box, style="Card.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="添加文件", command=self.add_files).pack(
            side="left")
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(
            side="left", padx=(_px(8), 0))
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(
            side="left", padx=(_px(8), 0))
        ttk.Button(bar, text="清空", command=self.clear_files).pack(
            side="left", padx=(_px(8), 0))

        _checkbutton(bar, self.recursive, text="含子文件夹",
                     command=self.refresh_preview).pack(
            side="left", padx=(_px(16), 0))
        ttk.Label(bar, text="只看后缀", style="Card.TLabel").pack(
            side="left", padx=(_px(16), 0))
        e = ttk.Entry(bar, textvariable=self.ext_filter, width=14)
        e.pack(side="left", padx=(_px(6), 0))
        e.bind("<Return>", lambda _e: self.refresh_preview())
        ttk.Label(bar, text="排序", style="Card.TLabel").pack(
            side="left", padx=(_px(12), 0))
        cb = ttk.Combobox(bar, textvariable=self.sort_by, width=8,
                          state="readonly",
                          values=("名称", "修改时间", "大小"))
        cb.pack(side="left", padx=(_px(6), 0))
        cb.bind("<<ComboboxSelected>>", lambda _e: self.refresh_preview())
        self.sort_map = {"名称": "name", "修改时间": "mtime", "大小": "size"}

        wrap = ttk.Frame(box, style="Card.TFrame")
        wrap.pack(fill="both", expand=True, pady=(_px(10), 0))
        lb = tk.Listbox(wrap, height=4, bg=CARD2, fg=FG, bd=0,
                        highlightthickness=0, selectbackground="#2c3a5e",
                        selectforeground=FG, font=_font(12),
                        activestyle="none")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox = lb
        self.file_label = ttk.Label(box, text="还没有添加文件",
                                    style="Card.TLabel", foreground=FG2)
        self.file_label.pack(anchor="w", pady=(_px(6), 0))

    def _build_rules(self, parent, pad):
        box = ttk.Frame(parent, style="Card.TFrame",
                        padding=(_px(12), _px(10)))
        box.pack(fill="x", padx=pad, pady=(_px(12), 0))
        ttk.Label(box, text="规则（从上往下依次执行，勾选的才生效）",
                  style="Card.TLabel").pack(anchor="w")
        ttk.Label(box, text="重设文件名支持 {name} {origin} {parent} {date}",
                  style="Dim.TLabel").pack(anchor="w", pady=(_px(2), 0))

        for rule in self.rules:
            self._rule_row(box, rule)

    def _rule_row(self, parent, rule):
        row = ttk.Frame(parent, style="Card.TFrame")
        row.pack(fill="x", pady=(_px(4), 0))
        kind = rule["kind"]
        v = {}
        self.vars[kind] = v

        en = tk.BooleanVar(value=rule.get("enabled", False))
        v["enabled"] = en
        _checkbutton(row, en, command=self.refresh_preview).pack(side="left")
        ttk.Label(row, text=core.rule_label(kind), width=10,
                  style="Card.TLabel", foreground=FG2).pack(side="left")

        if kind == "replace":
            v["find"] = tk.StringVar(value=rule["find"])
            v["to"] = tk.StringVar(value=rule["to"])
            v["case_sensitive"] = tk.BooleanVar(value=rule["case_sensitive"])
            ttk.Entry(row, textvariable=v["find"], width=18).pack(
                side="left", padx=(_px(4), 0))
            ttk.Label(row, text="→", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Entry(row, textvariable=v["to"], width=18).pack(
                side="left", padx=(_px(6), 0))
            _checkbutton(row, v["case_sensitive"], text="区分大小写").pack(
                side="left", padx=(_px(10), 0))

        elif kind == "regex":
            v["pattern"] = tk.StringVar(value=rule["pattern"])
            v["to"] = tk.StringVar(value=rule["to"])
            ttk.Entry(row, textvariable=v["pattern"], width=24).pack(
                side="left", padx=(_px(4), 0))
            ttk.Label(row, text="→", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Entry(row, textvariable=v["to"], width=18).pack(
                side="left", padx=(_px(6), 0))

        elif kind == "strip":
            v["n"] = tk.StringVar(value=str(rule["n"]))
            v["side"] = tk.StringVar(value=SIDE_CN.get(rule["side"], "开头"))
            ttk.Spinbox(row, from_=0, to=999, width=5,
                        textvariable=v["n"]).pack(side="left", padx=(_px(4), 0))
            ttk.Label(row, text="个字符（从", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Combobox(row, textvariable=v["side"], width=5,
                         state="readonly", values=("开头", "结尾")).pack(
                side="left", padx=(_px(4), 0))
            ttk.Label(row, text="）", style="Card.TLabel").pack(side="left")

        elif kind == "case":
            v["mode"] = tk.StringVar(value=CASE_CN.get(rule["mode"], "不变"))
            cb = ttk.Combobox(row, textvariable=v["mode"], width=12,
                              state="readonly",
                              values=("不变", "全小写", "全大写", "每个词首字母"))
            cb.pack(side="left", padx=(_px(4), 0))
            self.case_map = {"不变": "keep", "全小写": "lower",
                             "全大写": "upper", "每个词首字母": "title"}

        elif kind == "insert":
            v["text"] = tk.StringVar(value=rule["text"])
            v["pos"] = tk.StringVar(value=_pos_cn("insert", rule["pos"]))
            ttk.Entry(row, textvariable=v["text"], width=24).pack(
                side="left", padx=(_px(4), 0))
            ttk.Combobox(row, textvariable=v["pos"], width=6,
                         state="readonly", values=("加在前面", "加在后面")).pack(
                side="left", padx=(_px(8), 0))
            self.pos_map = {"加在前面": "prefix", "加在后面": "suffix"}

        elif kind == "number":
            v["start"] = tk.StringVar(value=str(rule["start"]))
            v["width"] = tk.StringVar(value=str(rule["width"]))
            v["step"] = tk.StringVar(value=str(rule["step"]))
            v["pos"] = tk.StringVar(value=_pos_cn("number", rule["pos"]))
            ttk.Label(row, text="从", style="Card.TLabel").pack(
                side="left", padx=(_px(4), 0))
            ttk.Spinbox(row, from_=0, to=999999, width=6,
                        textvariable=v["start"]).pack(side="left")
            ttk.Label(row, text="开始，每次 +", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Spinbox(row, from_=1, to=9999, width=5,
                        textvariable=v["step"]).pack(side="left")
            ttk.Label(row, text="，补足到", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Spinbox(row, from_=0, to=12, width=5,
                        textvariable=v["width"]).pack(side="left")
            ttk.Label(row, text="位（0=不补零）", style="Card.TLabel").pack(
                side="left", padx=(_px(6), 0))
            ttk.Combobox(row, textvariable=v["pos"], width=8,
                         state="readonly", values=("编号在前", "编号在后")).pack(
                side="left", padx=(_px(8), 0))

        elif kind == "date":
            v["field"] = tk.StringVar(value=FIELD_CN.get(rule["field"], "修改时间"))
            v["fmt"] = tk.StringVar(value=rule["fmt"])
            v["pos"] = tk.StringVar(value=_pos_cn("date", rule["pos"]))
            ttk.Combobox(row, textvariable=v["field"], width=10,
                         state="readonly", values=("修改时间", "创建时间")).pack(
                side="left", padx=(_px(4), 0))
            ttk.Entry(row, textvariable=v["fmt"], width=14).pack(
                side="left", padx=(_px(8), 0))
            ttk.Label(row, text="%Y年%m月%d日 例：20261008",
                      style="Card.TLabel", foreground=FG2).pack(
                side="left", padx=(_px(6), 0))
            ttk.Combobox(row, textvariable=v["pos"], width=8,
                         state="readonly", values=("日期在前", "日期在后")).pack(
                side="left", padx=(_px(8), 0))
            self.field_map = {"修改时间": "mtime", "创建时间": "ctime"}

        elif kind == "template":
            v["text"] = tk.StringVar(value=rule["text"])
            e = ttk.Entry(row, textvariable=v["text"], width=46)
            e.pack(side="left", padx=(_px(4), 0))
            self.tpl_entry = e

    def _build_preview(self, parent, pad):
        box = ttk.Frame(parent, style="Card.TFrame",
                        padding=(_px(12), _px(10)))
        box.pack(fill="both", expand=True, padx=pad, pady=(_px(12), 0))
        ttk.Label(box, text="预览（左：现在　右：改完之后）",
                  style="Card.TLabel").pack(anchor="w")

        wrap = ttk.Frame(box, style="Card.TFrame")
        wrap.pack(fill="both", expand=True, pady=(_px(8), 0))
        cols = ("old", "arrow", "new", "status")
        tv = ttk.Treeview(wrap, columns=cols, show="headings", height=6)
        tv.heading("old", text="现在叫什么")
        tv.heading("arrow", text="")
        tv.heading("new", text="改完之后")
        tv.heading("status", text="状态")
        tv.column("old", width=_px(340), anchor="w")
        tv.column("arrow", width=_px(30), anchor="center")
        tv.column("new", width=_px(340), anchor="w")
        tv.column("status", width=_px(150), anchor="w")
        sb = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for c, color in (("ok", OK), ("dup", BAD), ("illegal", BAD),
                         ("exists", WARN), ("same", FG2)):
            tv.tag_configure(c, foreground=color)
        self.tree = tv

    def _build_bottom(self, parent, pad):
        bar = ttk.Frame(parent, padding=(pad, _px(10), pad, _px(12)))
        bar.pack(side="bottom", fill="x")
        self.summary = ttk.Label(bar, text="先添加文件", style="Dim.TLabel")
        self.summary.pack(anchor="w")

        btns = ttk.Frame(bar)
        btns.pack(fill="x", pady=(_px(10), 0))
        ttk.Button(btns, text="刷新预览", command=self.refresh_preview).pack(
            side="left")
        ttk.Button(btns, text="撤销上一次", command=self.do_undo).pack(
            side="left", padx=(_px(8), 0))
        ttk.Button(btns, text="开始改名", style="Go.TButton",
                   command=self.do_rename).pack(side="right")
        _checkbutton(btns, self.keep_ext, text="保留扩展名",
                     command=self.refresh_preview).pack(
            side="right", padx=(0, _px(16)))
        # 常驻署名：右下角一小行，点一下开关于窗（品牌/版本/仓库/协议）。
        # 用 tk.Label 不用 ttk.Label —— ttk 那种在深色底上点起来不像能按的东西。
        self.credit = tk.Label(
            btns, text=brand.credit_line("批量重命名", core.VERSION),
            bg=BG, fg=FG2, font=_font(11), cursor="hand2")
        self.credit.pack(side="right", padx=(0, _px(18)))
        self.credit.bind("<Button-1>", lambda e: self.show_about())
        self.credit.bind("<Enter>", lambda e: self.credit.configure(fg=ACCENT))
        self.credit.bind("<Leave>", lambda e: self.credit.configure(fg=FG2))

    def show_about(self):
        """关于窗只开一扇：反复点署名不该在屏幕上叠出一排一模一样的窗。"""
        win = getattr(self, "_about", None)
        if win is not None:
            try:
                if win.winfo_exists():
                    win.lift()
                    return win
            except Exception:
                pass
            self._about = None
        self._about = AboutDialog(self.root, "批量重命名", core.VERSION,
                                  core.SUMMARY)
        return self._about

    # ------------------------------------------------------------ 文件

    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要改名的文件")
        if paths:
            self._add(list(paths))

    def add_folder(self):
        path = filedialog.askdirectory(title="选择文件夹")
        if path:
            self._add([path])

    def _add(self, paths):
        for p in paths:
            if p not in self.files:
                self.files.append(p)
        self.refresh_preview()

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        for i in reversed(sel):
            if 0 <= i < len(self.files):
                self.files.pop(i)
        self.refresh_preview()

    def clear_files(self):
        self.files = []
        self.refresh_preview()

    def _parse_exts(self):
        raw = self.ext_filter.get().strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        return ["." + p.lstrip(".").lower() for p in parts]

    def _rescan(self):
        return core.scan(self.files,
                         recursive=self.recursive.get(),
                         exts=self._parse_exts(),
                         sort_by=self.sort_map.get(self.sort_by.get(), "name"))

    def update_file_list(self, shown):
        lb = self.listbox
        lb.delete(0, "end")
        for p in shown:
            lb.insert("end", p)
        n = len(shown)
        if n == 0:
            self.file_label.configure(text="还没有添加文件")
        else:
            self.file_label.configure(text="共 %d 个文件" % n)

    # ------------------------------------------------------------ 规则收集

    def collect_rules(self):
        """把界面上的控件值读回规则列表。"""
        for rule in self.rules:
            v = self.vars.get(rule["kind"])
            if not v:
                continue
            rule["enabled"] = bool(v["enabled"].get())
            k = rule["kind"]
            try:
                if k == "replace":
                    rule["find"] = v["find"].get()
                    rule["to"] = v["to"].get()
                    rule["case_sensitive"] = bool(v["case_sensitive"].get())
                elif k == "regex":
                    rule["pattern"] = v["pattern"].get()
                    rule["to"] = v["to"].get()
                elif k == "strip":
                    rule["n"] = v["n"].get()
                    rule["side"] = ("right" if v["side"].get() == "结尾"
                                    else "left")
                elif k == "case":
                    rule["mode"] = self.case_map.get(v["mode"].get(), "keep")
                elif k == "insert":
                    rule["text"] = v["text"].get()
                    rule["pos"] = self.pos_map.get(v["pos"].get(), "prefix")
                elif k == "number":
                    rule["start"] = v["start"].get()
                    rule["width"] = v["width"].get()
                    rule["step"] = v["step"].get()
                    rule["pos"] = ("suffix" if v["pos"].get() == "编号在后"
                                   else "prefix")
                elif k == "date":
                    rule["field"] = self.field_map.get(v["field"].get(), "mtime")
                    rule["fmt"] = v["fmt"].get() or "%Y%m%d"
                    rule["pos"] = ("suffix" if v["pos"].get() == "日期在后"
                                   else "prefix")
                elif k == "template":
                    rule["text"] = v["text"].get() or "{name}"
            except Exception:
                continue
        return self.rules

    def _any_rule_on(self):
        return any(r.get("enabled") for r in self.rules)

    # ------------------------------------------------------------ 预览

    def refresh_preview(self):
        shown = self._rescan()
        self.update_file_list(shown)

        tv = self.tree
        for item in tv.get_children():
            tv.delete(item)

        if not shown:
            self.rows = []
            self.summary.configure(text="先添加文件")
            return

        rules = self.collect_rules()
        rows = core.plan(shown, rules, keep_ext=self.keep_ext.get())
        self.rows = rows

        for row in rows:
            tv.insert("", "end",
                      values=(row["old_name"], "→", row["new_name"],
                              STATUS_TEXT.get(row["status"], row["status"])),
                      tags=(row["status"],))

        ok = sum(1 for r in rows if r["status"] == "ok")
        dup = sum(1 for r in rows if r["status"] == "dup")
        bad = sum(1 for r in rows if r["status"] in ("illegal", "exists"))
        same = sum(1 for r in rows if r["status"] == "same")

        if not self._any_rule_on():
            text = "共 %d 个文件 · 还没有勾选任何规则" % len(rows)
        else:
            text = "共 %d 个 · 可改 %d 个 · 无变化 %d 个" % (
                len(rows), ok, same)
            if dup or bad:
                text += " · 有问题 %d 个（下面标红的不改）" % (dup + bad)
        self.summary.configure(text=text)

    # ------------------------------------------------------------ 执行

    def do_rename(self):
        self.refresh_preview()
        rows = self.rows
        todo = [r for r in rows if r["status"] == "ok"]
        if not todo:
            messagebox.showinfo("没得改",
                                "没有需要改名的文件。\n"
                                "要么没勾规则，要么新名字跟现在一样。")
            return

        blocked = len(rows) - len(todo)
        msg = "将要改名 %d 个文件。" % len(todo)
        if blocked:
            msg += "\n另有 %d 个因为撞名或目标已存在，会跳过不改。" % blocked
        msg += "\n\n改完之后可以用「撤销上一次」还原。"
        if not messagebox.askyesno("确认改名", msg):
            return

        log, done, failed = core.execute(rows)
        if not log:
            messagebox.showwarning("没改成",
                                   "一个都没改成功。\n%s" % (
                                       failed[0][1] if failed else ""))
            return

        # 按目录分组存撤销日志，撤销时逐个目录还原
        by_dir = {}
        for item in log["items"]:
            by_dir.setdefault(os.path.dirname(item["from"]), []).append(item)
        self.undo_dirs = []
        for folder, items in by_dir.items():
            try:
                core.save_undo(folder, {"time": log["time"],
                                        "count": len(items),
                                        "items": items})
                self.undo_dirs.append(folder)
            except Exception:
                pass

        self.files = [item["to"] for item in log["items"]]
        self.refresh_preview()

        tail = ""
        if failed:
            tail = "\n\n有 %d 个没成功，第一个原因：%s" % (
                len(failed), failed[0][1])
        messagebox.showinfo(
            "改完了",
            "成功改名 %d 个。\n撤销日志已存在这些文件所在的文件夹里，"
            "想还原就点「撤销上一次」。%s" % (done, tail))

    def do_undo(self):
        folders = self.undo_dirs or []
        if not folders:
            messagebox.showinfo("没有可撤销的",
                                "这次运行还没改过文件，没有撤销记录。")
            return

        total = 0
        problems = []
        for folder in folders:
            log = core.load_undo(folder)
            if not log:
                continue
            n, failed = core.undo(log)
            total += n
            problems.extend(failed)
            if n == len(log.get("items", [])):
                core.clear_undo(folder)
        self.undo_dirs = []

        # 还原后重新扫一遍列表
        self.refresh_preview()
        if problems:
            messagebox.showwarning(
                "撤销完成（有遗漏）",
                "还原了 %d 个。\n有 %d 个没能还原，第一个原因：%s" % (
                    total, len(problems), problems[0][1]))
        else:
            messagebox.showinfo("撤销完成", "已还原 %d 个文件。" % total)


class AboutDialog(tk.Toplevel):
    """
    关于小窗：品牌署名 + 版本 + 仓库 + 协议。

    信息行和 water-reminder 那扇完全一样（brand.about_rows 是唯一来源），
    只有外壳不同：这个工具是标准窗口 + ttk，那扇是自定义无边框玻璃窗。
    两扇共用一套文案，以后加工具就只多一份外壳、不用重写内容。
    """

    WIDTH = 520

    def __init__(self, parent, tool_name, version, summary):
        tk.Toplevel.__init__(self, parent)
        self.title("关于 · %s" % brand.WORKSHOP)
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        try:
            win32ext.set_round_corner(win32ext.get_hwnd(self))
        except Exception:
            pass

        head = ttk.Frame(self, padding=(_px(20), _px(16), _px(20), 0))
        head.pack(fill="x")
        ttk.Label(head, text=brand.WORKSHOP, style="Head.TLabel").pack(anchor="w")
        ttk.Label(head, text="office-toolkit · 内网办公小工具集",
                  style="Dim.TLabel").pack(anchor="w", pady=(_px(3), 0))

        card = ttk.Frame(self, style="Card.TFrame",
                         padding=(_px(14), _px(12)))
        card.pack(fill="both", expand=True, padx=_px(20), pady=(_px(14), 0))
        wrap = _px(self.WIDTH - 210)      # 值那一列的排版宽度（超了就换行）
        self.url = brand.REPO_URL
        for i, (label, value, target) in enumerate(
                brand.about_rows(tool_name, version, summary)):
            tk.Label(card, text=label, bg=CARD, fg=FG2, font=_font(11),
                     anchor="w").grid(row=i, column=0, sticky="nw",
                                      pady=_px(4))
            widget = tk.Label(
                card, text=value, bg=CARD, fg=ACCENT if target else FG,
                font=_font(12), anchor="w", justify="left", wraplength=wrap)
            if target:
                # 显示的那行不带协议头，点它/复制它拿到的都是完整地址
                self.url = target
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda _e: self.open_repo())
            widget.grid(row=i, column=1, sticky="w", padx=(_px(14), 0),
                        pady=_px(4))
        card.columnconfigure(1, weight=1)

        bar = ttk.Frame(self, padding=(_px(20), _px(10), _px(20), _px(18)))
        bar.pack(fill="x")
        # 左边是"已复制"的回显：以前点复制没有任何反馈，只能靠猜
        self.hint = tk.Label(bar, text="", bg=BG, fg=FG2, font=_font(11))
        self.hint.pack(side="left")
        ttk.Button(bar, text="打开仓库", style="Go.TButton",
                   command=self.open_repo).pack(side="right")
        ttk.Button(bar, text="复制仓库地址", command=self.copy_repo).pack(
            side="right", padx=(0, _px(10)))

        self.bind("<Escape>", lambda _e: self.destroy())
        # grab 要等窗口真的映射上来才设：自检脚本里父窗口是 withdraw 的，
        # 那时候直接 grab_set 会咬一口 TclError。
        self.after(80, self._apply_grab)
        self.after(60, self._center_over, parent)

    def _apply_grab(self):
        try:
            if self.winfo_viewable():
                self.grab_set()
        except Exception:
            pass

    def _center_over(self, parent):
        """压在父窗口正中，再钳一次防止落到屏幕外（无边框工具窗找不回来）。"""
        try:
            self.update_idletasks()
            w, h = self.winfo_width(), self.winfo_height()
            x = parent.winfo_x() + (parent.winfo_width() - w) // 2
            y = parent.winfo_y() + (parent.winfo_height() - h) // 2
            x, y = win32ext.clamp_into_view(x, y, w, h)
            self.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def copy_repo(self):
        """复制的是完整地址（带 https://），界面上那行只是省了协议头。"""
        try:
            self.clipboard_clear()
            self.clipboard_append(self.url)
            self.hint.configure(text="地址已复制")
        except Exception:
            self.hint.configure(text="复制失败，请手动选中")

    def open_repo(self):
        """只有用户明确点这颗按钮/这行地址才叫浏览器，程序自身不联网。"""
        try:
            webbrowser.open(self.url)
        except Exception:
            self.copy_repo()


def run(root):
    """由 main.py 调用：装界面 + 做窗口美化。"""
    app = App(root)

    def decorate():
        try:
            hwnd = win32ext.get_hwnd(root)
            win32ext.set_round_corner(hwnd)
            win32ext.set_window_shadow(hwnd)
        except Exception:
            pass

    root.after(120, decorate)
    return app
