# -*- coding: utf-8 -*-
"""
make_icon.py —— 把一张大 PNG 母图做成多尺寸 .ico（纯标准库，不依赖 Pillow）

用法：
    python make_icon.py <母图.png> [输出.ico]

开发期工具，不进 exe。为什么要它：
  * 运行时用 Python 现算的水滴太"素"，16px 托盘还行，exe 图标看着像占位图
  * AI 出的母图是 1024 的实色底 PNG，要抠底 + 缩放 + 打包成 ICO 才能用
  * Pillow 是三方依赖，这个项目承诺零依赖，所以 PNG 解码/缩放/ICO 封装都手写

抠底：母图要求纯色品红底。品红的特征是"绿通道远低于红和蓝"，所以用
spill = min(R,B) - G 估"有多品红"，再从边框带实测背景的实际颜色和噪声
下限（AI 出的图看着是纯品红，其实带 JPEG 噪点，硬按 #FF00FF 算会留一层
灰雾）。最后 C = (C_obs - (1-a)*BG) / a 还原真实颜色，边缘不带粉边。
"""

import os
import struct
import sys
import zlib

SIZES = (256, 128, 64, 48, 32, 24, 16)


# ---------------------------------------------------------------- PNG 解码

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def read_png(path):
    """返回 (width, height, channels, bytes)，只支持 8 位非隔行（够用了）。"""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 文件")
    pos = 8
    idat = bytearray()
    w = h = depth = ctype = interlace = 0
    palette = None
    trns = None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            w, h, depth, ctype, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            trns = body
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    if depth != 8 or interlace != 0:
        raise ValueError("只支持 8 位非隔行 PNG，实际 depth=%s interlace=%s"
                         % (depth, interlace))
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    if ctype == 3:
        raise ValueError("调色板 PNG 请转成 RGB/RGBA 再来")
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ftype == 3:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 255
        elif ftype == 4:
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                c = prev[i - ch] if i >= ch else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)


# ---------------------------------------------------------------- 抠底 / 缩放

def _spill(r, g, b):
    """这个像素有多"品红"：品红的绿通道远低于红和蓝。"""
    return max(0, min(r, b) - g)


