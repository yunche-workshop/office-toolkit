# -*- coding: utf-8 -*-
"""
e2e_shot.py —— 图片转 PDF：端到端真机跑一遍 + 出文章配图

走完整流程：造样本 → 载入 → 调页序 → 配参数 → 转换 → 弹关于窗，每步一张图。
用 App 对象直接驱动，不模拟鼠标（Tk 控件没有子窗口句柄，只能这么搞）。

用法（**必须用有 tkinter 的那个解释器**，managed 3.13 没 tkinter）：
    C:/Users/Mu/.workbuddy/binaries/python/envs/tk38/Scripts/python.exe \
        tools/img2pdf/tests/e2e_shot.py

产物： 04_素材/配图-img2pdf/01..06.png

三条从另外两个工具继承来的规矩：

1. 样本目录用一个"像真实用户"的短路径。界面列表显示的是完整路径，
   配图里出现 D:\\AI\\wkbuddy\\... 这种开发目录就穿帮了。测完连目录一起收掉
2. 每张图存完**数颜色**，少于 8 种判失败。别拿文件大小当"是不是空白"的判据
3. 截图**先 PrintWindow，再按像素内容决定要不要退回 BitBlt**。
   只靠"置顶 + 抢前台 + BitBlt"是不可靠的：后台进程抢不到前台。
   这个脚本第一次跑的时候六张图全截成了 IDE 的界面 —— 尺寸还是对的、
   颜色也有几千种，于是每一步都报了"成功"，典型的假通过
"""

import ctypes
import os
import struct
import sys
import time
import zlib
from ctypes import wintypes as wt

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.dirname(HERE)                                     # .../tools/img2pdf
FUYE = os.path.abspath(os.path.join(HERE, *([os.pardir] * 4)))   # tests 往上 4 层

SRC = os.path.join(TOOL, "src")
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

import tkinter as tk  # noqa: E402

import core  # noqa: E402
import ui  # noqa: E402
import win32ext  # noqa: E402
from test_core import png_bytes  # noqa: E402（这个解释器没有 Pillow）

# 样本放这儿：列表里显示的就是这条路径，所以要像真人会有的目录
TESTDIR = r"D:\办公文件\2026年3月发票"
OUTDIR = os.path.join(FUYE, "04_素材", "配图-img2pdf")
BORDER = 10           # GetWindowRect 含一圈看不见的 resize 边框，会透进背后的窗口

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
SRCCOPY = 0x00CC0020


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


def _topmost(hwnd, on):
    user32.SetWindowPos(wt.HWND(hwnd), HWND_TOPMOST if on else HWND_NOTOPMOST,
                        0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE)


