# -*- coding: utf-8 -*-
"""
shot.py —— 按窗口标题抓屏，存 PNG（纯标准库，不用 Pillow）

用法：
    python tests/shot.py "批量重命名" [输出路径]

exe 先启动，再跑这个脚本。抓的是 GDI 表面，看不到 DWM 合成的透明效果。
"""

import ctypes
import os
import struct
import sys
import time
import zlib
from ctypes import wintypes as wt

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32), ("biClrImportant", ctypes.c_uint32),
    ]


def _png(w, h, rgb_rows):
    raw = bytearray()
    for row in rgb_rows:
        raw.append(0)
        raw += row

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
            chunk(b"IEND", b""))


def grab(title, out):
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        raise SystemExit("没找到标题为「%s」的窗口" % title)

    user32.ShowWindow(hwnd, 9)          # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.6)

    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w = r.right - r.left
    h = r.bottom - r.top
    if w <= 0 or h <= 0:
        raise SystemExit("窗口尺寸不对：%dx%d" % (w, h))

    hdc = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, hdc, r.left, r.top, SRCCOPY)

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h                    # 负号 = 自上而下
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)

    data = buf.raw          # 索引这个才是 int，直接索引 buf 拿到的是长度 1 的 bytes
    rows = []
    stride = w * 4
    for y in range(h):
        line = bytearray()
        base = y * stride
        for x in range(w):
            b = data[base + x * 4]
            g = data[base + x * 4 + 1]
            rr = data[base + x * 4 + 2]
            line += bytes((rr, g, b))
        rows.append(bytes(line))

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, hdc)

    with open(out, "wb") as f:
        f.write(_png(w, h, rows))
    print("存好了：%s（%dx%d）" % (out, w, h))


if __name__ == "__main__":
    title = sys.argv[1] if len(sys.argv) > 1 else "批量重命名"
    here = os.path.dirname(os.path.abspath(__file__))
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "shot.png")
    grab(title, out)
