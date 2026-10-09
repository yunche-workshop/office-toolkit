# -*- coding: utf-8 -*-
"""
e2e_shot.py —— 批量重命名：端到端真机测试 + 文章配图截图

走完整流程：建测试样本 → 载入 → 配规则 → 预览 → 执行 → 撤销，每一步截图。
用 App 对象直接驱动，不模拟鼠标（Tk 控件没有子窗口句柄，只能这么搞）。

用法（**必须用有 tkinter 的系统 Python 3.8**，managed 3.13 没 tkinter）：
    C:\\Python38\\python.exe tools/batch-renamer/tests/e2e_shot.py

产物：  04_素材/配图-batch-renamer/01..05.png
"""

import ctypes
import os
import struct
import sys
import time
import zlib
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)          # .../02_工具/tools/batch-renamer
ROOT = os.path.dirname(TOOL)          # .../02_工具/tools
FUYE = os.path.abspath(os.path.join(HERE, *([os.pardir] * 4)))  # tests→4层到 fuye

SRC = os.path.join(TOOL, "src")
sys.path.insert(0, SRC)

import tkinter as tk  # noqa: E402
from tkinter import messagebox  # noqa: E402

import win32ext  # noqa: E402
import ui  # noqa: E402

print("HERE  = %s" % HERE)
print("TOOL  = %s" % TOOL)
print("ROOT  = %s" % ROOT)
print("FUYE  = %s" % FUYE)

# 测试样本放一个"像真实用户"的短路径——文件列表显示的是完整路径，
# 配图里出现 D:\AI\wkbuddy\... 这种开发目录就穿帮了。测完自动删
TESTDIR = r"D:\办公文件\2026年1月扫描"
OUTDIR = os.path.join(FUYE, "04_素材", "配图-batch-renamer")

# 模拟扫描仪/相机导出的流水号文件名——文章里要讲的就是这个痛点
NAMES = [
    "IMG_20260114_093012.jpg", "IMG_20260114_093047.jpg",
    "IMG_20260114_093115.jpg", "IMG_20260114_093203.jpg",
    "IMG_20260114_093241.jpg", "IMG_20260114_093318.jpg",
    "IMG_20260114_093402.jpg", "IMG_20260114_093455.jpg",
    "IMG_20260114_093528.jpg", "IMG_20260114_093611.jpg",
    "IMG_20260114_093640.jpg", "IMG_20260114_093722.jpg",
]

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002


def topmost(hwnd, on):
    user32.SetWindowPos(hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
                        0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)


class BMI(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
    ]


def capture(hwnd, path):
    """BitBlt 抓窗口在屏幕上的样子，纯标准库写 PNG。"""
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    BORDER = 10  # GetWindowRect 含一圈不可见的 resize 边框，会把背后的窗口透进来
    w = r.right - r.left - BORDER * 2
    h = r.bottom - r.top - BORDER * 2
    if w <= 0 or h <= 0:
        print("  截图失败：窗口尺寸 %dx%d" % (w, h))
        return False

    sdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(sdc)
    bmp = gdi32.CreateCompatibleBitmap(sdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, sdc, r.left + BORDER, r.top + BORDER, 0x00CC0020)

    bi = BMI()
    bi.biSize = ctypes.sizeof(BMI)
    bi.biWidth, bi.biHeight = w, -h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)

    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, sdc)

    data, stride = buf.raw, w * 4
    raw = bytearray()
    for y in range(h):
        seg = data[y * stride:(y + 1) * stride]
        row = bytearray(w * 3)
        row[0::3] = seg[2::4]
        row[1::3] = seg[1::4]
        row[2::3] = seg[0::4]
        raw += b"\x00" + bytes(row)

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    print("  -> %s（%dx%d）" % (os.path.basename(path), w, h))
    return True


def make_samples():
    """建一批假扫描件当测试样本。"""
    if os.path.isdir(TESTDIR):
        for f in os.listdir(TESTDIR):
            try:
                os.remove(os.path.join(TESTDIR, f))
            except Exception:
                pass
    else:
        os.makedirs(TESTDIR)
    for i, n in enumerate(NAMES):
        p = os.path.join(TESTDIR, n)
        # 写个 JPEG 文件头，让它看起来像真图而不是空文件
        with open(p, "wb") as f:
            f.write(b"\xff\xd8\xff\xe0" + b"\x00" * (2048 + i * 137))
    return [os.path.join(TESTDIR, n) for n in NAMES]


