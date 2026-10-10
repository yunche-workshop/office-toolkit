# -*- coding: utf-8 -*-
"""
core.py —— 图片转 PDF 的内核（纯标准库，不碰界面）

为什么自己写而不装 img2pdf：这个合集的卖点是"零第三方依赖 + 单文件绿色版"，
多装一个库就等于在内网电脑上判自己死刑。而 PDF 本身就是公开格式，图片这层
只要能干两件事：

  1. JPEG —— PDF 原生就有 /DCTDecode，**把 JPEG 码流原样塞进去**即可。
     不解码也不重编码，所以无损、快，20 MB 的照片也是毫秒级，产物体积≈原图
  2. PNG  —— zlib 解开 IDAT → 按扫描线把滤波还原成像素 → 需要的话拆出 alpha
     → 再 zlib 压一遍交给 /FlateDecode

界面只负责选文件、排序、设参数。这一层全是纯函数，直接跑单测。

设计要点：
- 一张图坏掉不影响整批：convert() 逐张 try，失败的记人话原因，成功的照常进 PDF
- EXIF 方向靠内容流的 CTM 表达，不重编码 JPEG（手机照片转出来是横躺的最容易被骂）
- PNG 的 alpha 拆成 /SMask 软掩膜，透明区在 PDF 里保持透明
- 输出走 tmp + os.replace，写一半断电不会留下打不开的半个 PDF
"""

import os
import time
import zlib

import brand

APP_NAME = "Img2Pdf"

# 界面上显示的那个中文名：标题栏、顶栏、署名行、关于窗、--version
# 全都读这一处。原来在 ui.py 里写死了四遍，改名字得挨个找。
TOOL_CN = "图片转 PDF"

# 版本号的唯一来源：exe 文件属性（build.py）、界面署名、关于窗都读这里。
VERSION = "0.1.0"

# 一句话简介：关于窗和 README 用同一句，别在界面里另写一版。
SUMMARY = "把一堆 JPG/PNG 拼成一个 PDF，JPEG 原样封装、不重编码"

SUPPORTED_EXTS = (".jpg", ".jpeg", ".png")

# PDF 的坐标单位是磅，1 英寸 = 72 磅
PT_PER_INCH = 72.0

# 图片自己没带密度信息时按这个 DPI 排版：300 太高（扫描件本来就标 300，
# 但手机照片的 72/96 会让页面小到打印发虚），72 又让 A4 变成 4000 磅宽。
# 200 是个折中，界面允许改。
DEFAULT_DPI = 200.0

# 版心留边的默认值（磅）：28pt ≈ 1 厘米，打印时不会被裁掉内容。
# 原来这个数在 layout / build_pdf / convert 三个签名和界面的初始值里
# 各写了一遍（一共四次），改一次要找四处 —— 收在这儿。
DEFAULT_MARGIN = 28.0

PAPER = {
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "Letter": (612.0, 792.0),
}

PAGE_MODES = ("image", "A4", "A5", "Letter")
ORIENTS = ("auto", "portrait", "landscape")

# 每页一张图，图块要占的对象号：内容流 + 图片 + 可选掩膜 + 页
_PER_PAGE = 4


class ImageError(Exception):
    """这张图读不了 / 不支持。整批转换时只跳过这一张，不中断。"""


# ---------------------------------------------------------------- 小工具

def ext_of(path):
    return os.path.splitext(path)[1].lower()


def _num(data, pos, n, little=False):
    """读无符号整数。越界一律当损坏，别把 IndexError 漏给调用方。"""
    if pos < 0 or pos + n > len(data):
        raise ImageError("文件在第 %d 字节处断了，数据不完整" % (pos + n))
    return int.from_bytes(data[pos:pos + n], "little" if little else "big")


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except (IOError, OSError) as exc:
        raise ImageError("打不开：%s" % exc)


# -------------------------------------------------------------------- JPEG
#
# JPEG 是"标记段"串起来的：FF <id> [2 字节长度] [载荷]。
# 只走到需要的段就停。注意：一遇到 SOS(0xDA) 必须收手，扫描数据里会出现
# 假的 FF 00 之外的一切字节，再按长度跳就跑到沟里去了。

_NO_LENGTH = frozenset([0x01] + list(range(0xD0, 0xDA + 1)))
_SOF = frozenset([0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                  0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF])


