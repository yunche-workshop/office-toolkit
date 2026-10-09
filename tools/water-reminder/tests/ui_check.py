# -*- coding: utf-8 -*-
"""
ui_check.py —— 界面自检（真机、会闪一下窗口）

跑的是"逻辑单测覆盖不到的那半截"：Tk 真的把窗口画出来了没有、
无边框窗能不能带起前台、换外观后配色真的变了没有、提示条会不会自己收。

    python tests/ui_check.py [停留毫秒数，默认 260]

两条注意：
  * 数据目录、配置、日志都会被重定向到临时目录，不会碰你真实的喝水记录；
  * 自启动写的是注册表 HKCU\\...\\Run，这里把它桩掉了，不会改你机器上的开机自启。
"""

import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import tkinter as tk  # noqa: E402

import core  # noqa: E402
import win32ext  # noqa: E402

LIVE_MS = int(sys.argv[1]) if len(sys.argv) > 1 else 260

# 必须在创建任何窗口之前，否则高分屏整体发糊
win32ext.enable_dpi_awareness()

TMP = tempfile.mkdtemp(prefix="wr_ui_")
core.ROOT = TMP
core.DATA_DIR = os.path.join(TMP, "data")
core.CONFIG_PATH = os.path.join(TMP, "config.json")
core.LOG_PATH = os.path.join(TMP, "run.log")

import main as appmod  # noqa: E402

errors = []
results = []