def main():
    win32ext.enable_dpi_awareness()  # 必须在创建任何窗口之前

    paths = make_samples()
    os.makedirs(OUTDIR, exist_ok=True)
    print("仓库根 FUYE = %s" % FUYE)
    print("截图输出 OUTDIR = %s" % OUTDIR)
    print("测试样本 TESTDIR = %s" % TESTDIR)

    # do_rename 里有 askyesno / showinfo，不打桩会卡住等用户点
    messagebox.askyesno = lambda *a, **k: True
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None

    root = tk.Tk()
    root.configure(bg="#16161a")
    left, top, right, bottom = win32ext.monitor_work_area()
    win32ext.clamp_into_view(
        left + (right - left - win32ext.px(980)) // 2,
        top + (bottom - top - win32ext.px(820)) // 2,
        win32ext.px(980), win32ext.px(820))
    root.geometry("%dx%d+%d+%d" % (win32ext.px(980), win32ext.px(820),
                                   left + (right - left - win32ext.px(980)) // 2,
                                   top + 40))

    app = ui.run(root)
    hwnd = [None]
    shots = []

    def idle():
        root.update_idletasks()
        root.update()

    def shot(name):
        def go():
            try:
                h = win32ext.get_hwnd(root)
                hwnd[0] = h
                topmost(h, True)
                user32.SetForegroundWindow(h)
                time.sleep(0.45)
                idle()
                path = os.path.join(OUTDIR, name)
                shots.append(capture(h, path))
            finally:
                try:
                    topmost(hwnd[0], False)
                except Exception:
                    pass
        idle()
        go()

    def stage1():
        print("[1] 初始界面（空）")
        shot("01_初始界面.png")

    def stage2():
        print("[2] 载入 12 个扫描件")
        app._add(paths)
        idle()
        shot("02_载入文件.png")

    def stage3():
        print("[3] 配规则 + 预览")
        v = app.vars["replace"]
        v["enabled"].set(True)
        v["find"].set("IMG_20260114_")
        v["to"].set("入库凭证_")
        v["case_sensitive"].set(False)

        v2 = app.vars["template"]
        v2["enabled"].set(True)
        v2["text"].set("{name}_20260114")

        app.refresh_preview()
        idle()
        rows = app.rows
        for r in rows[:3]:
            print("     %s  ->  %s  [%s]" % (r["old_name"], r["new_name"],
                                             r["status"]))
        shot("03_规则与预览.png")

    def stage4():
        print("[4] 执行改名")
        app.do_rename()
        idle()
        shot("04_改名完成.png")

    def stage5():
        print("[5] 撤销还原")
        app.do_undo()
        idle()
        shot("05_撤销还原.png")

    def finish():
        # 校验：撤销后文件名应该全部回到原始状态
        after = sorted(os.listdir(TESTDIR))
        ok = [n for n in after if not n.startswith("IMG_")] == []
        print("\n[校验] 撤销后是否全部还原：%s" % ("通过" if ok else "失败"))
        for n in after[:3]:
            print("     %s" % n)
        print("[校验] 截图 %d 张，成功 %d 张" % (len(shots), sum(1 for s in shots if s)))

        # 清理测试样本（连父目录一起，别在人家 D 盘留个空文件夹）
        for f in os.listdir(TESTDIR):
            try:
                os.remove(os.path.join(TESTDIR, f))
            except Exception:
                pass
        try:
            os.rmdir(TESTDIR)
            os.rmdir(os.path.dirname(TESTDIR))
        except Exception:
            pass
        root.after(200, root.destroy)

    # 别用固定 after 时间：1986x1591 的 PNG 压缩要好几秒，
    # 定时器按真实时间走会在上一个 stage 还没截完时就跑下一个，
    # 最后把窗口 destroy 掉，末尾的截图就废了。改成串行走完一个再下一个。
    def run_stages(seq, i=0):
        if i >= len(seq):
            return
        seq[i]()
        if i < len(seq) - 1:
            root.after(30, lambda: run_stages(seq, i + 1))

    root.after(700, lambda: run_stages(
        [stage1, stage2, stage3, stage4, stage5, finish]))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
