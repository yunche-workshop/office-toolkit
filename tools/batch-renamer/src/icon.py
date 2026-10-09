# -*- coding: utf-8 -*-
"""
icon.py —— 纯标准库生成 exe 图标

不依赖任何美术素材：手写 PNG 编码再包进 ICO 容器。
图形是「两行文件名 + 一个箭头」，一眼能看出是改名工具。

assets/app.ico 存在就直接用，不存在才现生成一个兜底。
"""

import os
import zlib
import struct

BG = (43, 92, 255, 255)      # 主色蓝
WHITE = (255, 255, 255, 255)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _round_rect(s):
    """返回 size x size 的 RGBA 矩阵：圆角实心方块。"""
    r = s * 0.22
    rows = []
    for y in range(s):
        row = []
        for x in range(s):
            # 圆心在四个角
            cx = min(max(x + 0.5, r), s - r)
            cy = min(max(y + 0.5, r), s - r)
            dx = x + 0.5 - cx
            dy = y + 0.5 - cy
            inside = (dx * dx + dy * dy) <= r * r
            row.append(BG if inside else (0, 0, 0, 0))
        rows.append(row)
    return rows


def _fill(rows, x0, y0, x1, y1, color):
    s = len(rows)
    for y in range(int(round(y0 * s)), int(round(y1 * s))):
        if not (0 <= y < s):
            continue
        for x in range(int(round(x0 * s)), int(round(x1 * s))):
            if 0 <= x < s:
                rows[y][x] = color


def _arrow(rows, cx, cy, half_w, half_h, color):
    """一个向右的实心三角。"""
    s = len(rows)
    for i in range(int(half_h * s * 2)):
        t = i / max(1, int(half_h * s * 2) - 1)      # 0..1 从上到下
        width = half_w * s * (1 - abs(t - 0.5) * 2) * 2
        y = int(cy * s - half_h * s + i)
        if not (0 <= y < s):
            continue
        for j in range(int(width)):
            x = int(cx * s - half_w * s + j)
            if 0 <= x < s:
                rows[y][x] = color


def _render(s):
    rows = _round_rect(s)
    # 上面一条：旧名字
    _fill(rows, 0.20, 0.26, 0.62, 0.36, WHITE)
    # 下面一条：新名字
    _fill(rows, 0.20, 0.64, 0.62, 0.74, WHITE)
    # 右侧箭头，把两条连起来
    _arrow(rows, 0.76, 0.50, 0.09, 0.13, WHITE)
    return rows


def _png(rows):
    """手写 PNG（RGBA8）。"""
    s = len(rows)
    raw = bytearray()
    for row in rows:
        raw.append(0)                     # filter type = None
        for r, g, b, a in row:
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", s, s, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def _ico(images):
    """把若干张 PNG 包成一个 ICO。"""
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = bytearray()
    datas = bytearray()
    for w, h, png in images:
        entries += struct.pack(
            "<BBBBHHII",
            w % 256, h % 256, 0, 0, 1, 32, len(png), offset)
        datas += png
        offset += len(png)
    return bytes(out) + bytes(entries) + bytes(datas)


def make_ico(path):
    images = []
    for s in SIZES:
        images.append((s, s, _png(_render(s))))
    data = _ico(images)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def ensure_icon(path):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    return make_ico(path)


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "..", "assets", "app.ico")
    print(make_ico(os.path.abspath(out)))
