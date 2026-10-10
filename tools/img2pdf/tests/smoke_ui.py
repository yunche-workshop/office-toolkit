# -*- coding: utf-8 -*-
"""
smoke_ui.py —— 图片转 PDF 的界面冒烟

跑法（要 tkinter，别用没装 Tk 的那个 python）：
    .../envs/tk38/Scripts/python.exe tests/smoke_ui.py

只做四类事：窗口能不能建起来、列表/参数/按钮的接线对不对、
一次真转换能不能从头走到尾、署名和关于窗还在不在。
像素级的排版对不对要靠截图（tests/e2e_shot.py 出文章配图时顺手验），这里不管。

不弹任何窗：messagebox 全程被换成记录器，剪贴板用完还原。
"""

import os
import shutil
import sys
import tempfile
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

import brand            # noqa: E402
import core             # noqa: E402
import ui               # noqa: E402
import win32ext         # noqa: E402
from test_core import jpeg_bytes, png_bytes  # noqa: E402

ok = 0
bad = []


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ok   %s" % name)
    else:
        bad.append(name)
        print("  FAIL %s %s" % (name, extra))


class Recorder(object):
    """
    把 messagebox 全接住：冒烟脚本里一弹窗就卡住了。

    ui.py 用的是 messagebox.showXxx(...)，所以这里得同名给一遍 ——
    少给一个方法，弹窗就变成 AttributeError，第 8 节会误报成"没弹警告窗"。
    """

    def __init__(self):
        self.calls = []

    def _rec(self, kind, title, message):
        self.calls.append((kind, title, str(message)))
        return True

    def showinfo(self, title, message):
        return self._rec("info", title, message)

    def showwarning(self, title, message):
        return self._rec("warning", title, message)

    def showerror(self, title, message):
        return self._rec("error", title, message)

    def askyesno(self, title, message):
        self._rec("askyesno", title, message)
        return True

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]

    @property
    def titles(self):
        return [c[1] for c in self.calls]


def make_images(dir_):
    """两张真图：一张 PNG（RGB8）、一张 JPEG（假码流但头是合规的）。"""
    p1 = os.path.join(dir_, "01.png")
    with open(p1, "wb") as fh:
        fh.write(png_bytes(30, 20, 8, 2,
                           [bytes([(c + r) % 256 for c in range(90)])
                            for r in range(20)]))
    p2 = os.path.join(dir_, "02.jpg")
    with open(p2, "wb") as fh:
        fh.write(jpeg_bytes(w=320, h=200, jfif_dpi=300))
    p3 = os.path.join(dir_, "坏文件.jpg")
    with open(p3, "wb") as fh:
        fh.write("这不是 JPEG，只是一段中文".encode("utf-8"))
    return p1, p2, p3


