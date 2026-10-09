# -*- coding: utf-8 -*-
"""
smoke_ui.py —— 界面冒烟（不弹窗，造完窗口就销毁）

验证：界面能建起来、规则能从控件读回、预览能算出结果。
用装了 tkinter 的那个 venv 跑：
    .../envs/tk38/Scripts/python.exe tests/smoke_ui.py
"""

import os
import sys
import tempfile
import shutil
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import win32ext  # noqa: E402
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
