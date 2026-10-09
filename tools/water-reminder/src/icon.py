# -*- coding: utf-8 -*-
"""
icon.py —— 托盘 / exe 图标的兜底生成

优先用 assets/app.ico（开发期用 make_icon.py 从母图生成的多尺寸图标）。
这个模块只在资源缺失时兜底：用纯 Python 现画一个水滴写成 .ico（约 6 KB），
保证即使打包时漏了 --add-data，程序也照样有图标而不是退成默认地球图标。
"""

import os
import struct

TOP_COLOR = (155, 231, 255)  # 浅蓝（水滴顶部）
BOTTOM_COLOR = (21, 101, 192)  # 深蓝（水滴底部）
HIGHLIGHT = (255, 255, 255)


def _inside(x, y, size):
    """水滴形状：下部圆 + 上部收成尖，返回是否落在形状内。"""
    cx = size / 2.0
    cy = size * 0.60
    r = size * 0.40
    top = size * 0.06
    if y < top:
        return False
    if y >= cy:
        dx = x - cx
        dy = y - cy
        return dx * dx + dy * dy <= r * r
    t = (y - top) / (cy - top)
    return abs(x - cx) <= r * (t ** 0.62)


def _render(size):
    """返回 RGBA 像素的 bytearray（自上而下，每行 RGBA）。"""
    buf = bytearray(size * size * 4)
    top = size * 0.06
    span = max(1.0, size - top)
    hx = size / 2.0 - size * 0.07
    hy = size * 0.52
    hr_x = size * 0.15
    hr_y = size * 0.11
    step = 1.0 / 3.0  # 3x3 超采样，做边缘抗锯齿

    for y in range(size):
        for x in range(size):
            hits = 0
            total = 0
            for sy in range(3):
                for sx in range(3):
                    px = x + (sx + 0.5) * step
                    py = y + (sy + 0.5) * step
                    total += 1
                    if _inside(px, py, size):
                        hits += 1
            if not hits:
                continue
            coverage = hits / float(total)

            t = (y - top) / span
            base = [
                TOP_COLOR[i] + (BOTTOM_COLOR[i] - TOP_COLOR[i]) * t for i in range(3)
            ]

            # 左上角高光
            d = ((x - hx) / hr_x) ** 2 + ((y - hy) / hr_y) ** 2
            if d < 1.0:
                k = (1.0 - d) * 0.65 * coverage
                base = [base[i] + (HIGHLIGHT[i] - base[i]) * k for i in range(3)]

            idx = (y * size + x) * 4
            buf[idx] = int(max(0, min(255, base[0])))
            buf[idx + 1] = int(max(0, min(255, base[1])))
            buf[idx + 2] = int(max(0, min(255, base[2])))
            buf[idx + 3] = int(255 * coverage)
    return buf


def _dib(size):
    """把 RGBA 像素转成 ICO 里的 DIB（BGRA + AND mask）。"""
    rgba = _render(size)
    xor = bytearray()
    row_bytes = size * 4
    for y in range(size - 1, -1, -1):  # DIB 自下而上
        row = rgba[y * row_bytes : (y + 1) * row_bytes]
        for x in range(size):
            i = x * 4
            r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
            # 预乘 alpha，避免某些渲染器出现黑边
            xor += bytes(
                (
                    int(b * a / 255),
                    int(g * a / 255),
                    int(r * a / 255),
                    a,
                )
            )
    mask_row = ((size + 31) // 32) * 4
    and_mask = bytes(mask_row * size)  # 全 0：完全依赖 alpha 通道

    header = struct.pack(
        "<IiiHHIIiiII",
        40,  # biSize
        size,  # biWidth
        size * 2,  # biHeight（含 mask）
        1,  # biPlanes
        32,  # biBitCount
        0,  # biCompression = BI_RGB
        len(xor) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    return header + bytes(xor) + and_mask


def build_ico_bytes(sizes=(16, 32, 48, 64)):
    """生成包含多个尺寸的 .ico 文件字节。"""
    images = [_dib(s) for s in sizes]
    count = len(sizes)
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    offset = 6 + 16 * count
    for s, data in zip(sizes, images):
        out += struct.pack(
            "<BBBBHHII",
            s % 256,
            s % 256,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
    for data in images:
        out += data
    return bytes(out)


def is_ico_file(path):
    """看文件头判断是不是真的 ICO（资源被截断 / 打包错成别的文件时能发现）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(6)
    except OSError:
        return False
    return len(head) == 6 and head[:4] == b"\x00\x00\x01\x00" and head[4:6] != b"\x00\x00"


def bundled_icon():
    """打包资源里的 app.ico；没有或损坏返回 ""。"""
    try:
        import core

        path = core.resource_path("app.ico")
    except Exception:
        return ""
    return path if path and is_ico_file(path) else ""


def icon_path(data_dir):
    """给托盘用的 .ico：优先打包的多尺寸图标，退化为运行时自算。"""
    return bundled_icon() or ensure_icon(os.path.join(data_dir, "icon.ico"))


def ensure_icon(path):
    """图标不存在时生成一个；返回路径。"""
    try:
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(build_ico_bytes())
        return path
    except Exception:
        return path


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "icon.ico"
    with open(target, "wb") as fh:
        fh.write(build_ico_bytes())
    print("wrote", target, os.path.getsize(target), "bytes")