def read_jpeg(data):
    """解 JPEG 头，返回 {width,height,bpc,comps,dpi,orientation,progressive}。"""
    if data[:2] != b"\xff\xd8":
        raise ImageError("不是 JPEG（开头缺 FFD8）")

    out = {"width": 0, "height": 0, "bpc": 8, "comps": 0, "dpi": None,
           "orientation": 1, "progressive": False}
    pos, n = 2, len(data)
    while pos + 3 < n:
        if data[pos] != 0xFF:
            pos += 1                        # 段间垃圾字节（有些相机写 0）
            continue
        marker = data[pos + 1]
        if marker == 0xFF:                  # 填充
            pos += 1
            continue
        if marker in _NO_LENGTH:
            pos += 2
            continue
        if marker == 0xDA:                  # 扫描开始，后面别乱跳
            break
        seglen = _num(data, pos + 2, 2)
        body, end = pos + 4, pos + 2 + seglen
        if seglen < 2 or end > n:
            raise ImageError("标记段 %02X 声明长度 %d，超出文件范围" % (marker, seglen))

        if marker in _SOF:
            out["bpc"] = data[body]
            out["height"] = _num(data, body + 1, 2)
            out["width"] = _num(data, body + 3, 2)
            out["comps"] = _num(data, body + 5, 1)
            out["progressive"] = (marker == 0xC2)
            if not out["width"] or not out["height"]:
                raise ImageError("JPEG 的宽高写成 0，文件损坏")
            break

        if marker == 0xE0 and data[body:body + 5] == b"JFIF\x00":
            # 版本(2) 单位(1) X密度(2) Y密度(2)
            units = data[body + 7]
            xd = _num(data, body + 8, 2)
            yd = _num(data, body + 10, 2)
            if xd and yd and xd == yd and units in (1, 2):
                dpi = xd * 2.54 if units == 2 else float(xd)
                # 1x1 是"没说过"，别当真
                out["dpi"] = dpi if dpi > 1.5 else None

        elif marker == 0xE1 and data[body:body + 6] == b"Exif\x00\x00":
            _read_exif(data[body + 6:end], out)

        pos = end

    if not out["width"]:
        raise ImageError("没找到 SOF 段，读不出宽高")
    if out["comps"] not in (1, 3, 4):
        raise ImageError("JPEG 分量数 %d 不认识（只支持灰度 1、RGB 3、CMYK 4）"
                         % out["comps"])
    if out["bpc"] != 8:
        raise ImageError("只支持 8 bit/分量的 JPEG，这张是 %d bit" % out["bpc"])
    return out


def _read_exif(tiff, out):
    """从 TIFF 头取 Orientation(0x0112) 与 XResolution(0x011A)，只走 IFD0。"""
    if len(tiff) < 8:
        return
    head = tiff[:2]
    if head not in (b"II", b"MM"):
        return
    little = (head == b"II")
    if _num(tiff, 2, 2, little) != 42:
        return
    ifd = _num(tiff, 4, 4, little)
    if ifd + 2 > len(tiff):
        return
    count = _num(tiff, ifd, 2, little)
    unit = None                             # ResolutionUnit：2=英寸，3=厘米
    xres = None
    for i in range(count):
        e = ifd + 2 + i * 12
        if e + 12 > len(tiff):
            break
        tag = _num(tiff, e, 2, little)
        typ = _num(tiff, e + 2, 2, little)
        cnt = _num(tiff, e + 4, 4, little)
        val = e + 8
        if tag == 0x0112 and typ == 3:      # SHORT 直接躺在值域里
            o = _num(tiff, val, 2, little)
            if 1 <= o <= 8:
                out["orientation"] = o
        elif tag == 0x011A and typ == 5 and cnt >= 1:
            # 有理数：偏移后面放着 分子/分母。注意 282 排在 296 前面，
            # 所以 ResolutionUnit 是"之后"才知道的，这里先存下来别急着用
            off = _num(tiff, val, 4, little)
            if off + 8 <= len(tiff):
                num = _num(tiff, off, 4, little)
                den = _num(tiff, off + 4, 4, little)
                if den:
                    xres = num / float(den)
        elif tag == 0x0128 and typ == 3:
            unit = _num(tiff, val, 2, little)
    if xres and out["dpi"] is None and unit in (2, 3):
        dpi = xres if unit == 2 else xres * 2.54
        if 24 <= dpi <= 4000:
            out["dpi"] = dpi


# --------------------------------------------------------------------- PNG

_PNG_SIG = b"\x89PNG\r\n\x1a\n"

