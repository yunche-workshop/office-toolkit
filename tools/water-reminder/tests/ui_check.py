# -*- coding: utf-8 -*-
"""
ui_check.py —— 界面自检

启动托盘 + 设置窗口 + 提醒窗口，停留若干秒后自动退出，
用于确认 Tk/亚克力/托盘在真机上不报错。
用法：python tests/ui_check.py [停留秒数]
"""

import os
import sys
import tkinter as tk
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import core  # noqa: E402
import main as appmod  # noqa: E402

STAY = int(sys.argv[1]) if len(sys.argv) > 1 else 8

errors = []


def hook(exc_type, exc, tb):
    errors.append("".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = hook


def main():
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = hook

    app = appmod.App(root)
    print("数据目录：%s" % core.DATA_DIR)
    app.start(minimized=True)

    root.after(600, app.open_settings)
    root.after(2600, app.show_reminder_now)
    root.after(STAY * 1000, app.quit)
    root.after(STAY * 1000 + 1200, root.quit)
    root.mainloop()

    win = app.settings_win
    print("设置窗口：%s" % ("已创建" if win else "未创建"))
    print("玻璃效果：%s" % ("启用" if win and win.glass else "回退实色"))
    print("托盘图标：%s" % ("已创建" if app.tray and app.tray.hwnd else "失败"))
    print("今日总量：%s ml" % app.store.total())
    if errors:
        print("发现 %d 个异常：" % len(errors))
        for e in errors[:5]:
            print(e)
        return 1
    print("界面自检通过，无异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
