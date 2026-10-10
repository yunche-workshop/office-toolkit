# -*- coding: utf-8 -*-
"""
ui.py —— 图片转 PDF 的 Tk 界面

风格跟批量重命名统一（深色实底 + 卡片 + 亮边），用标准窗口，
不做无边框自定义标题栏 —— 那套拖动在 Tk 里踩过崩溃的坑，没必要再踩。

所有尺寸和字号都过 win32ext.px()，200% 缩放的屏上才不会糊。

界面只干三件事：收文件、收参数、把 core.convert 叫起来。
**排版和写 PDF 一律不在这一层**，所以内核能脱开 Tk 单独测。
"""

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk, filedialog, messagebox

import brand
import core
import icon
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

PAPER_CN = {"image": "按图片大小", "A4": "A4", "A5": "A5", "Letter": "Letter"}
ORIENT_CN = {"auto": "自动（横图配横纸）", "portrait": "竖版",
             "landscape": "横版"}
PAPER_EN = dict((v, k) for k, v in PAPER_CN.items())
ORIENT_EN = dict((v, k) for k, v in ORIENT_CN.items())
FILE_TYPES = [("图片", "*.jpg *.jpeg *.png"), ("所有文件", "*.*")]


def _font(size, weight="normal"):
    """Tk 负数字号 = 像素高。DPI aware 后必须乘缩放因子，否则字小得离谱。"""
    return ("Microsoft YaHei UI", -int(round(size * win32ext.DPI_SCALE)), weight)


def _px(v):
    return win32ext.px(v)