def hook(exc_type, exc, tb):
    # 自己人（异常）也要看得见：光塞进列表里，脚本会"安静地少跑几项"
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    errors.append(text)
    print(text.strip().splitlines()[-1])


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def pump(root, ms):
    """真的过这么长时间并跑掉事件队列：after/定时器都得真的轮到。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:          # 退出流程已经把 Tk 拆掉了
            break
        time.sleep(0.01)
    try:
        root.update()
    except tk.TclError:
        pass


def bbox(win):
    return (win.winfo_x(), win.winfo_y(),
            win.winfo_x() + win.width, win.winfo_y() + win.height)


def inside(rect, area):
    return (rect[0] >= area[0] - 2 and rect[1] >= area[1] - 2
            and rect[2] <= area[2] + 2 and rect[3] <= area[3] + 2)


def run_checks(app, root):
    app.start(minimized=True)
    check("托盘图标已创建", bool(app.tray and app.tray.hwnd))

    area = win32ext.work_area()

    # ---------- 设置窗口：建得出、放得下、在屏幕里 ----------
    app.open_settings()
    pump(root, LIVE_MS)
    win = app.settings_win
    check("设置窗口已创建", win is not None and win.winfo_exists())
    if not (win and win.winfo_exists()):
        return
    check("设置窗口在工作区内", inside(bbox(win), area),
          "%s vs %s" % (bbox(win), area))
    check("设置窗口是居中的",
          abs((bbox(win)[0] + bbox(win)[2]) / 2.0
              - (area[0] + area[2]) / 2.0) < max(40, win.width * 0.1),
          str(bbox(win)))
    check("自适应缩放因子有效", win.MIN_FIT <= win.fit <= 1.0,
          "fit=%.2f S=%.2f" % (win.fit, win.S))

    # ---------- 文字不许压在柱状图上 ----------
    # 打包产物截图才发现"连续达标 N 天"和最近 7 天那排柱子叠在同一个位置，
    # 最右边那根把字吃掉半个。肉眼看得见、断言里没有 → 补一条几何相交检查。
    win.canvas.itemconfigure(win.txt_streak, text="连续达标 120 天")
    win.canvas.update_idletasks()
    sb = win.canvas.bbox(win.txt_streak)
    cb = tuple(win.u(v) for v in win._chart_box)
    separated = (sb is None or sb[2] <= cb[0] or sb[0] >= cb[2]
                 or sb[3] <= cb[1] or sb[1] >= cb[3])
    check("连续达标那行不压到柱状图", separated, "streak=%s chart=%s" % (sb, cb))

    dark_bg = win.C["bg"]

    # ---------- 换浅色：配色真的变了，而且重建后仍然居中 ----------
    app.cfg.data["theme"] = "light"
    app.reopen_settings()
    pump(root, LIVE_MS)
    win = app.settings_win
    check("切浅色后配色真的换了", win.C["bg"] != dark_bg,
          "%s -> %s" % (dark_bg, win.C["bg"]))
    check("重建后仍在屏幕内且不在左上角", inside(bbox(win), area)
          and (win.winfo_x(), win.winfo_y()) != (0, 0), str(bbox(win)))

    # ---------- 单位：进度数字跟着换算 ----------
    app.store.add(250)
    app.cfg.data["unit"] = "oz"
    app.on_config_saved()
    win.refresh()
    check("切盎司后总量按盎司显示",
          "oz" in win.canvas.itemcget(win.txt_goal, "text")
          and "." in win.canvas.itemcget(win.txt_total, "text"),
          "%s / %s" % (win.canvas.itemcget(win.txt_total, "text"),
                       win.canvas.itemcget(win.txt_goal, "text")))
    check("非毫升时提示用户输入框仍按毫升",
          "按毫升填" in win.canvas.itemcget(win.txt_unit_note, "text"))
    app.cfg.data["unit"] = "ml"
    app.cfg.data["theme"] = "dark"
    app.reopen_settings()
    pump(root, LIVE_MS)
    win = app.settings_win
    check("换回深色配色又回来了", win.C["bg"] == dark_bg, win.C["bg"])

    # ---------- 提醒弹窗：文案长了窗口要跟着长 ----------
    app.popup("短", 500, 2000, drink_amount=250)
    pump(root, 120)
    short = app.reminder_win.height
    app.popup("这条文案故意写得很长很长，用来验证标题换成两行以后窗口会不会把"
              "第二行切掉，所以它必须比短文案那一次更高一些才行", 500, 2000,
              drink_amount=250)
    pump(root, 120)
    long_ = app.reminder_win.height
    check("长文案窗口变高，不会切字", long_ > short, "%d -> %d" % (short, long_))
    check("提醒弹窗在屏幕内", inside(bbox(app.reminder_win), area),
          str(bbox(app.reminder_win)))
    rw = app.reminder_win
    check("提醒弹窗追提醒参数正确", rw.missable is True, rw.tail)
    tb = rw.canvas.bbox(rw.txt_count)
    check("倒计时文字换行后也不压到右上角的叉",
          tb is not None and tb[2] <= rw.u(rw.WIDTH - rw.PAD - 34),
          "%s vs %s" % (tb, rw.u(rw.WIDTH - rw.PAD - 34)))

    # ---------- 手动测试提醒不该追提醒 ----------
    app.show_reminder_now()
    pump(root, 120)
    check("手动测试提醒不追提醒", app.reminder_win.missable is False,
          app.reminder_win.tail)

    # ---------- 提示条：出现 + 自己收掉 ----------
    app.show_hint("这是一条自检提示", seconds=1)
    pump(root, 200)
    check("提示条已显示", app.hint_win is not None and app.hint_win.winfo_exists())
    pump(root, 1400)
    check("提示条会自己收掉",
          not (app.hint_win and app.hint_win.winfo_exists()))

    # ---------- 拖动钳制：拖动时至少露 40 像素，从托盘唤起时整扇拉回 ----------
    vd = win32ext.virtual_desktop()
    win._move_to(vd[2] + 400, vd[3] + 400)        # 故意往屏幕右下方甩
    pump(root, 120)
    rect = bbox(win)
    check("拖出屏幕时至少露出 40 像素（还能抓着拖回来）",
          rect[0] <= vd[2] - 40 and rect[1] <= vd[3] - 40
          and rect[2] >= vd[0] + 40 and rect[3] >= vd[1] + 40,
          "%s（虚拟桌面 %s）" % (rect, vd))

    # 绕过钳制直接写 geometry，模拟"位置存坏了 / 拔掉副屏后重开"
    win.geometry("+%d+%d" % (vd[2] + 900, vd[3] + 900))
    pump(root, 120)
    win.keep_in_view()
    pump(root, 120)
    check("从托盘唤起时整扇窗口都回到屏幕里", inside(bbox(win), area),
          "%s vs %s" % (bbox(win), area))
    win.center_on_screen()
    pump(root, 120)

    app.settings_win.to_tray()
    pump(root, 300)
    app.quit()
    pump(root, 400)
    check("退出流程已收口", app._running is False and app.settings_win is None
          and app.reminder_win is None)
    check("重复退出不会炸", (app.quit() or True))


def main():
    sys.excepthook = hook
    root = tk.Tk()
    root.withdraw()
    root.report_callback_exception = hook
    app = appmod.App(root)
    # 桩掉自启动：on_config_saved 会按注册表实际状态回写 HKCU\...\Run，
    # 自检脚本跑的是 python 解释器，真写进去就把用户的 exe 自启覆盖掉了。
    app.set_autostart = lambda want: bool(want)
    try:
        run_checks(app, root)
    except Exception:                       # 半路炸了也要把已跑过的项报出来
        hook(*sys.exc_info())
    return finish(app, root)


def finish(app, root):
    fails = [n for n, okv in results if not okv]
    print("")
    print("共 %d 项检查，通过 %d 项" % (len(results), len(results) - len(fails)))
    for f in fails:
        print("   FAIL：%s" % f)
    if errors:
        print("捕获到 %d 个异常：" % len(errors))
        for e in errors[:5]:
            print(e)
    log = core.LOG_PATH
    if os.path.exists(log):
        with open(log, "r", encoding="utf-8") as fh:
            print("日志末行：%s" % (fh.read().strip().splitlines() or ["(空)"])[-1])
    shutil.rmtree(TMP, ignore_errors=True)
    ok = not fails and not errors
    print("结果：%s" % ("界面自检通过，无异常" if ok else "存在问题"))
    try:
        root.destroy()
    except Exception:
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