def measure_bg(w, h, ch, pixels, frac=0.02):
    """
    从四周边框估背景：返回 (背景色, 背景 spill 下限)。

    不能假设背景是精确的 #FF00FF —— 这次母图实际是带 JPEG 噪点的
    (244, 9, 233) 一堆颜色，spill 在 180~215 之间飘。直接按理论值
    255 归一化的话，整张背景会留下 alpha≈65 的一层灰雾，缩到小尺寸
    后图标就"糊"在浅灰底上。所以拿边框带里最"实"的那批像素当下限，
    低于它才算透明。
    """
    band = max(6, min(w, h) // 48)
    spills = []
    rs = gs = bs = n = 0
    for y in range(h):
        edge_row = y < band or y >= h - band
        base = y * w * ch
        for x in range(w):
            if not (edge_row or x < band or x >= w - band):
                continue
            j = base + x * ch
            r, g, b = pixels[j], pixels[j + 1], pixels[j + 2]
            sp = _spill(r, g, b)
            spills.append(sp)
            rs += r
            gs += g
            bs += b
            n += 1
    spills.sort()
    floor = spills[int(len(spills) * frac)]
    return (rs // n, gs // n, bs // n), floor


def unkey(w, h, ch, pixels, floor, bg):
    """品红底 → RGBA。floor 以下（即非背景）才是不透明。"""
    out = bytearray(w * h * 4)
    f = float(floor)
    bgr, bgg, bgb = float(bg[0]), float(bg[1]), float(bg[2])
    for i in range(w * h):
        j = i * ch
        r, g, b = pixels[j], pixels[j + 1], pixels[j + 2]
        sp = _spill(r, g, b)
        if sp >= f:
            continue                            # 背景：留全透明
        a = (f - sp) / f
        if a < 0.30:
            # 抠底不可能抠干净：背景噪点会留下 alpha 几十的零星散点，
            # 它们会把外接框撑满整张图、还会在小图标上糊出一层脏边。
            # 0.30 以下直接当背景，边缘的柔和度交给缩放时的面积平均。
            continue
        a = min(1.0, a * 1.25)                  # 把抗锯齿边缘往实里推一点，去粉边
        if a < 1.0:
            inv = 1.0 - a
            r = (r - inv * bgr) / a
            g = (g - inv * bgg) / a
            b = (b - inv * bgb) / a
        k = i * 4
        out[k] = int(max(0, min(255, r)))
        out[k + 1] = int(max(0, min(255, g)))
        out[k + 2] = int(max(0, min(255, b)))
        out[k + 3] = int(a * 255)
    return w, h, bytes(out)


def bbox(w, h, rgba, min_ratio=0.004):
    """
    内容外接框。按"这一行/列的不透明像素够不够多"来定边界，
    而不是"有没有一个像素"，否则零星噪点会把框撑满整张图。
    """
    row_a = [0] * h
    col_a = [0] * w
    for y in range(h):
        base = y * w * 4
        acc = 0
        for x in range(w):
            a = rgba[base + x * 4 + 3]
            if a:
                acc += a
                col_a[x] += a
        row_a[y] = acc
    peak_r = max(row_a)
    peak_c = max(col_a)
    if not peak_r or not peak_c:
        raise ValueError("没找到不透明内容，母图背景色可能不是品红")
    tr, tc = peak_r * min_ratio, peak_c * min_ratio
    ys = [y for y in range(h) if row_a[y] > tr]
    xs = [x for x in range(w) if col_a[x] > tc]
    return xs[0], ys[0], xs[-1], ys[-1]


def crop_pad(w, h, rgba, box, margin_ratio=0.04):
    """裁到内容外接方框，补成正方形并留一点边距。"""
    x0, y0, x1, y1 = box
    cw, chh = x1 - x0 + 1, y1 - y0 + 1
    side = max(cw, chh)
    pad = int(side * margin_ratio)
    side += pad * 2
    out = bytearray(side * side * 4)
    ox = (side - cw) // 2
    oy = (side - chh) // 2
    for y in range(chh):
        src = ((y0 + y) * w + x0) * 4
        dst = ((oy + y) * side + ox) * 4
        out[dst:dst + cw * 4] = rgba[src:src + cw * 4]
    return side, side, bytes(out)


def _box(w, h, rgba, size):
    """面积平均缩放（盒式滤波 + alpha 加权），一步到位。"""
    out = bytearray(size * size * 4)
    sx = float(w) / size
    sy = float(h) / size
    for y in range(size):
        y0 = int(y * sy)
        y1 = min(h, max(y0 + 1, int((y + 1) * sy)))
        for x in range(size):
            x0 = int(x * sx)
            x1 = min(w, max(x0 + 1, int((x + 1) * sx)))
            n = aa = ar = ag = ab = 0.0
            for yy in range(y0, y1):
                base = yy * w * 4
                for xx in range(x0, x1):
                    k = base + xx * 4
                    a = rgba[k + 3]
                    n += 1
                    aa += a
                    # 非预乘 alpha 不能直接平均颜色，先乘上 alpha 再加权
                    ar += rgba[k] * a
                    ag += rgba[k + 1] * a
                    ab += rgba[k + 2] * a
            k = (y * size + x) * 4
            if n == 0 or aa <= 0:
                continue
            out[k] = int(ar / aa)
            out[k + 1] = int(ag / aa)
            out[k + 2] = int(ab / aa)
            out[k + 3] = int(aa / n)
    return size, size, bytes(out)


def _halve(w, h, rgba):
    """2x2 折半，先把大图降到接近目标尺寸再一步盒式，否则每档都全图扫描太慢。"""
    if w % 2 or h % 2:                        # 补一列/一行全透明，边缘本来就是空的
        nw, nh = w + w % 2, h + h % 2
        pad = bytearray(nw * nh * 4)
        for y in range(h):
            pad[y * nw * 4:(y * nw * 4) + w * 4] = rgba[y * w * 4:(y * w * 4) + w * 4]
        w, h, rgba = nw, nh, bytes(pad)
    ow, oh = w // 2, h // 2
    out = bytearray(ow * oh * 4)
    for y in range(oh):
        r0 = (y * 2) * w * 4
        r1 = (y * 2 + 1) * w * 4
        for x in range(ow):
            i0 = x * 2 * 4
            i1 = (x * 2 + 1) * 4
            tot = 0.0
            cr = cg = cb = 0.0
            for base in (r0, r1):
                for idx in (i0, i1):
                    k = base + idx
                    a = rgba[k + 3]
                    tot += a
                    cr += rgba[k] * a
                    cg += rgba[k + 1] * a
                    cb += rgba[k + 2] * a
            k = (y * ow + x) * 4
            if tot <= 0:
                continue
            out[k] = int(cr / tot)
            out[k + 1] = int(cg / tot)
            out[k + 2] = int(cb / tot)
            out[k + 3] = int(tot / 4.0)
    return ow, oh, bytes(out)


def shrink(w, h, rgba, size):
    while w >= size * 2 or h >= size * 2:
        w, h, rgba = _halve(w, h, rgba)
    return _box(w, h, rgba, size)


def harden(w, h, rgba, floor=56, gain=1.6):
    """
    小尺寸收紧半透明边缘。16/24px 时整圈淡边会把水滴"化开"，
    放在浅色任务栏上看着像蒙了层雾，所以低 alpha 直接砍掉、
    剩下的往实里推。
    """
    out = bytearray(rgba)
    for i in range(w * h):
        k = i * 4
        a = out[k + 3]
        if a == 0:
            continue
        out[k + 3] = 0 if a <= floor else min(255, int(a * gain))
    return bytes(out)


def saturize(w, h, rgba, boost=1.18, gamma=0.92):
    """
    小尺寸提一点饱和度：1024 的图缩到 16px 会整体发灰，
    托盘里看着像蒙了层灰。只给小尺寸用。
    """
    out = bytearray(rgba)
    for i in range(w * h):
        k = i * 4
        if out[k + 3] == 0:
            continue
        r, g, b = out[k], out[k + 1], out[k + 2]
        gray = (r * 30 + g * 59 + b * 11) // 100
        r = int(max(0, min(255, gray + (r - gray) * boost)))
        g = int(max(0, min(255, gray + (g - gray) * boost)))
        b = int(max(0, min(255, gray + (b - gray) * boost)))
        out[k] = int(255 * (r / 255.0) ** gamma)
        out[k + 1] = int(255 * (g / 255.0) ** gamma)
        out[k + 2] = int(255 * (b / 255.0) ** gamma)
    return bytes(out)


# ---------------------------------------------------------------- ICO / PNG 输出

def ico_frame(w, h, rgba):
    """一张图 → ICO 里的一个 DIB（BGRA 预乘 + 全 0 AND mask，靠 alpha 透明）。"""
    xor = bytearray()
    row_bytes = w * 4
    for y in range(h - 1, -1, -1):
        row = rgba[y * row_bytes:(y + 1) * row_bytes]
        for x in range(w):
            i = x * 4
            r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
            xor += bytes((int(b * a / 255), int(g * a / 255),
                          int(r * a / 255), a))
    mask_row = ((w + 31) // 32) * 4
    and_mask = bytes(mask_row * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def build_ico(frames):
    """frames: [(size, dib_bytes), ...]"""
    count = len(frames)
    out = bytearray(struct.pack("<HHH", 0, 1, count))
    offset = 6 + 16 * count
    for size, data in frames:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    for _size, data in frames:
        out += data
    return bytes(out)


def write_png(path, w, h, rgb):
    """rgb: 每像素 3 字节，自上而下。用来出预览图给人眼看。"""
    raw = bytearray()
    for y in range(h):
        raw += b"\x00"
        raw += rgb[y * w * 3:(y + 1) * w * 3]

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def _nearest(w, h, rgba, size):
    """最近邻放到 size×size，只为预览能看清小尺寸的真实像素。"""
    out = bytearray(size * size * 4)
    sx = float(w) / size
    sy = float(h) / size
    for y in range(size):
        sy_row = min(h - 1, int(y * sy)) * w * 4
        for x in range(size):
            k = sy_row + min(w - 1, int(x * sx)) * 4
            d = (y * size + x) * 4
            out[d:d + 4] = rgba[k:k + 4]
    return size, size, bytes(out)


def preview(path, images):
    """
    两行：深灰底 + 浅灰底，每格都把该尺寸放到 128 见方看真实像素。

    看深色行判断"够不够清楚"，看浅色行判断抠底有没有留一圈粉边/灰边。
    """
    cell = 128
    pad = 8
    w = len(images) * (cell + pad) + pad
    h = (cell + pad) * 2 + pad
    rows = [(pad, (32, 34, 40)), (cell + pad * 2, (243, 244, 246))]
    buf = bytearray(w * h * 3)
    for idx, (size, rgba) in enumerate(images):
        dw, dh, disp = _nearest(size, size, rgba, cell)
        for top, row_bg in rows:
            ox = pad + idx * (cell + pad)
            oy = top
            for y in range(dh):
                for x in range(dw):
                    k = (y * dw + x) * 4
                    a = disp[k + 3]
                    if a == 0:
                        continue
                    d = ((oy + y) * w + (ox + x)) * 3
                    for c, base in enumerate(row_bg):
                        buf[d + c] = (disp[k + c] * a + base * (255 - a)) // 255
    write_png(path, w, h, bytes(buf))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src or not os.path.exists(src):
        print(__doc__)
        return 2
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "app.ico")

    w, h, ch, pixels = read_png(src)
    print("母图 %dx%d %d 通道" % (w, h, ch))
    bg, floor = measure_bg(w, h, ch, pixels)
    print("背景色 %s，spill 下限 %d" % (bg, floor))
    w, h, rgba = unkey(w, h, ch, pixels, floor, bg)
    box = bbox(w, h, rgba)
    print("内容外接框 %s（%dx%d）" % (box, box[2] - box[0] + 1, box[3] - box[1] + 1))
    w, h, rgba = crop_pad(w, h, rgba, box)

    frames, shown = [], []
    for size in SIZES:
        sw, sh, data = shrink(w, h, rgba, size)
        # 越小越要"下手"：1024 的高光到 16px 会占掉半个水滴，
        # 放在浅色任务栏上就是一个发白的糊点。
        if size <= 16:
            data = saturize(sw, sh, data, boost=1.45, gamma=1.06)
            data = harden(sw, sh, data, floor=72, gain=1.8)
        elif size <= 24:
            data = saturize(sw, sh, data, boost=1.32)
            data = harden(sw, sh, data)
        elif size <= 32:
            data = saturize(sw, sh, data, boost=1.16)
        frames.append((size, ico_frame(sw, sh, data)))
        shown.append((size, data))
        print("  %d px：%d bytes" % (size, len(frames[-1][1])))

    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "wb") as fh:
        fh.write(build_ico(frames))
    print("写入 %s（%d bytes）" % (dst, os.path.getsize(dst)))

    prev = os.path.splitext(dst)[0] + "_preview.png"
    preview(prev, shown)
    print("预览 %s" % prev)
    return 0


if __name__ == "__main__":
    sys.exit(main())