# 颜色类型 → (通道数, 带 alpha, 是索引)
PNG_COLOR = {
    0: (1, False, False),   # 灰度
    2: (3, False, False),   # RGB
    3: (1, False, True),    # 索引色
    4: (2, True, False),    # 灰度 + alpha
    6: (4, True, False),    # RGBA
}


def read_png_header(data):
    """只读 IHDR，顺便挡掉隔行/怪位深这些不支持的。"""
    if data[:8] != _PNG_SIG:
        raise ImageError("不是 PNG（签名不对）")
    if len(data) < 33 or _num(data, 8, 4) != 13 or data[12:16] != b"IHDR":
        raise ImageError("PNG 第一段不是合法的 IHDR")
    w = _num(data, 16, 4)
    h = _num(data, 20, 4)
    depth, color = data[24], data[25]
    compress, filter_id, interlace = data[26], data[27], data[28]
    if not w or not h:
        raise ImageError("PNG 的宽高写成 0")
    if compress != 0 or filter_id != 0:
        raise ImageError("只支持标准 deflate + 自适应滤波的 PNG")
    if interlace != 0:
        raise ImageError("这是隔行（Adam7）PNG，要算 7 趟扫描线、收益又小，不支持。"
                         "重新存成普通 PNG 就行")
    if color not in PNG_COLOR:
        raise ImageError("不认识的颜色类型 %d" % color)
    channels, has_alpha, indexed = PNG_COLOR[color]
    # PNG 规范里合法的组合就这几种，其余一律当损坏/超范围
    legal = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8),
             4: (8, 16), 6: (8, 16)}
    if depth not in legal[color]:
        raise ImageError("颜色类型 %d 不支持位深 %d（合法的是 %s）"
                         % (color, depth, "/".join(str(x) for x in legal[color])))
    return {"width": w, "height": h, "depth": depth, "color": color,
            "channels": channels, "has_alpha": has_alpha, "indexed": indexed,
            "dpi": _png_phys(data)}


def _png_phys(data):
    """pHYs：每米像素数 → DPI。没有返回 None。"""
    for kind, payload in _png_chunks(data):
        if kind == b"pHYs":
            if len(payload) >= 9 and payload[8] == 1:
                x = _num(payload, 0, 4)
                y = _num(payload, 4, 4)
                if x and y and x == y:
                    return x / 39.3701
            return None
    return None


def _png_chunks(data):
    """按顺序产出 (类型, 载荷)。CRC 不校验：那是解压和渲染之后才用得着的事，
    真损坏的文件下面几步一样会报错并给出人话原因。"""
    pos, n = 8, len(data)
    while pos + 8 <= n:
        length = _num(data, pos, 4)
        kind = data[pos + 4:pos + 8]
        if length > n - pos - 8:
            raise ImageError("PNG 段 %r 声明 %d 字节，超出文件长度" % (kind, length))
        yield kind, data[pos + 8:pos + 8 + length]
        pos += 8 + length + 4
        if kind == b"IEND":
            return


