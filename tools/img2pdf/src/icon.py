# -*- coding: utf-8 -*-
"""
icon.py —— 纯标准库生成图标（打包用 .ico + 窗口用 PNG）

不依赖任何美术素材：手写 PNG 编码，再按需包进 ICO 容器。
图形是「一页纸 + 纸里一张小山照片」，一眼能看出是"图片进 PDF 出"。

两处用途：
  build.py  调 ensure_icon() 生成 assets/app.ico 当 exe 图标
  ui.py     调 window_photo() 拿 base64 PNG，给 Tk 窗口/任务栏换图标
            （Tk 默认的羽毛图标在 Win11 任务栏上特别扎眼）
"""

import base64
import os
import struct
import zlib

BG = (43, 92, 255, 255)          # 主色蓝，和界面 ACCENT 同一系
PAGE = (255, 255, 255, 255)
FOLD = (196, 212, 255, 255)      # 折角：比纸暗一点的蓝白
INK = (43, 92, 255, 255)         # 纸里的小山/太阳，直接用主色
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _round_rect(s, radius=0.22):
    """size x size 的 RGBA 矩阵：圆角实心方块。"""
    r = s * radius
    rows = []
    for y in range(s):
        row = []
        for x in range(s):
            cx = min(max(x + 0.5, r), s - r)
            cy = min(max(y + 0.5, r), s - r)
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            row.append(BG if (dx * dx + dy * dy) <= r * r else (0, 0, 0, 0))
        rows.append(row)
    return rows


def _fill(rows, x0, y0, x1, y1, color):
    s = len(rows)
    for y in range(int(round(y0 * s)), int(round(y1 * s))):
        if 0 <= y < s:
            for x in range(int(round(x0 * s)), int(round(x1 * s))):
                if 0 <= x < s:
                    rows[y][x] = color


def _dot(rows, cx, cy, r, color):
    s = len(rows)
    R = r * s
    for y in range(int(cy * s - R), int(cy * s + R) + 1):
        if not (0 <= y < s):
            continue
        for x in range(int(cx * s - R), int(cx * s + R) + 1):
            if not (0 <= x < s):
                continue
            dx, dy = x + 0.5 - cx * s, y + 0.5 - cy * s
            if dx * dx + dy * dy <= R * R:
                rows[y][x] = color


def _triangle(rows, ax, ay, bx, by, cx, cy, color):
    """三个归一化顶点构成的实心三角（扫描线法，够用且不依赖任何库）。"""
    s = len(rows)
    pts = [(ax * s, ay * s), (bx * s, by * s), (cx * s, cy * s)]
    y0 = int(min(p[1] for p in pts))
    y1 = int(max(p[1] for p in pts)) + 1
    for y in range(max(0, y0), min(s, y1)):
        xs = []
        for i in range(3):
            x1_, y1_ = pts[i]
            x2_, y2_ = pts[(i + 1) % 3]
            if (y1_ <= y < y2_) or (y2_ <= y < y1_):
                t = (y - y1_) / (y2_ - y1_)
                xs.append(x1_ + (x2_ - x1_) * t)
        if len(xs) < 2:
            continue
        for x in range(max(0, int(min(xs))), min(s, int(max(xs)) + 1)):
            rows[y][x] = color


def _render(s):
    """
    画一帧。16px 那档只留"纸 + 一个太阳点"：小山和折角在这个尺寸上
    会糊成一团脏像素（喝水提醒的 16px 图标就是这么返工的）。
    """
    rows = _round_rect(s, radius=0.20 if s >= 32 else 0.16)
    _fill(rows, 0.22, 0.14, 0.72, 0.88, PAGE)          # 纸
    if s >= 24:
        # 右上角折角：把纸那块的角挖掉再补一块浅色三角
        _fill(rows, 0.56, 0.14, 0.72, 0.22, BG)
        _triangle(rows, 0.56, 0.14, 0.72, 0.14, 0.56, 0.30, FOLD)
        _dot(rows, 0.36, 0.36, 0.055, INK)             # 太阳
        _triangle(rows, 0.28, 0.78, 0.50, 0.46, 0.66, 0.78, INK)   # 小山
        _triangle(rows, 0.44, 0.78, 0.58, 0.58, 0.70, 0.78, INK)
    else:
        _dot(rows, 0.47, 0.50, 0.16, INK)              # 16px：一个点就够认了
    return rows


def png_bytes(rows):
    """RGBA8 矩阵 -> PNG 字节。"""
    s = len(rows)
    raw = bytearray()
    for row in rows:
        raw.append(0)                      # filter type = None
        for px_ in row:
            raw += bytes(px_)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def _ico(images):
    """把若干张 PNG 包成一个 ICO（Vista 起的 PNG 帧格式）。"""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, datas = bytearray(), bytearray()
    for w, h, png in images:
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0,
                               1, 32, len(png), offset)
        datas += png
        offset += len(png)
    return bytes(out) + bytes(entries) + bytes(datas)


def make_ico(path):
    images = [(s, s, png_bytes(_render(s))) for s in SIZES]
    data = _ico(images)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return path


def ensure_icon(path):
    """assets/app.ico 已经存在就直接用，缺了才现生成一个兜底。"""
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return make_ico(path)


def window_photo(size=32):
    """
    给 Tk 用的 base64 PNG 字符串（tk.PhotoImage(data=...) 吃这个）。

    选 base64 而不是临时 .png 文件：打包成 onefile exe 之后资源在临时解压目录里，
    路径一旦算错就是"图标没了"，而数据直接进内存没有这条路可错。
    """
    return base64.b64encode(png_bytes(_render(size))).decode("ascii")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    print(make_ico(os.path.abspath(os.path.join(here, "..", "assets", "app.ico"))))