def pump(root, seconds=20.0):
    """等后台线程出结果：转完 _poll 会把 worker 置回 None。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        root.update()
        if app.worker is None and not app.q.empty():
            root.update()
            return True
        if app.worker is None:
            return True
        time.sleep(0.02)
    return False


def row_values():
    tv = app.tree
    return [tv.item(iid, "values") for iid in tv.get_children()]


def main():
    global app
    win32ext.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    rec = Recorder()
    real_box = ui.messagebox
    ui.messagebox = rec                      # 全程不许真弹窗
    try:
        app = ui.App(root)

        print("1) 界面构建")
        check("窗口建起来了", root.winfo_exists())
        check("标题是「图片转 PDF」", root.title() == "图片转 PDF", root.title())
        check("列表控件在", app.tree is not None)
        check("窗口图标换成了自己的（不是 Tk 羽毛）", app._photo is not None)
        byline = app.credit.cget("text")
        check("底部有常驻署名", brand.WORKSHOP in byline, byline)
        check("署名行带工具名和版本号",
              "图片转 PDF" in byline and core.VERSION in byline, byline)
        check("没文件时提示怎么开始",
              "先添加图片" in app.summary.cget("text"),
              app.summary.cget("text"))
        check("空列表时那行提示是「还没有添加图片」",
              "还没有添加图片" in app.list_label.cget("text"),
              app.list_label.cget("text"))
        check("还没转过时「打开所在文件夹」是灰的",
              str(app.open_btn.cget("state")) == "disabled")

        tmp = tempfile.mkdtemp(prefix="i2p_smoke_")
        png, jpg, junk = make_images(tmp)
        try:
            print("2) 加图片与列表")
            app._add([png, jpg])
            rows = row_values()
            check("列表两行", len(rows) == 2, len(rows))
            check("序号从 1 开始且跟着列表走",
                  [r[0] for r in rows] == ["1", "2"], [r[0] for r in rows])
            check("第一行是那张 PNG 且读出了规格",
                  "01.png" in str(rows[0][1]) and "PNG 30x20" in str(rows[0][2]),
                  rows[0])
            check("JPEG 报了真实尺寸", "320x200" in str(rows[1][2]), rows[1])
            check("JPEG 的 EXIF/JFIF DPI 上了界面（按 300 DPI）",
                  "300 DPI" in str(rows[1][2]), rows[1])
            check("大小那一列不是空", str(rows[0][3]) != "", rows[0][3])
            # 列表上方那行提示以前写死"还没有添加图片"，加了图它也不换
            # （是截图才看见的）。所以空/非空两种文案都得验，张数还得跟得上
            check("加了图之后那行不再说「还没有添加图片」",
                  "还没有" not in app.list_label.cget("text"),
                  app.list_label.cget("text"))
            check("提示行报的张数和列表一致",
                  "共 2 张" in app.list_label.cget("text"),
                  app.list_label.cget("text"))
            keep = list(app.paths)
            app.clear()
            check("清空后那行提示回到「还没有添加图片」",
                  "还没有添加图片" in app.list_label.cget("text"),
                  app.list_label.cget("text"))
            app._add(keep)
            check("清空再装回来，列表和提示行一起复原",
                  len(row_values()) == 2 and "共 2 张" in app.list_label.cget("text"),
                  app.list_label.cget("text"))
            app._add([png])
            check("重复添加同一个文件不会多一行",
                  len(row_values()) == 2, len(row_values()))
            check("摘要说了张数和去向", "2 张" in app.summary.cget("text"),
                  app.summary.cget("text"))

            print("3) 排序与删除")
            app.tree.selection_set("0")
            app.move(1)
            check("下移换了顺序", [r[1] for r in row_values()] ==
                  ["02.jpg", "01.png"], [r[1] for r in row_values()])
            # 下移之后 01.png 在第 2 行（iid "1"），换回来要选它、往上走
            app.tree.selection_set("1")
            app.move(-1)
            check("上移能换回来", [r[1] for r in row_values()] ==
                  ["01.png", "02.jpg"], [r[1] for r in row_values()])
            app.tree.selection_set("1")
            app.remove_selected()
            check("删除选中的那一行", [r[1] for r in row_values()] == ["01.png"],
                  [r[1] for r in row_values()])
            app._add([jpg])
            check("补回来还是两行", len(row_values()) == 2, len(row_values()))

            print("4) 坏文件在列表里就说清楚")
            app._add([junk])
            row3 = row_values()[2]
            check("读不出来的行写明原因而不是空白",
                  "读不出来" in str(row3[2]), row3)
            app.paths.remove(junk)
            app._refresh()

            print("5) 参数收集")
            opts = app.collect()
            check("默认是按图片大小 + 边距 28 磅",
                  opts["page"] == "image" and opts["margin"] == 28.0, opts)
            check("DPI 留空 = 自动（None）", opts["dpi"] is None, opts)
            app.vars["page"].set(ui.PAPER_CN["A4"])
            check("选 A4 后方向控件解锁",
                  str(app.orient_cb.cget("state")) == "readonly",
                  app.orient_cb.cget("state"))
            app.vars["page"].set(ui.PAPER_CN["image"])
            check("切回按图片大小，方向控件该灰掉",
                  str(app.orient_cb.cget("state")) == "disabled",
                  app.orient_cb.cget("state"))
            app.vars["dpi"].set("不是数字")
            try:
                app.collect()
                check("DPI 填错要报错", False, "没抛异常")
            except ValueError as exc:
                check("DPI 填错要报错", "DPI" in str(exc), str(exc))
            # 填错不能只是"抛个没人接的异常"：摘要行要当场把话说明白，
            # 否则用户只会觉得点了没反应
            app._refresh_summary()
            check("填错时摘要变黄字提示而不是崩",
                  "DPI" in app.summary.cget("text"),
                  app.summary.cget("text"))
            app.vars["dpi"].set("99999")
            try:
                app.collect()
                check("DPI 离谱也要拦", False, "没抛异常")
            except ValueError as exc:
                check("DPI 离谱也要拦", "2400" in str(exc), str(exc))
            app.vars["dpi"].set("")
            app.vars["page"].set(ui.PAPER_CN["A4"])
            app.vars["margin"].set(20)
            app.vars["orient"].set(ui.ORIENT_CN["landscape"])
            check("参数能读回成内核要的英文",
                  app.collect()["orient"] == "landscape", app.collect())
            app._refresh_summary()
            check("参数改对之后摘要自己回到正常口吻",
                  "DPI" not in app.summary.cget("text") and
                  "横" in app.summary.cget("text"),
                  app.summary.cget("text"))

            print("6) 输出路径")
            check("没填输出时给的建议落在图旁边（取首个文件名、去掉扩展名）",
                  app.suggested_out().endswith("01等2张.pdf"),
                  app.suggested_out())
            out = os.path.join(tmp, "出的 pdf.pdf")
            app.vars["out"].set(out)

            print("7) 真转换（后台线程 + 队列回主线程）")
            app.do_convert()
            check("点下转换按钮按钮立刻禁用（防手抖连点）",
                  str(app.go.cget("state")) == "disabled")
            check("转换中状态行有字", "正在" in app.summary.cget("text"),
                  app.summary.cget("text"))
            pump(root)
            check("PDF 真写出来了", os.path.exists(out))
            with open(out, "rb") as fh:
                blob = fh.read()
            check("产物是 PDF", blob[:5] == b"%PDF-", blob[:8])
            check("两页", blob.count(b"/Type /Page ") == 2,
                  blob.count(b"/Type /Page "))
            check("A4 横版尺寸进了 MediaBox",
                  b"/MediaBox [0 0 841.89 595.28]" in blob)
            check("JPEG 那页是原样封装", b"/DCTDecode" in blob)
            check("转换完了按钮恢复", str(app.go.cget("state")) == "normal")
            check("状态行报了页数和大小", "2 页" in app.summary.cget("text"),
                  app.summary.cget("text"))
            check("成功后「打开所在文件夹」解锁",
                  str(app.open_btn.cget("state")) == "normal")
            check("没弹任何错误窗", not rec.of("error"), rec.titles)

            print("8) 坏文件混在里面：跳过而不是全崩")
            app._add([junk])
            app.vars["out"].set(os.path.join(tmp, "mixed.pdf"))
            app.do_convert()
            pump(root)
            check("跳过的文件有警告窗", bool(rec.of("warning")), rec.titles)
            check("警告窗里点名了那个文件",
                  any("坏文件" in c[2] for c in rec.of("warning")),
                  [c[2][:80] for c in rec.of("warning")])
            check("状态行也写了跳过", "跳过" in app.summary.cget("text"),
                  app.summary.cget("text"))
            with open(app.vars["out"].get(), "rb") as fh:
                check("产物只有能读的那 2 页",
                      fh.read().count(b"/Type /Page ") == 2)
            app.paths.remove(junk)
            app._refresh()
            rec.calls = []

            print("9) 转换前的白痴检查")
            saved = list(app.paths)
            empty_out = os.path.join(tmp, "不该存在.pdf")
            app.paths = []
            app.vars["out"].set(empty_out)
            app._refresh()
            app.do_convert()
            check("一张图都没有：只提示不干活", bool(rec.of("info")),
                  rec.titles)
            check("拦下来之后真的没写文件", not os.path.exists(empty_out))
            check("按钮没被卡在转换中", str(app.go.cget("state")) == "normal",
                  app.go.cget("state"))
            app.paths = saved
            app.vars["out"].set(out)
            app._refresh()
            rec.calls = []
            app.vars["out"].set(os.path.join(tmp, "不存在的方向", "x.pdf"))
            app.do_convert()
            check("输出目录不存在要拦下来", bool(rec.of("warning")),
                  rec.titles)
            app.vars["out"].set(out)

            print("10) probe 只读头部，而且真的被缓存")
            calls = []
            real_probe = core.probe

            def counting(path, *a, **kw):
                calls.append(path)
                return real_probe(path, *a, **kw)

            core.probe = counting
            app._cache.clear()
            app._refresh()
            first = len(calls)
            app._refresh()
            app._refresh()
            core.probe = real_probe
            check("每张图只探一次（列表刷新不重读盘）",
                  first == len(app.paths) and len(calls) == first,
                  "%d 次 / %d 张" % (len(calls), first))

            print("11) 关于窗")
            old_clip = None
            try:
                old_clip = root.clipboard_get()
            except Exception:
                pass
            dialog = app.show_about()
            root.update()
            check("点署名能打开关于窗", dialog is not None and dialog.winfo_exists())
            check("重复点不叠第二扇", app.show_about() is dialog)
            blob_text = " | ".join(
                str(c.cget("text")) for c in _labels(dialog))
            check("关于窗里有品牌名", brand.WORKSHOP in blob_text)
            check("关于窗里有仓库地址", brand.repo_display() in blob_text)
            check("显示的那行不带协议头", "https://" not in blob_text)
            check("简介用的是 core.SUMMARY（不在界面里另写一版）",
                  core.SUMMARY in blob_text, core.SUMMARY)
            check("关于窗里有版本号和协议",
                  core.VERSION in blob_text and "MIT" in blob_text)
            dialog.copy_repo()
            root.update()
            try:
                got = root.clipboard_get()
            except Exception:
                got = None
            check("「复制仓库地址」真的进了剪贴板", got == brand.REPO_URL,
                  repr(got)[:60])
            check("复制后有回显", "已复制" in dialog.hint.cget("text"),
                  dialog.hint.cget("text"))
            # 关窗要"先销毁 dialog 再问它在不在"，也不能先销毁 root：
            # root 没了之后 dialog.winfo_exists() 不是返回 False，而是抛 TclError
            dialog.destroy()
            root.update()
            check("关于窗能关掉", not dialog.winfo_exists())
            if old_clip is not None:
                try:
                    root.clipboard_clear()
                    root.clipboard_append(old_clip)
                except Exception:
                    pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        ui.messagebox = real_box
        try:
            if root.winfo_exists():
                root.destroy()
        except Exception:
            pass

    print("\n通过 %d 项" % ok)
    if bad:
        print("失败 %d 项：%s" % (len(bad), ", ".join(bad)))
        return 1
    print("界面冒烟全过")
    return 0


def _labels(widget):
    out = []
    try:
        children = widget.winfo_children()
    except Exception:
        return out
    from tkinter import ttk
    for child in children:
        if isinstance(child, (tk.Label, ttk.Label)):
            out.append(child)
        out.extend(_labels(child))
    return out


if __name__ == "__main__":
    sys.exit(main())