def _paeth(a, b, c):
    """PNG 滤波 4 的 Paeth 预测器，规范原文就是这个式子。"""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def png_scanlines(info, raw):
    """
    把解压后的扫描线逐个滤波还原，返回每行的字节列表。

    只做"去滤波"，通道怎么拼给下面。
    """
    w, h = info["width"], info["height"]
    depth, chan = info["depth"], info["channels"]
    row_bytes = (w * chan * depth + 7) // 8
    # 滤波回看的是"一个像素"，不足 1 字节时按 1 字节算（规范如此）
    bpp = max(1, (chan * depth + 7) // 8)

    need = h * (row_bytes + 1)
    if len(raw) < need:
        raise ImageError("PNG 解压出来只有 %d 字节，按 %dx%d 该有 %d 字节，"
                         "数据缺了一块" % (len(raw), w, h, need))
    rows = []
    prev = bytearray(row_bytes)
    for y in range(h):
        base = y * (row_bytes + 1)
        f = raw[base]
        line = bytearray(raw[base + 1:base + 1 + row_bytes])
        if f == 0:
            pass
        elif f == 1:                                   # Sub：加左边
            for i in range(bpp, row_bytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif f == 2:                                   # Up：加上一行
            for i in range(row_bytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif f == 3:                                   # Average
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif f == 4:                                   # Paeth
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xFF
        else:
            raise ImageError("第 %d 行用了不存在的滤波类型 %d" % (y, f))
        rows.append(bytes(line))
        prev = bytearray(line)
    return row_bytes, rows


def _row_samples(row, count, depth):
    """
    取一行里的 count 个采样，16 位降到 8 位（直接取高字节）。

    1/2/4 位不走这里——那种只有灰度和索引色会用，调用方自己摊开。
    """
    if depth == 8:
        return row[:count]
    if depth == 16:
        return bytes(row[i * 2] for i in range(count))
    raise ImageError("内部错误：位深 %d 不该走 _row_samples" % depth)


def _pixel_indices(row, width, depth):
    """
    1/2/4 位一行摊开成 width 个**原始索引**。

    不放大到 0-255——索引是查调色板用的，不是亮度值。
    （低比特灰度走的是原样透传，压根不经过这个函数。）
    """
    step = 8 // depth
    mask = (1 << depth) - 1
    vals = []
    for b in row:
        for k in range(step):
            vals.append((b >> (8 - depth * (k + 1))) & mask)
    return vals[:width]


def png_to_pdf_image(data):
    """
    解 PNG → PDF 图块：{width,height,colorspace,bpc,data,smask,dpi}。

    data 是已经 zlib 压好、可直接给 /FlateDecode 的像素流；
    smask 是同尺寸灰度流（alpha 拆出来的软掩膜），没有 alpha 就是 None。
    """
    info = read_png_header(data)
    idat = b"".join(p for k, p in _png_chunks(data) if k == b"IDAT")
    if not idat:
        raise ImageError("PNG 里没有 IDAT 数据")
    try:
        raw = zlib.decompress(idat)
    except zlib.error as exc:
        raise ImageError("PNG 数据解压失败（%s），文件损坏" % exc)

    row_bytes, rows = png_scanlines(info, raw)
    w, h = info["width"], info["height"]
    depth, color = info["depth"], info["color"]

    body = bytearray()
    alpha = None
    bpc = 8

    if info["indexed"]:
        plte = trns = b""
        for k, p in _png_chunks(data):
            if k == b"PLTE":
                plte = p
            elif k == b"tRNS":
                trns = p
            elif k == b"IDAT":
                break
        if not plte:
            raise ImageError("索引色 PNG 缺 PLTE 调色板")
        colorspace = b"/DeviceRGB"
        entries = len(plte) // 3
        # 一次摊平，RGB 和 alpha 都用它；顺便只查一次越界（逐像素 try 太慢）
        plan = [_pixel_indices(row, w, depth) for row in rows]
        flat = [idx for line in plan for idx in line]
        if flat and max(flat) >= entries:
            raise ImageError("索引 %d 超出调色板（只有 %d 项）"
                             % (max(flat), entries))
        for line in plan:
            for idx in line:
                body += plte[idx * 3:idx * 3 + 3]
        if trns:
            alpha = bytes(trns[idx] if idx < len(trns) else 255
                          for line in plan for idx in line)
    elif color == 0 and depth < 8:
        # 1/2/4 位灰度：PDF 的 DeviceGray 原生支持这个位深，原样给下去
        colorspace = b"/DeviceGray"
        bpc = depth
        for row in rows:
            body += row
    else:
        chan = info["channels"]
        colorspace = b"/DeviceRGB" if chan >= 3 else b"/DeviceGray"
        if info["has_alpha"]:
            alpha = bytearray()
        for row in rows:
            s = _row_samples(row, w * chan, depth)
            if chan in (1, 3):
                body += s
            elif chan == 2:                    # 灰度 + alpha
                body += s[0::2]
                alpha += s[1::2]
            else:                              # RGBA
                for i in range(w):
                    body += s[i * 4:i * 4 + 3]
                    alpha += s[i * 4 + 3:i * 4 + 4]
        if isinstance(alpha, bytearray) and (not alpha or min(alpha) == 255):
            # 全不透明的 alpha 别写成掩膜——白多一倍体积，还让渲染器多跑一层混合
            alpha = None

    if not body:
        raise ImageError("PNG 解出来是空的")

    return {"kind": "png", "width": w, "height": h, "colorspace": colorspace,
            "bpc": bpc, "data": zlib.compress(bytes(body), 9),
            "smask": zlib.compress(alpha, 9) if alpha else None,
            "dpi": info["dpi"], "orientation": 1, "jpeg": False, "decode": None}


def jpeg_to_pdf_image(data):
    """JPEG：只读头，码流原样带走。这是这工具"无损"的全部原因。"""
    meta = read_jpeg(data)
    colorspace = {1: b"/DeviceGray", 3: b"/DeviceRGB",
                  4: b"/DeviceCMYK"}[meta["comps"]]
    # Adobe 的 CMYK JPEG 是反的（0=满墨），用 /Decode 把它翻回正常
    decode = [b"1 0"] * 4 if meta["comps"] == 4 else None
    return {"kind": "jpeg", "width": meta["width"], "height": meta["height"],
            "colorspace": colorspace, "bpc": 8, "data": data, "smask": None,
            "dpi": meta["dpi"], "orientation": meta["orientation"],
            "jpeg": True, "decode": decode}


# ------------------------------------------------------------------- 读文件

def load(path):
    """读一张图变成 PDF 图块。认不了抛 ImageError（带人话原因）。"""
    ext = ext_of(path)
    if ext not in SUPPORTED_EXTS:
        raise ImageError("不支持的格式「%s」（目前只认 .jpg .jpeg .png）"
                         % (ext or "无扩展名"))
    data = _read_bytes(path)
    if not data:
        raise ImageError("文件是空的")

    if data[:8] == _PNG_SIG:
        img = png_to_pdf_image(data)
    elif data[:2] == b"\xff\xd8":
        img = jpeg_to_pdf_image(data)
    else:
        raise ImageError("扩展名写着 %s，可内容既不是 JPEG 也不是 PNG" % ext)

    img["path"] = path
    img["name"] = os.path.basename(path)
    img["bytes"] = len(data)
    return img


def probe(path):
    """
    只读头部，不解像素、不压缩 —— 给界面的"规格"那一列用。

    为什么单开一个函数：load() 要把 PNG 的扫描线全解压、去滤波、再重压一遍，
    一张 4000x3000 的截图得小半秒。列表里加 200 张就是界面卡死几分钟。

    返回的字典和 load() 同形，但没有 data/smask；透明信息放在 has_alpha 里
    （是"这张带 alpha 通道"，不等于产物里真的写了掩膜 —— 全不透明的 RGBA
    会被 load() 丢掉掩膜，那是体积优化，列表里说"透明"并不骗人）。
    和 load() 的一致性由 tests/test_core.py 的 TestProbe 逐格式盯着。
    """
    ext = ext_of(path)
    if ext not in SUPPORTED_EXTS:
        raise ImageError("不支持的格式「%s」（目前只认 .jpg .jpeg .png）"
                         % (ext or "无扩展名"))
    data = _read_bytes(path)
    if not data:
        raise ImageError("文件是空的")

    if data[:8] == _PNG_SIG:
        info = read_png_header(data)
        chan, has_alpha, indexed = PNG_COLOR[info["color"]]
        if indexed:
            plte = trns = False
            for kind, payload in _png_chunks(data):
                if kind == b"PLTE":
                    plte = True
                elif kind == b"tRNS":
                    trns = True
                elif kind == b"IDAT":
                    break
            if not plte:
                raise ImageError("索引色 PNG 缺 PLTE 调色板")
            colorspace, bpc = b"/DeviceRGB", 8
            has_alpha = trns
        else:
            bpc = 8
            if info["color"] == 0 and info["depth"] < 8:
                bpc = info["depth"]
            colorspace = b"/DeviceRGB" if chan >= 3 else b"/DeviceGray"
        out = {"kind": "png", "width": info["width"], "height": info["height"],
               "colorspace": colorspace, "bpc": bpc, "data": None,
               "smask": None, "has_alpha": bool(has_alpha),
               "dpi": info["dpi"], "orientation": 1, "jpeg": False,
               "decode": None}
    elif data[:2] == b"\xff\xd8":
        meta = read_jpeg(data)
        colorspace = {1: b"/DeviceGray", 3: b"/DeviceRGB",
                      4: b"/DeviceCMYK"}[meta["comps"]]
        out = {"kind": "jpeg", "width": meta["width"], "height": meta["height"],
               "colorspace": colorspace, "bpc": 8, "data": None,
               "smask": None, "has_alpha": False, "dpi": meta["dpi"],
               "orientation": meta["orientation"], "jpeg": True,
               "decode": [b"1 0"] * 4 if meta["comps"] == 4 else None}
    else:
        raise ImageError("扩展名写着 %s，可内容既不是 JPEG 也不是 PNG" % ext)

    out["path"] = path
    out["name"] = os.path.basename(path)
    out["bytes"] = len(data)
    return out


# -------------------------------------------------------------- 排版与方向

def page_points(img, dpi=None):
    """图片按给定 DPI 摊成磅；图里带了密度就用图里的，都没有用 DEFAULT_DPI。"""
    d = float(dpi or img.get("dpi") or DEFAULT_DPI)
    if d <= 0:
        d = DEFAULT_DPI
    return (img["width"] * PT_PER_INCH / d, img["height"] * PT_PER_INCH / d)


# EXIF 方向 → 单位正方形到平行四边形的轴向 (A,B,C,D)。
# PDF 的 cm 参数是 [a b c d e f]：x = a*u + c*v + e，y = b*u + d*v + f。
# u 是图片从左到右，v 是从下到上，所以 u 轴对应 (a,b)、v 轴对应 (c,d)。
ORIENT_MATRIX = {
    1: (1, 0, 0, 1),      # 正常
    2: (-1, 0, 0, 1),     # 左右镜像
    3: (-1, 0, 0, -1),    # 转 180
    4: (1, 0, 0, -1),     # 上下镜像
    5: (0, 1, 1, 0),      # 沿主对角线转置
    6: (0, -1, 1, 0),     # 顺时针转 90（手机竖拍最常见）
    7: (0, -1, -1, 0),    # 沿副对角线转置
    8: (0, 1, -1, 0),     # 顺时针转 270
}


def display_size(img, sw, sh):
    """考虑方向之后，这张图在页面上占多大（磅）。"""
    sa, sb, sc, sd = ORIENT_MATRIX.get(img.get("orientation") or 1,
                                       (1, 0, 0, 1))
    if sa or sd:                     # 轴没互换
        return sw, sh
    return sh, sw


def place_matrix(img, sw, sh, x0, y0):
    """
    返回内容流用的 6 个数 (a,b,c,d,e,f)：把图片画成 (x0,y0) 起、
    已经按 EXIF 方向摆正的那个平行四边形。

    e/f 是算出来的：先取四个角的包围盒最小点，再把它平移到 (x0,y0)。
    这样八种方向共用一段代码，不用各写一遍偏移。
    """
    sa, sb, sc, sd = ORIENT_MATRIX.get(img.get("orientation") or 1,
                                       (1, 0, 0, 1))
    a, b = sa * sw, sb * sw
    c, d = sc * sh, sd * sh
    xs = [0, a, c, a + c]
    ys = [0, b, d, b + d]
    return (a, b, c, d, x0 - min(xs), y0 - min(ys))


def layout(img, page="image", orient="auto", margin=DEFAULT_MARGIN, dpi=None):
    """
    算这一页：页面尺寸（磅）+ 图片摆放。返回 dict。

    page:   'image' = 每页正好等于这张图（可按 dpi 换算物理尺寸）
            'A4'/'A5'/'Letter' = 固定纸张，图片等比放进版心，绝不拉伸
    orient: 'auto' = 图是横的就用横纸；'portrait'/'landscape' = 钉死
    margin: 版心留边（磅）。image 模式下页面会跟着变大，图居中。
    """
    margin = max(0.0, float(margin))
    pw, ph = page_points(img, dpi)
    dw, dh = display_size(img, pw, ph)

    if page == "image":
        W, H = dw + 2 * margin, dh + 2 * margin
        return {"page_w": W, "page_h": H, "x": margin, "y": margin,
                "sw": pw, "sh": ph, "disp_w": dw, "disp_h": dh}

    if page not in PAPER:
        raise ImageError("不认识的纸张：%s" % page)
    base = PAPER[page]
    if orient == "landscape":
        W, H = base[1], base[0]
    elif orient == "portrait":
        W, H = base
    else:                                    # auto：按摆正后的样子选横竖
        if margin * 2 >= min(base):
            W, H = base
        else:
            wide = (dw - 2 * margin) > (dh - 2 * margin)
            W, H = (base[1], base[0]) if wide else base

    margin = min(margin, min(W, H) / 2.0 - 1)
    iw, ih = W - 2 * margin, H - 2 * margin
    # 那个 1.0 很关键：只缩不放。一张 12 磅的图标塞进 A4 也不该被拉成一页纸，
    # 拉了只会糊，读者还会以为原图就那么大
    scale = min(1.0, iw / dw, ih / dh)
    sw, sh = pw * scale, ph * scale          # 缩放后仍按原像素长宽给矩阵用
    ndw, ndh = dw * scale, dh * scale
    x0 = (W - ndw) / 2.0
    y0 = (H - ndh) / 2.0
    return {"page_w": W, "page_h": H, "x": x0, "y": y0,
            "sw": sw, "sh": sh, "disp_w": ndw, "disp_h": ndh}


# --------------------------------------------------------------- PDF 写出

def _pdf_now():
    """PDF 的日期串：D:YYYYMMDDHHMMSS±HH'MM'。时区取本机实际的，不硬写 +08。"""
    t = time.localtime()
    off = getattr(t, "tm_gmtoff", 0) or 0
    sign = "+" if off >= 0 else "-"
    off = abs(off)
    return (b"D:" + time.strftime("%Y%m%d%H%M%S", t).encode("ascii")
            + ("%s%02d'%02d'" % (sign, off // 3600, (off % 3600) // 60)).encode("ascii"))


def _text_field(text):
    """PDF 字符串：ASCII 走字面串（括号里那几个字符要转义），
    其余走 UTF-16BE 十六进制串，省掉中文的转义麻烦。"""
    try:
        text.encode("ascii")
        out = []
        for ch in text:
            out.append("\\" + ch if ch in "\\()" else ch)
        return b"(" + "".join(out).encode("ascii") + b")"
    except (UnicodeEncodeError, UnicodeDecodeError):
        raw = ("\ufeff" + text).encode("utf-16-be")
        return b"<" + b"".join(b"%02X" % x for x in bytearray(raw)) + b">"


def _stream(extra, data):
    """/Length 是压缩后的字节数，也就是流本身的长度——别写成解压后的。"""
    return (b"<< " + extra + b" /Length %d >>\nstream\n" % len(data)
            + data + b"\nendstream")


class PdfWriter(object):
    """
    最小可用的写出器：先按对象号占位、后填内容，最后一次性算 xref 偏移。

    之所以要先占位：页对象要 /Parent 2 0 R，而 Pages 得等所有页号凑齐才能写；
    动态 append 会算错号，这是手写 PDF 最容易翻车的地方。
    """

    def __init__(self):
        self.objs = []

    def reserve(self, count):
        start = len(self.objs) + 1
        self.objs.extend([None] * count)
        return list(range(start, start + count))

    def put(self, num, body):
        self.objs[num - 1] = body

    def serialize(self, root=1, info=None):
        out = bytearray()
        out += b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n"     # 告诉工具这是二进制文件
        offsets = []
        for num, body in enumerate(self.objs, start=1):
            if body is None:
                raise ImageError("内部错误：%d 号对象没填内容" % num)
            offsets.append(len(out))
            out += b"%d 0 obj\n" % num + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n" % (len(self.objs) + 1)
        out += b"0000000000 65535 f \n"
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        trailer = b"<< /Size %d /Root %d 0 R" % (len(self.objs) + 1, root)
        if info:
            trailer += b" /Info %d 0 R" % info
        out += b"trailer\n" + trailer + b" >>\nstartxref\n%d\n%%%%EOF\n" % xref
        return bytes(out)


def build_pdf(images, page="image", orient="auto", margin=DEFAULT_MARGIN, dpi=None,
              title="", producer=None):
    """
    把 load() 出来的图块列表拼成一个 PDF（一页一张图，顺序即列表顺序）。
    """
    if not images:
        raise ImageError("一张图都没有，转什么 PDF")

    w = PdfWriter()
    catalog_id, pages_id, info_id = w.reserve(3)
    kids = []

    for img in images:
        lay = layout(img, page, orient, margin, dpi)
        ids = w.reserve(_PER_PAGE if img["smask"] else _PER_PAGE - 1)
        page_id, content_id, image_id = ids[0], ids[1], ids[2]
        smask_id = ids[3] if img["smask"] else None

        a, b, c, d, e, f = place_matrix(img, lay["sw"], lay["sh"],
                                         lay["x"], lay["y"])
        stream = ("q %.6f %.6f %.6f %.6f %.6f %.6f cm /Im0 Do Q"
                  % (a, b, c, d, e, f)).encode("ascii")
        w.put(content_id, _stream(b"/Filter /FlateDecode", zlib.compress(stream, 9)))

        if smask_id:
            w.put(smask_id, _stream(
                b"/Type /XObject /Subtype /Image /Width %d /Height %d"
                b" /ColorSpace /DeviceGray /BitsPerComponent 8"
                b" /Filter /FlateDecode" % (img["width"], img["height"]),
                img["smask"]))

        extra = [b"/Type /XObject /Subtype /Image",
                 b"/Width %d /Height %d" % (img["width"], img["height"]),
                 b"/ColorSpace " + img["colorspace"],
                 b"/BitsPerComponent %d" % img["bpc"],
                 b"/Filter " + (b"/DCTDecode" if img["jpeg"] else b"/FlateDecode")]
        if img.get("decode"):
            extra.append(b"/Decode [" + b" ".join(img["decode"]) + b"]")
        if smask_id:
            extra.append(b"/SMask %d 0 R" % smask_id)
        w.put(image_id, _stream(b" ".join(extra), img["data"]))

        w.put(page_id,
              b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f]"
              b" /Resources << /XObject << /Im0 %d 0 R >>"
              b" /ProcSet [/PDF /ImageC /ImageGray] >>"
              b" /Contents %d 0 R >>"
              % (pages_id, lay["page_w"], lay["page_h"], image_id, content_id))
        kids.append(page_id)

    w.put(catalog_id, b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)
    w.put(pages_id, b"<< /Type /Pages /Count %d /Kids [%s] >>"
          % (len(kids), b" ".join(b"%d 0 R" % k for k in kids)))

    bits = [b"/Producer " + _text_field(
        producer or ("%s · 纯 Python 标准库" % brand.WORKSHOP))]
    if title:
        bits.append(b"/Title " + _text_field(title))
    bits.append(b"/CreationDate " + _pdf_now())
    w.put(info_id, b"<< " + b" ".join(bits) + b" >>")
    return w.serialize(root=catalog_id, info=info_id)


# ----------------------------------------------------------------- 批量入口

def convert(paths, out_path, page="image", orient="auto", margin=DEFAULT_MARGIN,
            dpi=None, title=""):
    """
    界面调这个。逐张读，坏的一张张跳过，最后写文件。

    返回 {'ok': [...], 'failed': [(名字, 原因)], 'pages': n, 'bytes': 大小}
    """
    imgs, failed = [], []
    for p in paths:
        try:
            imgs.append(load(p))
        except ImageError as exc:
            failed.append((os.path.basename(p), str(exc)))
    if not imgs:
        raise ImageError("没有一张图能读出来：%s"
                         % (failed[0][1] if failed else "列表是空的"))

    blob = build_pdf(imgs, page=page, orient=orient, margin=margin, dpi=dpi,
                     title=title or os.path.splitext(
                         os.path.basename(out_path))[0])

    tmp = out_path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.replace(tmp, out_path)
    except (IOError, OSError):
        # 目标被占用（PDF 正开在阅读器里）：不硬抢，另存一份带时刻的
        alt = os.path.splitext(out_path)[0] + "-" + time.strftime("%H%M%S") + ".pdf"
        os.replace(tmp, alt)
        out_path = alt

    return {"ok": [i["name"] for i in imgs], "failed": failed,
            "pages": len(imgs), "bytes": os.path.getsize(out_path),
            "out": out_path}


def suggested_output(paths):
    """默认输出名：落在第一个文件所在目录，叫 <首个文件名>等N张.pdf。"""
    if not paths:
        return ""
    base = os.path.dirname(paths[0]) or "."
    stem = os.path.splitext(os.path.basename(paths[0]))[0]
    tail = "" if len(paths) < 2 else "等%d张" % len(paths)
    return os.path.join(base, "%s%s.pdf" % (stem, tail))


def describe(img):
    """
    给界面用的一行说明：多大、什么色、多少 DPI。

    load() 和 probe() 出来的字典都能吃，所以列表刷新不必把像素解一遍。
    """
    d = img.get("dpi") or DEFAULT_DPI
    kind = "JPEG" if img.get("jpeg") else "PNG"
    cs = (img.get("colorspace") or b"").decode("ascii").lstrip("/")
    bpc = img.get("bpc") or 8
    bits = "%d位" % bpc if bpc != 8 else ""
    extra = "·透明" if (img.get("smask") or img.get("has_alpha")) else ""
    rot = "" if (img.get("orientation") or 1) == 1 else "·已按EXIF转正"
    return "%s %dx%d %s%s%s · 按 %d DPI" % (
        kind, img["width"], img["height"], cs, bits, extra, round(d)) + rot