def _chunk(tag, payload):
    return (struct.pack(">I", len(payload)) + tag + payload +
            struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def _grab(hwnd, via_print):
    """抓一块位图，返回 (宽, 高, RGB 字节)。via_print=True 走 PrintWindow。"""
    r = wt.RECT()
    user32.GetWindowRect(wt.HWND(hwnd), ctypes.byref(r))
    w, h = r.right - r.left - BORDER * 2, r.bottom - r.top - BORDER * 2
    if w <= 0 or h <= 0:
        return 0, 0, b""

    full_w, full_h = r.right - r.left, r.bottom - r.top
    hdc = user32.GetDC(wt.HWND(hwnd)) if via_print else user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, full_w, full_h)
    old = gdi32.SelectObject(mem, bmp)
    if via_print:
        # 位图是整窗（含那圈看不见的边框），所以内容从 (BORDER, BORDER) 开始
        ok = user32.PrintWindow(wt.HWND(hwnd), mem, 2)     # 2 = RENDERFULLCONTENT
        x_off = y_off = BORDER
    else:
        # BitBlt 直接从"往里缩一格"的屏幕坐标拷，内容就落在 (0, 0)
        gdi32.BitBlt(mem, 0, 0, full_w, full_h, hdc,
                     r.left + BORDER, r.top + BORDER, SRCCOPY)
        ok, x_off, y_off = 1, 0, 0

    bi = BMI()
    bi.biSize = ctypes.sizeof(BMI)
    bi.biWidth, bi.biHeight = full_w, -full_h
    bi.biPlanes, bi.biBitCount = 1, 32
    buf = ctypes.create_string_buffer(full_w * full_h * 4)
    gdi32.GetDIBits(mem, bmp, 0, full_h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(wt.HWND(hwnd) if via_print else None, hdc)
    if not ok:
        return 0, 0, b""

    data, stride = buf.raw, full_w * 4
    base = y_off * stride + x_off * 4
    raw = bytearray()
    for y in range(h):
        # 每行只取 w 个像素：多取一个字节，下面那句扩展切片就会因为长度对不上炸
        row_start = base + y * stride
        seg = data[row_start:row_start + w * 4]
        row = bytearray(w * 3)
        row[0::3] = seg[2::4]
        row[1::3] = seg[1::4]
        row[2::3] = seg[0::4]
        raw += b"\x00" + bytes(row)
    return w, h, bytes(raw)


def _color_kinds(raw, row_bytes):
    """
    数图里有几种颜色 —— 只为"是不是空白"服务，按行首取样、够 400 种就收手。
    """
    colors = set()
    step = max(1, row_bytes // 7) * 3
    for off in range(0, len(raw), step):
        colors.add(raw[off:off + 3])
        if len(colors) > 400:
            break
    return len(colors)


def _png(path, w, h, raw):
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(_chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        fh.write(_chunk(b"IDAT", zlib.compress(raw, 6)))
        fh.write(_chunk(b"IEND", b""))


def capture(hwnd, path):
    """先 PrintWindow（不受遮挡影响），画不出东西才退回 BitBlt 抓屏幕。"""
    for via_print in (True, False):
        w, h, raw = _grab(hwnd, via_print)
        if not raw:
            continue
        n = _color_kinds(raw, w * 3)
        if n >= 8:
            _png(path, w, h, raw)
            print("  -> %s（%dx%d，取样 %d 种颜色，%s）" % (
                os.path.basename(path), w, h, n,
                "PrintWindow" if via_print else "BitBlt 兜底"))
            return True
        print("     %s 这条路没画出东西（只 %d 种颜色），换下一种" % (
            "PrintWindow" if via_print else "BitBlt", n))
    print("  截图失败：%s" % os.path.basename(path))
    return False


def make_samples():
    """
    造一批"像刚拍完的发票"：真照片 JPEG（系统壁纸）+ 自己编的 PNG。

    JPEG 用真照片，界面里那行尺寸/密度才是真人会看到的数字；
    PNG 用 test_core 里那个手写编码器造，因为这个解释器没有 Pillow。
    """
    if os.path.isdir(TESTDIR):
        for f in os.listdir(TESTDIR):
            try:
                os.remove(os.path.join(TESTDIR, f))
            except Exception:
                pass
    else:
        os.makedirs(TESTDIR)

    wall = next((c for c in (r"C:\Windows\Web\Wallpaper\Windows\img0.jpg",
                             r"C:\Windows\Web\Screen\img100.jpg")
                 if os.path.exists(c)), None)
    names = ["发票_滴滴_0312.jpg", "发票_出租车_0314.jpg", "附件_行程单.png",
             "发票_酒店_0315.jpg", "附件_水单.png", "封面.png"]
    paths = []
    for name in names:
        p = os.path.join(TESTDIR, name)
        if name.endswith(".jpg"):
            if wall:
                with open(wall, "rb") as fh:
                    blob = fh.read()
            else:                                  # 没有壁纸就自己编一张 JPEG
                from test_core import jpeg_bytes
                blob = jpeg_bytes(w=1240, h=1754)
            with open(p, "wb") as fh:
                fh.write(blob)
        else:
            # 纯 Python 造像素，行数一多就慢：620x877、一行复用
            w, h = 620, 877
            row = bytes([(j * 5) % 256 for j in range(w * 3)])
            with open(p, "wb") as fh:
                fh.write(png_bytes(w, h, 8, 2, [row] * h))
        paths.append(p)
    return paths


def main():
    win32ext.enable_dpi_awareness()          # 必须在创建任何窗口之前

    paths = make_samples()
    os.makedirs(OUTDIR, exist_ok=True)
    print("%s v%s 端到端截图" % (core.TOOL_CN, core.VERSION))
    print("样本目录 = %s" % TESTDIR)
    print("截图输出 = %s" % OUTDIR)

    root = tk.Tk()
    root.configure(bg="#16161a")
    left, top, right, bottom = win32ext.monitor_work_area()
    w, h = win32ext.px(860), win32ext.px(640)
    root.geometry("%dx%d+%d+%d" % (w, h, left + (right - left - w) // 2, top + 30))

    app = ui.run(root)
    shots = []
    # 转换有没有真出 PDF，和"截图抓了几张成功"是两码事，混在一个列表里
    # 报出来的就是"截图 7 张"这种对不上的数
    produced = []

    def idle():
        for _ in range(6):
            root.update_idletasks()
            root.update()
            time.sleep(0.02)

    def shot(name, win=None):
        win = win or root
        hwnd = win32ext.get_hwnd(win)
        _topmost(hwnd, True)                   # 只为"看得见"，抓图像本身不依赖它
        time.sleep(0.4)
        idle()
        shots.append(capture(hwnd, os.path.join(OUTDIR, name)))
        _topmost(hwnd, False)
        idle()

    def stage1():
        print("[1] 初始界面（空列表）")
        shot("01_初始界面.png")

    def stage2():
        print("[2] 载入 6 张发票")
        app._add(paths)
        idle()
        shot("02_载入图片.png")

    def stage3():
        print("[3] 调页序（把最后一张挪到最前当封面）")
        app.tree.selection_set(str(len(app.paths) - 1))
        for _ in range(len(app.paths) - 1):
            app.move(-1)
        idle()
        shot("03_调过页序.png")

    def stage4():
        print("[4] 配参数：A4 竖版 + 页边距 + 文档标题")
        app.vars["page"].set(ui.PAPER_CN["A4"])
        app.vars["orient"].set(ui.ORIENT_CN["portrait"])
        app.vars["margin"].set(20)
        app.vars["title"].set("2026年3月出差报销")
        app._refresh_summary()
        idle()
        shot("04_参数与预览.png")

    def stage5():
        print("[5] 转换（等后台线程真的写完）")
        app.vars["out"].set(os.path.join(TESTDIR, "2026年3月报销.pdf"))
        app.do_convert()
        deadline = time.time() + 120
        while time.time() < deadline:
            root.update()
            if app.worker is None and app.last_out:
                break
            time.sleep(0.05)
        idle()
        print("     摘要行 = %r" % app.summary.cget("text"))
        produced.append(bool(app.last_out))
        shot("05_转换完成.png")

    def stage6():
        print("[6] 关于窗（点底部署名弹出来的那个）")
        dlg = app.show_about()
        idle()
        shot("06_关于窗.png", win=dlg)
        dlg.destroy()
        idle()

    def finish():
        pdf = app.last_out
        print("\n[校验] 产物存在：%s（%s 字节）" % (
            bool(pdf and os.path.exists(pdf)),
            os.path.getsize(pdf) if pdf and os.path.exists(pdf) else "-"))
        total = sum(os.path.getsize(os.path.join(TESTDIR, f))
                    for f in os.listdir(TESTDIR)
                    if f.lower().endswith((".jpg", ".png")))
        print("[校验] 6 张原图合计 %d 字节 → PDF 多 %d 字节（3 张 JPEG 码流原样，"
              "3 张 PNG 重压）" % (total, os.path.getsize(pdf) - total if pdf else 0))
        print("[校验] 截图 %d 张，成功 %d 张" % (len(shots), sum(1 for s in shots if s)))

        for f in os.listdir(TESTDIR):
            try:
                os.remove(os.path.join(TESTDIR, f))
            except Exception:
                pass
        for d in (TESTDIR, os.path.dirname(TESTDIR)):
            try:
                os.rmdir(d)          # 只在空的时候删得掉，不会误删真人在用的目录
            except Exception:
                pass
        root.destroy()

    for fn in (stage1, stage2, stage3, stage4, stage5, stage6, finish):
        fn()

    print("\n配图都在 %s" % OUTDIR)
    return 0 if all(shots) and all(produced) else 1


if __name__ == "__main__":
    sys.exit(main())