def human(n):
    """字节数说人话：界面上给 1 248 576 这种数没人看得下去。"""
    if n >= 1024 * 1024:
        return "%.1f MB" % (n / 1024.0 / 1024.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d B" % n


class App(object):
    def __init__(self, root):
        self.root = root
        self.paths = []                # 顺序 = PDF 页序
        self.vars = {}
        self._cache = {}               # 路径 -> (规格, 大小)，见 _row_text
        self.q = queue.Queue()
        self.worker = None
        self._about = None             # 关于窗只允许一扇，重复点用 lift
        self.last_out = None

        self._setup_style()
        self._build()
        self._refresh()

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
        st.configure("Dim.TLabel", background=BG, foreground=FG2, font=_font(12))
        st.configure("Card.Dim.TLabel", background=CARD, foreground=FG2,
                     font=_font(12))
        st.configure("Head.TLabel", background=BG, foreground=FG,
                     font=_font(19, "bold"))
        st.configure("TEntry", fieldbackground=CARD2, foreground=FG,
                     insertcolor=FG, bordercolor=LINE, lightcolor=LINE,
                     darkcolor=LINE, padding=(6, 4))
        st.configure("TSpinbox", fieldbackground=CARD2, foreground=FG,
                     insertcolor=FG, bordercolor=LINE, arrowcolor=FG)
        st.configure("TCombobox", fieldbackground=CARD2, foreground=FG,
                     background=CARD2, bordercolor=LINE, arrowcolor=FG)
        # 坑：readonly 的 Combobox 不吃上面的 configure，必须用 map 单独配，
        # 不然就是系统默认白底 + 白字（批量重命名那版真机截图抓到过）
        st.map("TCombobox",
               fieldbackground=[("readonly", CARD2), ("disabled", CARD)],
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
        st.map("Go.TButton", background=[("active", "#4a7bec"),
                                        ("disabled", LINE)])
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
        r.title(core.TOOL_CN)
        r.configure(bg=BG)
        r.geometry("%dx%d" % (_px(860), _px(640)))
        r.minsize(_px(700), _px(520))
        try:
            self._photo = tk.PhotoImage(data=icon.window_photo(32))
            r.iconphoto(True, self._photo)     # True = 往后的窗口（含关于窗）都用它
        except Exception:
            self._photo = None

        pad = _px(16)
        top = ttk.Frame(r, padding=(pad, _px(14), pad, 0))
        top.pack(fill="x")
        ttk.Label(top, text=core.TOOL_CN, style="Head.TLabel").pack(anchor="w")
        ttk.Label(top, text="本地转换，不联网 · JPEG 原样封装，不二次压缩不掉画质",
                  style="Dim.TLabel").pack(anchor="w", pady=(_px(2), 0))

        # 顺序要紧：底部工具条先 side="bottom" 占位，再让列表吃剩下的空间。
        # 反过来的话 expand=True 的列表会把按钮挤出可视区，只能全屏才看得见。
        self._build_bottom(r, pad)
        self._build_options(r, pad)
        self._build_list(r, pad)

        r.bind("<Control-o>", lambda _e: self.add_files())
        r.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_list(self, parent, pad):
        box = ttk.Frame(parent, style="Card.TFrame",
                        padding=(_px(12), _px(10)))
        box.pack(fill="both", expand=True, padx=pad, pady=(_px(14), 0))

        bar = ttk.Frame(box, style="Card.TFrame")
        bar.pack(fill="x")
        ttk.Button(bar, text="添加图片", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(
            side="left", padx=(_px(8), 0))
        ttk.Button(bar, text="下移", command=lambda: self.move(1)).pack(
            side="right")
        ttk.Button(bar, text="上移", command=lambda: self.move(-1)).pack(
            side="right", padx=(0, _px(8)))
        ttk.Button(bar, text="移除选中", command=self.remove_selected).pack(
            side="right", padx=(0, _px(12)))
        ttk.Button(bar, text="清空", command=self.clear).pack(
            side="right", padx=(0, _px(8)))

        self.list_label = ttk.Label(box, text="还没有添加图片",
                                    style="Card.Dim.TLabel")
        self.list_label.pack(anchor="w", pady=(_px(8), _px(4)))

        tv = ttk.Treeview(box, columns=("n", "name", "info", "size"),
                          show="headings", selectmode="extended")
        tv.heading("n", text="#")
        tv.heading("name", text="文件")
        tv.heading("info", text="规格")
        tv.heading("size", text="大小")
        tv.column("n", width=_px(44), anchor="center", stretch=False)
        tv.column("name", width=_px(280), anchor="w")
        tv.column("info", width=_px(300), anchor="w")
        tv.column("size", width=_px(80), anchor="e", stretch=False)
        tv.pack(fill="both", expand=True)
        tv.bind("<<TreeviewSelect>>", lambda _e: self._refresh_summary())
        # Delete 绑在列表上而不是根窗口：焦点在 DPI 输入框里按删除键，
        # 不该把选中的图片一并删掉
        tv.bind("<Delete>", lambda _e: self.remove_selected())
        self.tree = tv

    def _build_options(self, parent, pad):
        box = ttk.Frame(parent, style="Card.TFrame",
                        padding=(_px(12), _px(10)))
        box.pack(side="bottom", fill="x", padx=pad, pady=(_px(12), 0))

        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(fill="x")
        self.vars["page"] = tk.StringVar(value=PAPER_CN["image"])
        self.vars["orient"] = tk.StringVar(value=ORIENT_CN["auto"])
        self.vars["margin"] = tk.IntVar(value=int(core.DEFAULT_MARGIN))
        self.vars["dpi"] = tk.StringVar(value="")
        self.vars["title"] = tk.StringVar(value="")

        def label(text):
            ttk.Label(row, text=text, style="Card.TLabel").pack(
                side="left", padx=(0, _px(6)))

        def combo(var, values, width, cb=None):
            c = ttk.Combobox(row, textvariable=var, values=values,
                             state="readonly", width=width)
            c.pack(side="left")
            if cb:
                c.bind("<<ComboboxSelected>>", lambda _e: cb())
            return c

        label("纸张")
        self.page_cb = combo(self.vars["page"],
                             [PAPER_CN[k] for k in core.PAGE_MODES], 12)
        # 绑变量而不是只绑下拉框的选中事件：这样"从配置里恢复""脚本改值"
        # 这些不走鼠标的路径也能把方向控件的灰/亮状态带对
        self.vars["page"].trace_add("write", lambda *_a: self._on_page())
        label("方向")
        self.orient_cb = combo(self.vars["orient"],
                               [ORIENT_CN[k] for k in core.ORIENTS], 16)
        label("边距")
        sp = ttk.Spinbox(row, from_=0, to=120, increment=4,
                         textvariable=self.vars["margin"], width=5)
        sp.pack(side="left")
        ttk.Label(row, text="磅", style="Card.Dim.TLabel").pack(
            side="left", padx=(_px(4), _px(14)))

        row2 = ttk.Frame(box, style="Card.TFrame")
        row2.pack(fill="x", pady=(_px(10), 0))
        ttk.Label(row2, text="打印 DPI", style="Card.TLabel").pack(
            side="left", padx=(0, _px(6)))
        e = ttk.Entry(row2, textvariable=self.vars["dpi"], width=8)
        e.pack(side="left")
        ttk.Label(row2, text="留空 = 读图里的 DPI，没有就按 %d；只影响「按图片大小」那页的物理尺寸"
                  % core.DEFAULT_DPI, style="Card.Dim.TLabel").pack(
            side="left", padx=(_px(8), _px(18)))
        ttk.Label(row2, text="文档标题", style="Card.TLabel").pack(
            side="left", padx=(0, _px(6)))
        ttk.Entry(row2, textvariable=self.vars["title"], width=26).pack(
            side="left")

        self._on_page()

    def _on_page(self):
        """"按图片大小"时方向没用（页面跟着图转），灰掉比留着让人猜强。"""
        st = "disabled" if self.vars["page"].get() == PAPER_CN["image"] else "readonly"
        try:
            self.orient_cb.configure(state=st)
        except Exception:
            pass
        self._refresh_summary()

    def _build_bottom(self, parent, pad):
        bar = ttk.Frame(parent, padding=(pad, _px(10), pad, _px(12)))
        bar.pack(side="bottom", fill="x")

        outrow = ttk.Frame(bar)
        outrow.pack(fill="x")
        ttk.Label(outrow, text="输出", style="Dim.TLabel").pack(side="left")
        self.vars["out"] = tk.StringVar(value="")
        ttk.Entry(outrow, textvariable=self.vars["out"]).pack(
            side="left", fill="x", expand=True, padx=(_px(8), _px(8)))
        ttk.Button(outrow, text="另存为…", command=self.pick_output).pack(side="right")

        self.summary = ttk.Label(bar, text="先添加图片", style="Dim.TLabel")
        self.summary.pack(anchor="w", pady=(_px(8), 0))

        btns = ttk.Frame(bar)
        btns.pack(fill="x", pady=(_px(8), 0))
        self.open_btn = ttk.Button(btns, text="打开所在文件夹",
                                   command=self.open_folder, state="disabled")
        self.open_btn.pack(side="left")
        self.go = ttk.Button(btns, text="开始转换", style="Go.TButton",
                             command=self.do_convert)
        self.go.pack(side="right")

        # 常驻署名：右下角一小行，点一下开关于窗（品牌/版本/仓库/协议）。
        # 用 tk.Label 不用 ttk.Label —— ttk 那种在深色底上点起来不像能按的东西。
        self.credit = tk.Label(btns, text=brand.credit_line(core.TOOL_CN, core.VERSION),
                               bg=BG, fg=FG2, font=_font(11), cursor="hand2")
        self.credit.pack(side="right", padx=(0, _px(18)))
        self.credit.bind("<Button-1>", lambda _e: self.show_about())
        self.credit.bind("<Enter>", lambda _e: self.credit.configure(fg=ACCENT))
        self.credit.bind("<Leave>", lambda _e: self.credit.configure(fg=FG2))

    # ------------------------------------------------------------ 列表操作

    def add_files(self):
        paths = filedialog.askopenfilenames(title="选择要转成 PDF 的图片",
                                            filetypes=FILE_TYPES)
        if paths:
            self._add(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(title="选择放图片的文件夹")
        if not d:
            return
        found = []
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if os.path.isfile(p) and core.ext_of(p) in core.SUPPORTED_EXTS:
                found.append(p)
        if not found:
            messagebox.showinfo("这个文件夹里没有能转的图",
                                "只认 %s，没往里递归。\n%s" % (
                                    " / ".join(core.SUPPORTED_EXTS), d))
            return
        self._add(found)

    def _add(self, paths):
        known = set(self.paths)
        for p in paths:
            p = os.path.abspath(p)
            if p not in known:
                self.paths.append(p)
                known.add(p)
        self._refresh()

    def remove_selected(self):
        """按 iid（就是下标字符串）反查，比边删边配对稳，不会错位。"""
        idx = set()
        for iid in self.tree.selection():
            try:
                idx.add(int(iid))
            except (TypeError, ValueError):
                pass
        if not idx:
            return
        self.paths = [p for i, p in enumerate(self.paths) if i not in idx]
        self._refresh()

    def clear(self):
        self.paths = []
        self._refresh()

    def move(self, step):
        """把选中的行整体上/下移一格。多选时按"挤进目标位"处理，够用且不怪。"""
        sel = sorted(int(i) for i in self.tree.selection())
        if not sel:
            return
        if step < 0:
            order = sel
        else:
            order = list(reversed(sel))
        for i in order:
            j = i + step
            if 0 <= j < len(self.paths):
                self.paths[i], self.paths[j] = self.paths[j], self.paths[i]
        self._refresh(select=[j for j in
                              [i + step for i in order] if 0 <= j < len(self.paths)])

    def pick_output(self):
        p = filedialog.asksaveasfilename(
            title="PDF 存到哪里", defaultextension=".pdf",
            initialfile=os.path.basename(self.suggested_out()) or "图片.pdf",
            filetypes=[("PDF", "*.pdf")])
        if p:
            self.vars["out"].set(os.path.abspath(p))
            self._refresh_summary()

    def suggested_out(self):
        return self.vars["out"].get().strip() or core.suggested_output(self.paths)

    # ------------------------------------------------------------ 参数收集

    def collect(self):
        """
        把控件读成 core.convert 的关键字参数。

        抛 ValueError（人话）而不抛 traceback：界面上是用户填错，不是程序坏了。
        """
        page = PAPER_EN.get(self.vars["page"].get(), "image")
        orient = ORIENT_EN.get(self.vars["orient"].get(), "auto")
        try:
            margin = float(self.vars["margin"].get())
        except (TypeError, ValueError):
            raise ValueError("边距得是个数字（0 到 120 之间的磅值）")
        if not (0 <= margin <= 200):
            raise ValueError("边距 %g 磅太离谱了，0 到 200 之间挑一个" % margin)
        raw = self.vars["dpi"].get().strip()
        dpi = None
        if raw and raw not in ("自动",):
            try:
                dpi = float(raw)
            except ValueError:
                raise ValueError("打印 DPI 得是数字，或者干脆留空让它自动")
            if not (30 <= dpi <= 2400):
                raise ValueError("打印 DPI 在 30 到 2400 之间才有意义（现在 %g）" % dpi)
        return {"page": page, "orient": orient, "margin": margin, "dpi": dpi,
                "title": self.vars["title"].get().strip()}

    # ------------------------------------------------------------ 刷新

    def _refresh(self, select=None):
        tv = self.tree
        tv.delete(*tv.get_children())
        for i, p in enumerate(self.paths):
            info, size = self._row_text(p)
            tv.insert("", "end", iid=str(i),
                      values=(i + 1, os.path.basename(p), info, size))
        # 列表上方那行提示：原来写死"还没有添加图片"，加了 6 张图它还在那儿
        # （截图才看见的）。空列表才说"还没有"，有图就顺手把页序怎么调说出来。
        if self.paths:
            self.list_label.configure(
                text="共 %d 张 · 一张图一页，选中后用「上移」「下移」调页序"
                     % len(self.paths))
        else:
            self.list_label.configure(text="还没有添加图片")
        if select:
            tv.selection_set([str(i) for i in select])
        self._refresh_summary()

    def _row_text(self, path):
        """
        那一行的"规格 + 大小"。

        走 core.probe()（只读头部）而不是 load()：后者要把每张图的扫描线
        解压、去滤波、再压一遍，一个文件夹的截图点下去界面就冻住了。
        探过的结果按路径缓存，加文件/删文件/上下移都只对新路径真读盘。
        """
        hit = self._cache.get(path)
        if hit is not None:
            return hit
        try:
            out = (core.describe(core.probe(path)), human(os.path.getsize(path)))
        except Exception as exc:
            out = ("读不出来：%s" % exc, "-")
        self._cache[path] = out
        return out

    def _refresh_summary(self):
        n = len(self.paths)
        try:
            opts = self.collect()
        except ValueError as exc:
            self.summary.configure(text=str(exc), foreground=WARN)
            return
        if n == 0:
            self.summary.configure(
                text="先添加图片（Ctrl+O）· 页面顺序就是下面的列表顺序",
                foreground=FG2)
            return
        out = self.suggested_out()
        tail = "→ %s" % os.path.basename(out) if out else "还没定输出路径"
        # 摘要里给中文标签（用户在下拉框看到的就是这几个字），
        # 别把内核的英文键 a4 / landscape 抖到界面上去
        if opts["page"] == "image":
            paper = "按图片各自大小"
        else:
            paper = "%s %s" % (self.vars["page"].get(),
                               "" if opts["orient"] == "auto"
                               else self.vars["orient"].get())
        self.summary.configure(
            text="%d 张 · 纸张 %s · 边距 %g 磅 · %s" % (n, paper, opts["margin"], tail),
            foreground=FG2)

    # ------------------------------------------------------------ 转换

    def do_convert(self):
        if self.worker is not None:
            return
        if not self.paths:
            messagebox.showinfo("还没有图片", "先点「添加图片」挑几张 JPG 或 PNG。")
            return
        try:
            opts = self.collect()
        except ValueError as exc:
            messagebox.showwarning("参数有问题", str(exc))
            return
        out = self.suggested_out()
        if not out:
            messagebox.showwarning("参数有问题", "输出路径是空的，点「另存为…」定一个。")
            return
        out = os.path.abspath(out)
        if os.path.splitext(out)[1].lower() != ".pdf":
            out += ".pdf"
        if not os.path.isdir(os.path.dirname(out)):
            messagebox.showwarning("参数有问题",
                                   "输出目录不存在：%s" % os.path.dirname(out))
            return
        if any(os.path.abspath(p) == out for p in self.paths):
            messagebox.showwarning("参数有问题", "输出的 PDF 不能是自己的输入图片。")
            return

        paths = list(self.paths)
        self.vars["out"].set(out)
        self.go.configure(state="disabled", text="转换中…")
        self.open_btn.configure(state="disabled")
        self.summary.configure(text="%d 张正在读图打包…" % len(paths),
                               foreground=ACCENT)
        self.root.update_idletasks()
        self.worker = threading.Thread(target=self._work,
                                       args=(paths, out, opts), daemon=True)
        self.worker.start()
        self.root.after(120, self._poll)

    def _work(self, paths, out, opts):
        """工作线程里只做非 UI 的事，结果一律丢回队列（Tk 不许跨线程摸控件）。"""
        try:
            self.q.put(("done", core.convert(paths, out, **opts)))
        except Exception as exc:
            self.q.put(("error", str(exc)))

    def _poll(self):
        try:
            kind, payload = self.q.get_nowait()
        except queue.Empty:
            self.root.after(120, self._poll)
            return
        self.worker = None
        self.go.configure(state="normal", text="开始转换")
        if kind == "error":
            self.summary.configure(text="转换失败：%s" % payload, foreground=BAD)
            messagebox.showerror("转换失败", str(payload))
            return
        self.last_out = payload["out"]
        self.open_btn.configure(state="normal")
        bad = payload["failed"]
        text = "好了：%d 页 · %s → %s" % (payload["pages"],
                                        human(payload["bytes"]),
                                        os.path.basename(payload["out"]))
        if bad:
            text += "（跳过 %d 张：%s）" % (len(bad), bad[0][0])
            messagebox.showwarning(
                "部分图片没转成",
                "成功 %d 张，另有 %d 张被跳过：\n\n%s" % (
                    payload["pages"], len(bad),
                    "\n".join("%s —— %s" % (n, r) for n, r in bad[:8])))
        self.summary.configure(text=text, foreground=OK if not bad else WARN)
        # 这里不能再 _refresh()：列表内容没变，而刷新会把"好了：N 页 · 多大"
        # 那句结果汇报重新算成参数摘要，用户点完转换就再也看不到产物信息了。
        # （第一版就栽在这儿，冒烟脚本第 7 节盯着它。）

    def open_folder(self):
        if not self.last_out:
            return
        try:
            os.startfile(os.path.dirname(self.last_out))   # noqa: 只在 Windows 上跑
        except Exception as exc:
            messagebox.showwarning("打不开文件夹", str(exc))

    def on_close(self):
        if self.worker is not None:
            if not messagebox.askyesno("正在转换", "现在退出这个 PDF 可能只写了一半，确定？"):
                return
        self.root.destroy()

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
        self._about = AboutDialog(self.root, core.TOOL_CN, core.VERSION,
                                  core.SUMMARY)
        return self._about


class AboutDialog(tk.Toplevel):
    """
    关于小窗：品牌署名 + 版本 + 仓库 + 协议。

    信息行和另外两个工具完全一样（brand.about_rows 是唯一来源），
    只有外壳不同：这个工具是标准窗口 + ttk，喝水提醒那扇是自定义无边框玻璃窗。
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

        card = ttk.Frame(self, style="Card.TFrame", padding=(_px(14), _px(12)))
        card.pack(fill="both", expand=True, padx=_px(20), pady=(_px(14), 0))
        wrap = _px(self.WIDTH - 210)      # 值那一列的排版宽度（超了就换行）
        self.url = brand.REPO_URL
        for i, (label, value, target) in enumerate(
                brand.about_rows(tool_name, version, summary)):
            tk.Label(card, text=label, bg=CARD, fg=FG2, font=_font(11),
                     anchor="w").grid(row=i, column=0, sticky="nw",
                                      pady=_px(4))
            widget = tk.Label(card, text=value, bg=CARD,
                              fg=ACCENT if target else FG, font=_font(12),
                              anchor="w", justify="left", wraplength=wrap)
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
        """压在父窗口正中，再钳一次防止落到屏幕外（窗口找不回来很难受）。"""
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
