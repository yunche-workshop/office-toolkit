# -*- coding: utf-8 -*-
"""
smoke_ui.py —— 界面冒烟（不弹窗，造完窗口就销毁）

验证：界面能建起来、规则能从控件读回、预览能算出结果。
用装了 tkinter 的那个 venv 跑：
    .../envs/tk38/Scripts/python.exe tests/smoke_ui.py
"""

import os
import math
import sys
import tempfile
import shutil
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import win32ext  # noqa: E402
import brand  # noqa: E402
import core  # noqa: E402
import ui  # noqa: E402

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


def _labels(widget):
    """递归收集所有 Label（tk 和 ttk 两种都要，关于窗的标题行是 ttk.Label）。"""
    out = []
    for child in widget.winfo_children():
        if isinstance(child, (tk.Label, ttk.Label)):
            out.append(child)
        out.extend(_labels(child))
    return out


def _lines(label):
    """这行文字按它自己的 wraplength 会被切成几行 —— 用 Tk 的度量，不靠猜。"""
    text = str(label.cget("text"))
    try:
        wrap = int(label.cget("wraplength"))
        f = tkfont.Font(font=label.cget("font"))
    except Exception:          # ttk.Label 根本没有 wraplength 这个选项
        return 1
    if wrap <= 0:
        return 1
    return max(1, int(math.ceil(f.measure(text) / float(wrap))))


def main():
    win32ext.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()

    app = ui.App(root)
    print("1) 界面构建")
    check("窗口建起来了", root.winfo_exists())
    check("8 条规则都建了行", len(app.rules) == 8, len(app.rules))
    check("规则变量齐全", len(app.vars) == 8, len(app.vars))

    tmp = tempfile.mkdtemp(prefix="brsmoke")
    files = []
    try:
        for name in ("IMG_001.jpg", "IMG_002.jpg", "IMG_010.jpg"):
            p = os.path.join(tmp, name)
            open(p, "w").close()
            files.append(p)

        print("2) 空状态")
        app.refresh_preview()
        check("没文件时 rows 为空", app.rows == [])

        print("3) 加文件 + 无规则")
        app.files = list(files)
        app.refresh_preview()
        check("扫到 3 个", len(app.rows) == 3, len(app.rows))
        check("没勾规则时都是无变化",
              all(r["status"] == "same" for r in app.rows))

        print("4) 勾选查找替换")
        app.vars["replace"]["enabled"].set(True)
        app.vars["replace"]["find"].set("IMG_")
        app.vars["replace"]["to"].set("照片")
        app.refresh_preview()
        check("新名已替换",
              [r["new_name"] for r in app.rows] ==
              ["照片001.jpg", "照片002.jpg", "照片010.jpg"],
              [r["new_name"] for r in app.rows])
        check("全部可改", all(r["status"] == "ok" for r in app.rows))

        print("5) 叠加编号（会撞名）")
        app.vars["template"]["enabled"].set(True)
        app.vars["template"]["text"].set("统一名字")
        app.refresh_preview()
        statuses = [r["status"] for r in app.rows]
        check("第一个 ok，其余撞名", statuses[0] == "ok" and
              all(s == "dup" for s in statuses[1:]), statuses)

        print("6) 关掉模板，改成编号在后")
        app.vars["template"]["enabled"].set(False)
        app.vars["number"]["enabled"].set(True)
        app.vars["number"]["start"].set("1")
        app.vars["number"]["width"].set("2")
        app.refresh_preview()
        check("编号生效",
              [r["new_name"] for r in app.rows] ==
              ["照片00101.jpg", "照片00202.jpg", "照片01003.jpg"],
              [r["new_name"] for r in app.rows])

        print("7) 扩展名过滤")
        app.ext_filter.set("jpg")
        app.refresh_preview()
        check("过滤后仍是 3 个", len(app.rows) == 3, len(app.rows))
        app.ext_filter.set("png")
        app.refresh_preview()
        check("换成 png 后 0 个", len(app.rows) == 0, len(app.rows))
        app.ext_filter.set("")

        print("8) 排序映射")
        app.sort_by.set("修改时间")
        app.refresh_preview()
        check("按时间排序不报错", len(app.rows) >= 0)

        print("9) 规则收集回读")
        rules = app.collect_rules()
        check("replace 规则读回", rules[0]["find"] == "IMG_", rules[0])
        check("number 的 pos 是后缀", rules[5]["pos"] == "suffix", rules[5])
        check("template 已关闭", rules[7]["enabled"] is False)

        print("10) 署名与关于窗")
        byline = app.credit.cget("text")
        check("底部有常驻署名", "允澈工坊" in byline, byline)
        check("署名行带工具名和版本号",
              "批量重命名" in byline and core.VERSION in byline, byline)
        dialog = app.show_about()
        root.update()
        check("点署名能打开关于窗",
              dialog is not None and dialog.winfo_exists())
        check("重复点不叠第二扇", app.show_about() is dialog)
        blob = " | ".join(str(c.cget("text")) for c in _labels(dialog))
        check("关于窗里有品牌名", brand.WORKSHOP in blob)
        check("关于窗里有仓库地址", brand.repo_display() in blob,
              brand.repo_display())
        check("显示的那行不带协议头", "https://" not in blob)
        check("关于窗里有协议和版本",
              "MIT" in blob and core.VERSION in blob)
        check("简介用的是 core.SUMMARY（不在界面里另写一版）",
              core.SUMMARY in blob, core.SUMMARY)
        old_clip = None
        try:
            old_clip = root.clipboard_get()
        except Exception:
            pass
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
        # 每行文字都必须是"看得完"的：wraplength 算小了会出现半行被裁
        tight = [(str(l.cget("text"))[:10], _lines(l))
                 for l in _labels(dialog) if _lines(l) > 4]
        check("关于窗里的长文案没有炸到 4 行以上", not tight, str(tight))
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
        root.destroy()

    print("\n通过 %d 项" % ok)
    if bad:
        print("失败 %d 项：%s" % (len(bad), ", ".join(bad)))
        return 1
    print("界面冒烟全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
