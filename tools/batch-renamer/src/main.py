# -*- coding: utf-8 -*-
"""
main.py —— 入口

顺序很重要：enable_dpi_awareness() 必须在创建任何窗口之前调用，
否则 200% 缩放的屏上整个窗口会被 Windows 位图拉伸，字是糊的。
"""

import os
import sys
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import win32ext  # noqa: E402
import ui  # noqa: E402

WIN_W = 980
WIN_H = 760


def center(root, w, h):
    left, top, right, bottom = win32ext.monitor_work_area()
    x = left + (right - left - w) // 2
    y = top + (bottom - top - h) // 2
    x, y = win32ext.clamp_into_view(x, y, w, h)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))


def main():
    win32ext.enable_dpi_awareness()

    root = tk.Tk()
    root.configure(bg="#16161a")
    center(root, win32ext.px(WIN_W), win32ext.px(WIN_H))
    root.minsize(win32ext.px(860), win32ext.px(620))

    ui.run(root)
    root.mainloop()


if __name__ == "__main__":
    main()
