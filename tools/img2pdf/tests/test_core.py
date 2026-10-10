# -*- coding: utf-8 -*-
"""
test_core.py —— 图片转 PDF 内核单测（纯标准库，不建窗口、不联网）

跑法：
    python tests/test_core.py
或
    python -m unittest discover -s tests

这一层最怕两件事，测试都对着它们来：
1. **像素解错**（滤波、位深、通道拆分）—— 所以拿"手工算好的字节"当基准，
   而不是拿自己的编码器生成的数据去验自己的解码器（那叫自证）
2. **PDF 结构写错**（xref 偏移、/Length、对象号）—— 所以把产物按字节解析回来，
   逐个偏移核对它真的指向 "N 0 obj"，/Length 和流实际长度对得上
"""

import os
import re
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import core  # noqa: E402


# ------------------------------------------------------------- 造测试图片

def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload +
            struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def ihdr(w, h, depth, color, interlace=0):
    return chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, interlace))


def png_bytes(w, h, depth, color, rows, filters=None, plte=None, trns=None,
              phys=None, interlace=0, extra_idat_split=False):
    """
    按"给定的像素行 + 每行滤波类型"拼一个 PNG。

    rows 是每行的采样字节（未滤波），长度必须等于 ceil(w*chan*depth/8)。
    编码器在这里只用来制造输入，判对错的基准是测试里写死的期望像素。
    """
    chan = core.PNG_COLOR[color][0]
    # 行数和行长先在这儿对齐：错了要在"造输入"这一步就炸，
    # 别留到后面让某个不相干的断言报 IndexError
    want = (w * chan * depth + 7) // 8
    if len(rows) != h:
        raise AssertionError("测试自己写的行数 %d 对不上高度 %d" % (len(rows), h))
    for y, r in enumerate(rows):
        if len(r) != want:
            raise AssertionError("第 %d 行 %d 字节，应为 %d" % (y, len(r), want))
    if filters is None:
        filters = [0] * h
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += ihdr(w, h, depth, color, interlace)
    if phys:
        out += chunk(b"pHYs", struct.pack(">IIB", phys, phys, 1))
    if plte:
        out += chunk(b"PLTE", plte)
    if trns:
        out += chunk(b"tRNS", trns)

    bpp = max(1, (chan * depth + 7) // 8)
    raw = bytearray()
    prev = bytearray(len(rows[0]))
    for y, row in enumerate(rows):
        f = filters[y]
        line = bytearray(len(row))
        for i in range(len(row)):
            a = row[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 0:
                pred = 0
            elif f == 1:
                pred = a
            elif f == 2:
                pred = b
            elif f == 3:
                pred = (a + b) >> 1
            elif f == 4:
                pred = core._paeth(a, b, c)
            else:
                raise AssertionError("测试自己写错了滤波号 %d" % f)
            line[i] = (row[i] - pred) & 0xFF
        raw.append(f)
        raw += line
        prev = bytearray(row)

    comp = zlib.compress(bytes(raw), 6)
    if extra_idat_split:
        # 规范说所有 IDAT 的载荷**拼起来**才是一个完整的 zlib 流，
        # 所以要切压缩后的字节，不能切压缩前的
        half = len(comp) // 2
        out += chunk(b"IDAT", comp[:half])
        out += chunk(b"IDAT", comp[half:])
    else:
        out += chunk(b"IDAT", comp)
    out += chunk(b"IEND", b"")
    return bytes(out)


def gray_rows(w, values):
    """一行灰度：values 是 0-255 的列表。"""
    return [bytes(values)]


def jpeg_bytes(w=120, h=80, comps=3, bpc=8, jfif_dpi=None, exif_orientation=None,
               exif_dpi=None, progressive=False, extra_tail=True):
    """
    造一个"头部完全合规"的 JPEG：SOI + 可选 JFIF + 可选 EXIF + SOF0 + SOS。

    扫描数据是假的——但这没关系：内核从不解码 JPEG 像素，它只读头然后把整段
    码流原样交给 /DCTDecode。真正"能不能显示"由 C:\\Windows\\Web\\Screen 那张
    系统自带照片那条集成用例负责（见 TestRealJpeg）。
    """
    out = bytearray(b"\xff\xd8")
    if jfif_dpi:
        payload = (b"JFIF\x00" + b"\x01\x02" + bytes([1]) +
                   struct.pack(">HH", jfif_dpi, jfif_dpi) + b"\x00\x00")
        out += b"\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload
    if exif_orientation or exif_dpi:
        body = b"Exif\x00\x00" + _exif_app1(exif_orientation, exif_dpi)
        out += b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body
    marker = b"\xff\xc2" if progressive else b"\xff\xc0"
    sof = bytes([bpc]) + struct.pack(">HH", h, w) + bytes([comps])
    # SOF0 的载荷还要带分量说明，我们只读到 comps 就停，后面随便填
    sof += b"\x00" * max(0, (3 * comps))
    out += marker + struct.pack(">H", len(sof) + 2) + sof
    sos = b"\x00" * 8
    out += b"\xff\xda" + struct.pack(">H", len(sos) + 2) + sos
    if extra_tail:
        out += b"\x01\x02\x03\xff\xd9"          # 假扫描数据 + EOI
    return bytes(out)


def _exif_app1(orientation=None, dpi=None):
    """小端 TIFF：IFD0 里按标签号升序写 XResolution(282) 和 ResolutionUnit(296)。"""
    little = b"II*\x00"
    tags = []
    if orientation:
        tags.append((0x0112, 3, 1, orientation))
    if dpi:
        tags.append((0x011A, 5, 1, None))        # 有理数放偏移里
        tags.append((0x0128, 3, 1, 2))           # 2 = 每英寸
    tags.sort(key=lambda t: t[0])

    body = bytearray(little)
    body += struct.pack("<I", 8)                 # IFD0 紧接头后
    rationals_at = 8 + 2 + len(tags) * 12 + 4
    body += struct.pack("<H", len(tags))
    extra = bytearray()
    for i, (tag, typ, cnt, val) in enumerate(tags):
        if val is None:
            off = rationals_at + len(extra)
            body += struct.pack("<HHII", tag, typ, cnt, off)
            extra += struct.pack("<II", int(dpi), 1)
        else:
            body += struct.pack("<HHII", tag, typ, cnt, val)
    body += struct.pack("<I", 0)                 # 下一个 IFD = 无
    body += extra
    return bytes(body)


def fake_img(w=100, h=50, dpi=None, orientation=1, kind="jpeg", smask=False):
    """拼一个 load() 出来那种图块字典，排版/写出用例只关心少数字段。"""
    return {"kind": kind, "width": w, "height": h,
            "colorspace": b"/DeviceRGB", "bpc": 8,
            "data": b"\xff\xd8fake\xff\xd9" if kind == "jpeg" else b"x",
            "smask": zlib.compress(b"\xff" * (w * h)) if smask else None,
            "dpi": dpi, "orientation": orientation, "jpeg": kind == "jpeg",
            "decode": None, "name": "x.jpg", "path": "x.jpg", "bytes": 10}


# ---------------------------------------------------------------- PNG 头部

class TestPngHeader(unittest.TestCase):

    def test_basic_fields(self):
        data = png_bytes(4, 3, 8, 2, [bytes(range(12))] * 3)
        info = core.read_png_header(data)
        self.assertEqual((info["width"], info["height"]), (4, 3))
        self.assertEqual((info["depth"], info["color"]), (8, 2))
        self.assertEqual(info["channels"], 3)
        self.assertFalse(info["has_alpha"])

    def test_bad_signature(self):
        with self.assertRaises(core.ImageError):
            core.read_png_header(b"PNG?no")

    def test_interlaced_rejected(self):
        data = png_bytes(2, 2, 8, 0, [b"\x00\x00", b"\x00\x00"], interlace=1)
        with self.assertRaises(core.ImageError) as ctx:
            core.read_png_header(data)
        self.assertIn("隔行", str(ctx.exception))

    def test_unknown_color_type(self):
        data = chunk_ok_color(5)
        with self.assertRaises(core.ImageError):
            core.read_png_header(data)

    def test_bad_depth_for_truecolor(self):
        data = ihdr(2, 2, 4, 2) + chunk(b"IDAT", b"") + chunk(b"IEND", b"")
        with self.assertRaises(core.ImageError):
            core.read_png_header(data)

    def test_zero_size(self):
        data = ihdr(0, 8, 8, 2) + chunk(b"IEND", b"")
        with self.assertRaises(core.ImageError):
            core.read_png_header(data)

    def test_phys_dpi(self):
        # 3937 像素/厘米 ≈ 100 DPI
        data = png_bytes(2, 1, 8, 0, [b"\x01\x02"], phys=3937)
        info = core.read_png_header(data)
        self.assertAlmostEqual(info["dpi"], 100.0, delta=0.2)

    def test_no_phys_means_none(self):
        data = png_bytes(2, 1, 8, 0, [b"\x01\x02"])
        self.assertIsNone(core.read_png_header(data)["dpi"])

    def test_truncated_chunk(self):
        """
        read_png_header 只看 IHDR 那 13 个字节，尾巴缺了它管不着（也不该管）。
        真正拦住损坏文件的是段遍历器：声明长度超出文件剩余长度就直接抛。
        """
        data = png_bytes(2, 1, 8, 0, [b"\x01\x02"])
        self.assertEqual(core.read_png_header(data[:len(data) - 6])["width"], 2)
        i = data.find(b"IDAT")
        forged = data[:i - 4] + struct.pack(">I", 99999) + data[i:]
        with self.assertRaises(core.ImageError) as ctx:
            core.png_to_pdf_image(forged)
        self.assertIn("超出文件长度", str(ctx.exception))


def chunk_ok_color(color):
    """造一个指定颜色类型的合法头部 PNG（颜色类型 5 是规范里不存在的）。"""
    body = struct.pack(">IIBBBBB", 2, 2, 8, color, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", body) + chunk(b"IEND", b"")


# ------------------------------------------------------------------ 去滤波

class TestUnfilter(unittest.TestCase):
    """
    滤波还原是整个 PNG 路径最容易错、错了又最难看出来的一段
    （错的话图会有规律地花屏）。所以这里既有"自己编码再解"的往返，
    也有**手工写死字节**的绝对基准。
    """

    ROWS = [bytes([10, 20, 30, 40, 50, 60]),        # 2 像素 RGB
            bytes([5, 250, 7, 9, 11, 13])]

    def _decode(self, filters):
        data = png_bytes(2, 2, 8, 2, self.ROWS, filters=filters)
        info = core.read_png_header(data)
        raw = zlib.decompress(b"".join(p for k, p in core._png_chunks(data)
                                       if k == b"IDAT"))
        rb, rows = core.png_scanlines(info, raw)
        self.assertEqual(rb, 6)
        return [bytes(r) for r in rows]

    def test_all_five_filters_roundtrip(self):
        for f in range(5):
            self.assertEqual(self._decode([f, f]), self.ROWS,
                             "滤波类型 %d 解回来不对" % f)

    def test_mixed_filters_per_row(self):
        # 真实 PNG 就是每行挑最省的一种，必须能混用
        self.assertEqual(self._decode([0, 4]), self.ROWS)
        self.assertEqual(self._decode([3, 1]), self.ROWS)
        self.assertEqual(self._decode([2, 0]), self.ROWS)

    def test_paeth_hand_written_bytes(self):
        """
        绝对基准：手工按规范算好的 Paeth 行，不经过本文件的编码器。

        像素 RGB=(10,20,30)(40,50,60) 上一行 (1,1,1)(2,2,2)，
        Paeth 预测逐个是 1,1,1,10,20,30 → 滤波后字节 = 原值 - 预测。
        """
        prev = bytearray([1, 1, 1, 2, 2, 2])
        row = bytearray([10, 20, 30, 40, 50, 60])
        exp = bytearray()
        for i in range(6):
            a = row[i - 3] if i >= 3 else 0
            c = prev[i - 3] if i >= 3 else 0
            exp.append((row[i] - _paeth_ref(a, prev[i], c)) & 0xFF)
        raw = b"\x02" + bytes(prev) + b"\x04" + bytes(exp)
        info = {"width": 2, "height": 2, "depth": 8, "channels": 3}
        _, rows = core.png_scanlines(info, raw)
        self.assertEqual([bytes(r) for r in rows], [bytes(prev), bytes(row)])

    def test_unknown_filter_type(self):
        raw = b"\x07" + b"\x00" * 6
        info = {"width": 2, "height": 1, "depth": 8, "channels": 3}
        with self.assertRaises(core.ImageError) as ctx:
            core.png_scanlines(info, raw)
        self.assertIn("滤波类型 7", str(ctx.exception))

    def test_short_data_reports_sizes(self):
        info = {"width": 10, "height": 10, "depth": 8, "channels": 3}
        with self.assertRaises(core.ImageError) as ctx:
            core.png_scanlines(info, b"\x00" * 10)
        msg = str(ctx.exception)
        self.assertIn("该有", msg)
        self.assertIn("缺了一块", msg)

    def test_multi_idat(self):
        # 有的编码器把 IDAT 切成多段，必须拼起来再解压
        data = png_bytes(2, 2, 8, 2, self.ROWS, extra_idat_split=True)
        info = core.read_png_header(data)
        raw = zlib.decompress(b"".join(p for k, p in core._png_chunks(data)
                                       if k == b"IDAT"))
        _, rows = core.png_scanlines(info, raw)
        self.assertEqual([bytes(r) for r in rows], self.ROWS)


def _paeth_ref(a, b, c):
    """测试自己实现一份预测器，和 core 里那份互为对照（写错一处就红）。"""
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


# ---------------------------------------------------------------- 像素拆合

class TestPngToPdf(unittest.TestCase):

    def test_rgb_passthrough(self):
        rows = [bytes([1, 2, 3, 4, 5, 6]), bytes([7, 8, 9, 10, 11, 12])]
        img = core.png_to_pdf_image(png_bytes(2, 2, 8, 2, rows))
        self.assertEqual(img["colorspace"], b"/DeviceRGB")
        self.assertEqual(img["bpc"], 8)
        self.assertEqual(zlib.decompress(img["data"]), b"".join(rows))
        self.assertIsNone(img["smask"])
        self.assertEqual((img["width"], img["height"]), (2, 2))

    def test_gray8(self):
        rows = [bytes([9, 8]), bytes([7, 6])]
        img = core.png_to_pdf_image(png_bytes(2, 2, 8, 0, rows))
        self.assertEqual(img["colorspace"], b"/DeviceGray")
        self.assertEqual(zlib.decompress(img["data"]), b"\x09\x08\x07\x06")

    def test_gray_low_bits_keeps_bpc(self):
        # 4 位灰度：2 像素一行 = 1 字节，PDF 原生支持，不该摊成 8 位
        rows = [bytes([0x3A]), bytes([0x7F])]
        img = core.png_to_pdf_image(png_bytes(2, 2, 4, 0, rows))
        self.assertEqual(img["bpc"], 4)
        self.assertEqual(img["colorspace"], b"/DeviceGray")
        self.assertEqual(zlib.decompress(img["data"]), b"\x3a\x7f")

    def test_rgb16_downconverts_to_high_byte(self):
        # 16 位：每行 6 个采样 = 12 字节，取高字节
        rows = [bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC,
                       0xDE, 0xAD, 0xBE, 0xEF, 0x01, 0x02])]
        img = core.png_to_pdf_image(png_bytes(2, 1, 16, 2, rows))
        self.assertEqual(img["bpc"], 8)
        self.assertEqual(zlib.decompress(img["data"]),
                         bytes([0x12, 0x56, 0x9A, 0xDE, 0xBE, 0x01]))

    def test_rgba_splits_smask(self):
        rows = [bytes([10, 20, 30, 200, 40, 50, 60, 0])]
        img = core.png_to_pdf_image(png_bytes(2, 1, 8, 6, rows))
        self.assertEqual(img["colorspace"], b"/DeviceRGB")
        self.assertEqual(zlib.decompress(img["data"]),
                         bytes([10, 20, 30, 40, 50, 60]))
        self.assertEqual(zlib.decompress(img["smask"]), bytes([200, 0]))

    def test_rgba_fully_opaque_drops_smask(self):
        rows = [bytes([1, 2, 3, 255, 4, 5, 6, 255])]
        img = core.png_to_pdf_image(png_bytes(2, 1, 8, 6, rows))
        self.assertIsNone(img["smask"], "alpha 全是 255 还写掩膜 = 白涨体积")

    def test_gray_alpha(self):
        rows = [bytes([11, 220, 33, 44])]
        img = core.png_to_pdf_image(png_bytes(2, 1, 8, 4, rows))
        self.assertEqual(img["colorspace"], b"/DeviceGray")
        self.assertEqual(zlib.decompress(img["data"]), bytes([11, 33]))
        self.assertEqual(zlib.decompress(img["smask"]), bytes([220, 44]))

    def test_palette_to_rgb(self):
        plte = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
        rows = [bytes([0, 1, 2])]
        img = core.png_to_pdf_image(png_bytes(3, 1, 8, 3, rows, plte=plte))
        self.assertEqual(img["colorspace"], b"/DeviceRGB")
        self.assertEqual(zlib.decompress(img["data"]),
                         bytes([255, 0, 0, 0, 255, 0, 0, 0, 255]))
        self.assertIsNone(img["smask"])

    def test_palette_transparency(self):
        plte = bytes([1, 2, 3, 4, 5, 6])
        rows = [bytes([0, 1, 0])]
        img = core.png_to_pdf_image(png_bytes(3, 1, 8, 3, rows, plte=plte,
                                              trns=bytes([0, 128])))
        self.assertEqual(zlib.decompress(img["smask"]), bytes([0, 128, 0]))

    def test_palette_4bit(self):
        plte = bytes([9, 9, 9, 8, 8, 8])
        rows = [bytes([0x10])]                    # 两个索引：1 和 0
        img = core.png_to_pdf_image(png_bytes(2, 1, 4, 3, rows, plte=plte))
        self.assertEqual(zlib.decompress(img["data"]),
                         bytes([8, 8, 8, 9, 9, 9]))

    def test_palette_missing_plte(self):
        with self.assertRaises(core.ImageError) as ctx:
            core.png_to_pdf_image(png_bytes(2, 1, 8, 3, [bytes([0, 1])]))
        self.assertIn("PLTE", str(ctx.exception))

    def test_palette_index_out_of_range(self):
        plte = bytes([1, 2, 3])
        with self.assertRaises(core.ImageError):
            core.png_to_pdf_image(png_bytes(2, 1, 8, 3, [bytes([0, 7])],
                                            plte=plte))

    def test_corrupt_idat(self):
        """
        把压缩载荷本身改花——zlib 会抛它自己的错，内核得把它翻译成人话，
        不能让用户看见 traceback。
        """
        good = png_bytes(4, 4, 8, 0, [bytes(range(4))] * 4)
        i = good.find(b"IDAT") + 8            # 跳过 长度 + "IDAT"
        bad = bytearray(good)
        for k in range(i, i + 10):
            bad[k] ^= 0xA5
        with self.assertRaises(core.ImageError) as ctx:
            core.png_to_pdf_image(bytes(bad))
        self.assertIn("解压", str(ctx.exception))

    def test_short_decompressed_data(self):
        """解压成功但行数不够：也是损坏，报的是"缺了一块"而不是 IndexError。"""
        data = (b"\x89PNG\r\n\x1a\n" + ihdr(4, 4, 8, 0) +
                chunk(b"IDAT", zlib.compress(b"\x00\x01\x02")) +
                chunk(b"IEND", b""))
        with self.assertRaises(core.ImageError) as ctx:
            core.png_to_pdf_image(data)
        self.assertIn("缺了一块", str(ctx.exception))

    def test_no_idat(self):
        data = b"\x89PNG\r\n\x1a\n" + ihdr(2, 2, 8, 0) + chunk(b"IEND", b"")
        with self.assertRaises(core.ImageError):
            core.png_to_pdf_image(data)


# -------------------------------------------------------------------- JPEG

class TestJpeg(unittest.TestCase):

    def test_dims_and_comps(self):
        m = core.read_jpeg(jpeg_bytes(w=640, h=480, comps=3))
        self.assertEqual((m["width"], m["height"], m["comps"]), (640, 480, 3))
        self.assertEqual(m["orientation"], 1)
        self.assertFalse(m["progressive"])

    def test_progressive_flag(self):
        m = core.read_jpeg(jpeg_bytes(progressive=True))
        self.assertTrue(m["progressive"])

    def test_jfif_dpi(self):
        m = core.read_jpeg(jpeg_bytes(jfif_dpi=300))
        self.assertEqual(m["dpi"], 300.0)

    def test_jfif_1x1_is_not_a_dpi(self):
        # 很多软件写 1x1，等于没说；当成 None 才不会把页面算成天文数字
        m = core.read_jpeg(jpeg_bytes(jfif_dpi=1))
        self.assertIsNone(m["dpi"])

    def test_exif_orientation(self):
        for o in (1, 3, 6, 8):
            m = core.read_jpeg(jpeg_bytes(exif_orientation=o))
            self.assertEqual(m["orientation"], o)

    def test_exif_dpi_when_unit_comes_after(self):
        """
        EXIF 里 282(XResolution) 排在 296(ResolutionUnit) 前面，
        所以"按英寸还是厘米"要等读完才知道。这里专门钉住那个顺序问题。
        """
        m = core.read_jpeg(jpeg_bytes(exif_dpi=300))
        self.assertEqual(m["dpi"], 300.0)

    def test_exif_and_jfif_prefer_jfif(self):
        m = core.read_jpeg(jpeg_bytes(jfif_dpi=96, exif_dpi=300))
        self.assertEqual(m["dpi"], 96.0)

    def test_gray_and_cmyk(self):
        self.assertEqual(core.read_jpeg(jpeg_bytes(comps=1))["comps"], 1)
        self.assertEqual(core.read_jpeg(jpeg_bytes(comps=4))["comps"], 4)

    def test_bad_components(self):
        with self.assertRaises(core.ImageError) as ctx:
            core.read_jpeg(jpeg_bytes(comps=2))
        self.assertIn("分量数", str(ctx.exception))

    def test_12bit_rejected(self):
        with self.assertRaises(core.ImageError):
            core.read_jpeg(jpeg_bytes(bpc=12))

    def test_not_jpeg(self):
        with self.assertRaises(core.ImageError) as ctx:
            core.read_jpeg(b"HELLO WORLD")
        self.assertIn("开头缺 FFD8", str(ctx.exception))

    def test_no_sof(self):
        with self.assertRaises(core.ImageError):
            core.read_jpeg(b"\xff\xd8\xff\xda\x00\x02" + b"\x00" * 4)

    def test_lying_segment_length_is_rejected(self):
        """段长字段吹牛（超出文件尾）必须报错，不然一路读到隔壁对象里去。"""
        data = bytearray(jpeg_bytes())
        i = data.find(b"\xff\xc0")
        struct.pack_into(">H", data, i + 2, 60000)
        with self.assertRaises(core.ImageError) as ctx:
            core.read_jpeg(bytes(data))
        self.assertIn("超出文件范围", str(ctx.exception))

    def test_truncated_scan_still_reads_size(self):
        """
        截到扫描数据中间不算错：尺寸在 SOF 里，头读完就收工。
        "下到一半的 jpg"也能报出宽高，用户才有的选——这是有意的宽松。
        """
        data = jpeg_bytes(w=320, h=200)
        cut = data.find(b"\xff\xda") + 6
        m = core.read_jpeg(data[:cut])
        self.assertEqual((m["width"], m["height"]), (320, 200))

    def test_padding_and_junk_before_marker(self):
        # 有些相机在 SOI 后塞 0 字节填充，解析器得跳过而不是当损坏
        data = b"\xff\xd8\x00" + jpeg_bytes()[2:]
        m = core.read_jpeg(data)
        self.assertTrue(m["width"])

    def test_jpeg_data_is_not_decoded(self):
        """/DCTDecode 的要点：码流一个字节都不许动。"""
        blob = jpeg_bytes(w=10, h=10)
        img = core.jpeg_to_pdf_image(blob)
        self.assertIs(img["data"], blob)
        self.assertEqual(img["colorspace"], b"/DeviceRGB")

    def test_cmyk_gets_decode_array(self):
        img = core.jpeg_to_pdf_image(jpeg_bytes(comps=4))
        self.assertEqual(img["colorspace"], b"/DeviceCMYK")
        self.assertEqual(img["decode"], [b"1 0"] * 4)

    def test_gray_jpeg(self):
        img = core.jpeg_to_pdf_image(jpeg_bytes(comps=1))
        self.assertEqual(img["colorspace"], b"/DeviceGray")
        self.assertIsNone(img["decode"])


# ------------------------------------------------------------------ 排版

class TestLayout(unittest.TestCase):

    def test_page_points_uses_image_dpi(self):
        img = fake_img(w=200, h=100, dpi=200)
        w, h = core.page_points(img)
        self.assertAlmostEqual(w, 72.0)          # 200 px @200dpi = 1 英寸
        self.assertAlmostEqual(h, 36.0)

    def test_page_points_default_and_override(self):
        img = fake_img(w=core.DEFAULT_DPI, h=10)
        self.assertAlmostEqual(core.page_points(img)[0], 72.0)
        self.assertAlmostEqual(core.page_points(img, dpi=72)[0], 200.0)
        self.assertAlmostEqual(core.page_points(img, dpi=72)[1], 10.0)
        self.assertAlmostEqual(core.page_points(img, dpi=144)[0], 100.0)

    def test_display_size_all_orientations(self):
        wide = fake_img(w=200, h=100)
        for o in (1, 2, 3, 4):
            self.assertEqual(core.display_size(wide, 200, 100), (200, 100),
                             "方向 %d 不该换长宽" % o)
        wide["orientation"] = 5
        self.assertEqual(core.display_size(wide, 200, 100), (100, 200))
        wide["orientation"] = 6
        self.assertEqual(core.display_size(wide, 200, 100), (100, 200))
        wide["orientation"] = 7
        self.assertEqual(core.display_size(wide, 200, 100), (100, 200))
        wide["orientation"] = 8
        self.assertEqual(core.display_size(wide, 200, 100), (100, 200))

    def test_place_matrix_covers_box_for_every_orientation(self):
        """
        八种方向都要求：四个角落点的包围盒**正好**落在 (x0,y0) 起的
        显示尺寸上。符号写反、偏移漏一项，这里立刻红。
        """
        sw, sh, x0, y0 = 200.0, 100.0, 37.0, 51.0
        for o in range(1, 9):
            img = fake_img(w=200, h=100, orientation=o)
            a, b, c, d, e, f = core.place_matrix(img, sw, sh, x0, y0)
            xs = [e, a + e, c + e, a + c + e]
            ys = [f, b + f, d + f, b + d + f]
            dw, dh = core.display_size(img, sw, sh)
            self.assertAlmostEqual(min(xs), x0, places=6,
                                   msg="方向 %d 左边没对齐" % o)
            self.assertAlmostEqual(max(xs) - min(xs), dw, places=6,
                                   msg="方向 %d 宽度不对" % o)
            self.assertAlmostEqual(min(ys), y0, places=6,
                                   msg="方向 %d 下边没对齐" % o)
            self.assertAlmostEqual(max(ys) - min(ys), dh, places=6,
                                   msg="方向 %d 高度不对" % o)

    def test_orientation_6_is_90_clockwise(self):
        """手机竖拍存成横图，转正 90° 顺时针：u 轴该指向下方。"""
        img = fake_img(w=200, h=100, orientation=6)
        a, b, c, d, _, _ = core.place_matrix(img, 200.0, 100.0, 0.0, 0.0)
        self.assertEqual((a, b), (0.0, -200.0))   # u（图片左→右）转向下
        self.assertEqual((c, d), (100.0, 0.0))    # v（图片下→上）转向右

    def test_image_mode_page_equals_picture_plus_margin(self):
        img = fake_img(w=300, h=150, dpi=72)       # 300x150 磅
        lay = core.layout(img, page="image", margin=10)
        self.assertAlmostEqual(lay["page_w"], 320.0)
        self.assertAlmostEqual(lay["page_h"], 170.0)
        self.assertAlmostEqual(lay["x"], 10.0)
        self.assertAlmostEqual(lay["y"], 10.0)

    def test_image_mode_rotated_page_swaps(self):
        img = fake_img(w=300, h=150, dpi=72, orientation=6)
        lay = core.layout(img, page="image", margin=0)
        self.assertAlmostEqual(lay["page_w"], 150.0)
        self.assertAlmostEqual(lay["page_h"], 300.0)

    def test_a4_fit_never_upsamples_small_image(self):
        img = fake_img(w=50, h=50, dpi=300)        # 12x12 磅的小图
        lay = core.layout(img, page="A4", margin=28)
        self.assertLessEqual(lay["disp_w"], 595.28 - 56)
        self.assertAlmostEqual(lay["disp_w"], 12.0, places=2,
                               msg="小图不该被放大糊成一团")

    def test_a4_fit_landscape_is_width_bound(self):
        """960x720 磅的横图进竖 A4：撞的是宽度，等比缩完居中，四周留白。"""
        img = fake_img(w=4000, h=3000, dpi=300)    # 960x720 磅
        lay = core.layout(img, page="A4", orient="portrait", margin=28)
        iw, ih = 595.28 - 56, 841.89 - 56
        self.assertAlmostEqual(lay["disp_w"], iw, places=1)
        self.assertAlmostEqual(lay["disp_h"], 720 * (iw / 960.0), places=1)
        self.assertLessEqual(lay["disp_h"], ih + 0.01)
        self.assertAlmostEqual(lay["x"], (595.28 - lay["disp_w"]) / 2.0, places=1)
        self.assertAlmostEqual(lay["y"], (841.89 - lay["disp_h"]) / 2.0, places=1)

    def test_a4_fit_tall_image_is_height_bound(self):
        """细长竖图反过来撞高度：截图长图最常见，别让它顶掉留边。"""
        img = fake_img(w=1200, h=6000, dpi=300)    # 288x1440 磅
        lay = core.layout(img, page="A4", orient="portrait", margin=28)
        iw, ih = 595.28 - 56, 841.89 - 56
        self.assertAlmostEqual(lay["disp_h"], ih, places=1)
        self.assertLessEqual(lay["disp_w"], iw + 0.01)
        self.assertAlmostEqual(lay["x"], (595.28 - lay["disp_w"]) / 2.0, places=1)

    def test_auto_orient_picks_landscape_for_wide(self):
        img = fake_img(w=4000, h=1000, dpi=300)
        lay = core.layout(img, page="A4", orient="auto", margin=28)
        self.assertGreater(lay["page_w"], lay["page_h"])

    def test_auto_orient_respects_exif(self):
        """图本身是横着存的、但 EXIF 说该竖着看 → 版心该选竖的。"""
        img = fake_img(w=4000, h=1000, dpi=300, orientation=6)
        lay = core.layout(img, page="A4", orient="auto", margin=28)
        self.assertLess(lay["page_w"], lay["page_h"])

    def test_margin_clamped_inside_paper(self):
        img = fake_img(w=800, h=600, dpi=100)
        lay = core.layout(img, page="A5", margin=9999)
        self.assertLess(lay["disp_w"], core.PAPER["A5"][0])
        self.assertGreater(lay["disp_w"], 0)

    def test_unknown_paper_rejected(self):
        with self.assertRaises(core.ImageError):
            core.layout(fake_img(), page="B5")


# -------------------------------------------------------------- PDF 结构

def find_objects(blob):
    """按字节找出所有 "N 0 obj" 的位置，用来核对 xref。"""
    out = {}
    pos = 0
    while True:
        i = blob.find(b" 0 obj\n", pos)
        if i < 0:
            break
        j = blob.rfind(b"\n", 0, i)
        head = blob[j + 1:i]
        if head.split(b" ")[0].isdigit():
            out[int(head.split(b" ")[0])] = j + 1
        pos = i + 1
    return out


def xref_table(blob):
    # 注意找 "\nxref\n"：startxref 里也含 "xref\n"，直接 rfind 会命中那里
    i = blob.rfind(b"\nxref\n")
    assert i > 0, "没找到 xref 段"
    lines = blob[i + len(b"\nxref\n"):blob.find(b"trailer", i)].split(b"\n")
    assert lines[0].startswith(b"0 "), "xref subsection 头不对: %r" % lines[0]
    total = int(lines[0].split(b" ")[1])
    rows = []
    for ln in lines[1:total + 1]:
        parts = ln.split(b" ")
        rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def object_bodies(blob):
    """把每个对象的正文（"N 0 obj" 到 "endobj" 之间）取出来，方便断言内容。"""
    out = {}
    for num, off in find_objects(blob).items():
        end = blob.find(b"\nendobj", off)
        body = blob[blob.index(b"\n", off) + 1:end]
        out[num] = body
    return out


def find_page_object(bodies):
    """页对象长这样：/Type /Page 且带 /Contents（别和 /Type /Pages 混了）。"""
    hits = [n for n, b in bodies.items()
            if b"/Type /Page " in b and b"/Contents" in b]
    assert len(hits) == 1, "期望正好一页，实际 %s" % hits
    return hits[0]


def parse_dict_stream(blob, start):
    """从 "N 0 obj" 位置解析出 (字典文本, 流字节)。非流对象返回 (dict, None)。"""
    body = blob[start:]
    end = body.find(b"endobj")
    seg = body[:end]
    if b"stream\n" not in seg:
        return seg.strip(), None
    head, rest = seg.split(b"<<", 1)[1].split(b">>", 1)
    dict_text = b"<<" + head + b">>"
    # 字典和 stream 关键字之间可能有换行；末尾流长度以 /Length 为准
    marker = rest.find(b"stream")
    data_start = marker + len(b"stream")
    if rest[data_start:data_start + 1] == b"\r":
        data_start += 1
    if rest[data_start:data_start + 1] == b"\n":
        data_start += 1
    data = rest[data_start:]
    if data.endswith(b"\n"):
        data = data[:-1]
    return dict_text, data


class TestPdf(unittest.TestCase):

    def one(self, imgs, **kw):
        return core.build_pdf(imgs, **kw)

    def test_xref_offsets_are_real(self):
        blob = self.one([fake_img(), fake_img(w=80, h=80)])
        objs = find_objects(blob)
        rows = xref_table(blob)
        self.assertEqual(len(rows) - 1, len(objs), "xref 条数和对象数不一致")
        for num, (off, gen, kind) in enumerate(rows[1:], start=1):
            self.assertEqual(kind, b"n")
            self.assertEqual(off, objs[num], "%d 号对象的 xref 偏移指错了" % num)
            self.assertEqual(blob[off:off + 12].split(b" ")[0],
                             str(num).encode("ascii"))

    def test_header_and_tail(self):
        blob = self.one([fake_img()])
        self.assertTrue(blob.startswith(b"%PDF-1."))
        self.assertTrue(blob.rstrip().endswith(b"%%EOF"))
        startx = int(blob[blob.rfind(b"startxref") + 9:].split(b"\n")[1])
        self.assertEqual(blob[startx:startx + 4], b"xref")

    def test_object_graph_resolves(self):
        """trailer → Catalog → Pages → 每一页 → 内容流/图片，引用链一环都不能断。"""
        blob = self.one([fake_img(), fake_img(w=60, h=60)])
        bodies = object_bodies(blob)
        trailer = blob[blob.rfind(b"trailer"):]
        catalog_num = int(trailer.split(b"/Root ")[1].split(b" ")[0])
        self.assertIn(b"/Type /Catalog", bodies[catalog_num])
        pages_num = int(bodies[catalog_num].split(b"/Pages ")[1].split(b" ")[0])
        pages = bodies[pages_num]
        self.assertEqual(int(pages.split(b"/Count ")[1].split(b" ")[0]), 2)
        kids = pages.split(b"/Kids [")[1].split(b"]")[0]
        nums = [int(x) for x in re.findall(rb"(\d+) 0 R", kids)]
        self.assertEqual(len(nums), 2)
        for num in nums:
            self.assertIn(b"/Type /Page ", bodies[num])
            self.assertIn(b"/Parent %d 0 R" % pages_num, bodies[num])
            cid = int(bodies[num].split(b"/Contents ")[1].split(b" ")[0])
            self.assertIn(b"/Length", bodies[cid])
            iid = int(bodies[num].split(b"/Im0 ")[1].split(b" ")[0])
            self.assertIn(b"/Subtype /Image", bodies[iid])
        info_num = int(trailer.split(b"/Info ")[1].split(b" ")[0])
        self.assertIn(b"/Producer", bodies[info_num])

    def test_every_stream_length_matches(self):
        """
        /Length 写错是"PDF 打得开但图片全糊"的头号原因。
        逐个流按声明长度截出来，确认它后面紧跟 endstream（中间允许一个换行，
        规范说 endstream 前那个 EOL 不计入长度）。

        找的是 "\nstream\n" 不是 "stream\n"——后者会命中 "endstream"，
        然后拿上一段的字典去解下一段，测试自己先乱。
        """
        blob = self.one([fake_img(), fake_img(w=50, h=40, smask=True)])
        pos = 0
        checked = 0
        while True:
            i = blob.find(b"\nstream\n", pos)
            if i < 0:
                break
            head_start = blob.rfind(b"<<", 0, i)
            head = blob[head_start:i]
            declared = int(head.split(b"/Length ")[1].split(b" >>")[0])
            body = blob[i + len(b"\nstream\n"):]
            tail = body[declared:declared + 10]
            self.assertTrue(tail.startswith(b"\nendstream") or
                            tail.startswith(b"endstream"),
                            "/Length=%d 后面接的不是 endstream：%r" % (declared, tail))
            checked += 1
            pos = i + len(b"\nstream\n") + declared
        self.assertGreaterEqual(checked, 4, "用例太少，没覆盖内容流/图/掩膜")

    def test_jpeg_is_embedded_byte_for_byte(self):
        blob_in = jpeg_bytes(w=64, h=32)
        img = core.jpeg_to_pdf_image(blob_in)
        pdf = self.one([img])
        self.assertGreater(pdf.find(blob_in), 0, "JPEG 码流没被原样放进去")
        self.assertIn(b"/Filter /DCTDecode", pdf)
        bodies = object_bodies(pdf)
        iid = [n for n, b in bodies.items() if b"/Subtype /Image" in b][0]
        self.assertNotIn(b"/FlateDecode", bodies[iid],
                        "JPEG 不该被再压一道——那是重编码，不是无损")

    def test_png_embeds_flatedeflate_rgb(self):
        rows = [bytes([1, 2, 3, 4, 5, 6])]
        img = core.png_to_pdf_image(png_bytes(2, 1, 8, 2, rows))
        pdf = self.one([img])
        self.assertIn(b"/Filter /FlateDecode", pdf)
        self.assertIn(b"/ColorSpace /DeviceRGB", pdf)
        self.assertIn(b"/Width 2 /Height 1", pdf)

    def test_smask_only_when_alpha(self):
        plain = core.png_to_pdf_image(png_bytes(2, 1, 8, 2,
                                                [bytes([1, 2, 3, 4, 5, 6])]))
        alpha = core.png_to_pdf_image(png_bytes(2, 1, 8, 6,
                                                [bytes([1, 2, 3, 9, 4, 5, 6, 200])]))
        self.assertNotIn(b"/SMask", self.one([plain]))
        self.assertIn(b"/SMask", self.one([alpha]))
        self.assertIn(b"/ColorSpace /DeviceGray", self.one([alpha]))

    def test_cmyk_decode_written(self):
        img = core.jpeg_to_pdf_image(jpeg_bytes(comps=4))
        pdf = self.one([img])
        self.assertIn(b"/ColorSpace /DeviceCMYK", pdf)
        self.assertIn(b"/Decode [1 0 1 0 1 0 1 0]", pdf)

    def test_media_box_follows_layout(self):
        img = fake_img(w=200, h=100, dpi=72)
        pdf = self.one([img], page="image", margin=0)
        self.assertIn(b"/MediaBox [0 0 200.00 100.00]", pdf)

    def test_content_stream_matrix_matches_layout(self):
        img = fake_img(w=200, h=100, dpi=72, orientation=6)
        lay = core.layout(img, page="image", margin=0)
        pdf = self.one([img], page="image", margin=0)
        bodies = object_bodies(pdf)
        page_num = find_page_object(bodies)
        cid = int(bodies[page_num].split(b"/Contents ")[1].split(b" ")[0])
        _, data = parse_dict_stream(pdf, find_objects(pdf)[cid])
        stream = zlib.decompress(data).decode("ascii")
        self.assertTrue(stream.startswith("q "), stream)
        nums = [float(x) for x in stream[2:stream.index(" cm")].split()]
        expect = core.place_matrix(img, lay["sw"], lay["sh"], lay["x"], lay["y"])
        self.assertEqual(nums, [round(v, 6) for v in expect])
        self.assertIn("/Im0 Do Q", stream)

    def test_title_and_producer_encoding(self):
        img = fake_img()
        # 默认 producer 是"允澈工坊 · 纯 Python 标准库"，中文 → 该走十六进制串
        pdf = self.one([img], title="报销单 2026-10")
        self.assertIn(b"/Title <", pdf, "中文标题该走 UTF-16 十六进制串")
        self.assertIn(b"/Producer <", pdf, "中文产商也该走十六进制串")
        # 显式给个纯 ASCII 的产商，才走字面串那条分支
        ascii_pdf = self.one([img], producer="img2pdf 0.1.0")
        self.assertIn(b"/Producer (img2pdf 0.1.0)", ascii_pdf)
        bodies = object_bodies(pdf)
        trailer = pdf[pdf.rfind(b"trailer"):]
        info_num = int(trailer.split(b"/Info ")[1].split(b" ")[0])
        hex_part = bodies[info_num].split(b"/Title <")[1].split(b">")[0].decode("ascii")
        raw = bytes(int(hex_part[i:i + 2], 16) for i in range(0, len(hex_part), 2))
        self.assertEqual(raw.decode("utf-16-be"), "\ufeff报销单 2026-10")

    def test_escaping_in_ascii_strings(self):
        pdf = self.one([fake_img()], title="a(b)c\\d")
        self.assertIn(b"/Title (a\\(b\\)c\\\\d)", pdf)

    def test_creation_date_has_offset(self):
        pdf = self.one([fake_img()])
        i = pdf.find(b"/CreationDate D:")
        self.assertGreater(i, 0)
        self.assertRegex(pdf[i:i + 40], rb"D:\d{14}[+-]\d{2}'\d{2}'")

    def test_empty_list_is_error(self):
        with self.assertRaises(core.ImageError):
            core.build_pdf([])

    def test_page_count_matches(self):
        pdf = self.one([fake_img() for _ in range(7)])
        self.assertIn(b"/Type /Pages /Count 7", pdf)


# ---------------------------------------------------------------- 批量入口

class TestConvert(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i2p_test_")

    def _png(self, name, w=6, h=4, color=2, depth=8):
        chan = core.PNG_COLOR[color][0]
        row_bytes = (w * chan * depth + 7) // 8
        rows = [bytes([(i + y) % 251 for i in range(row_bytes)])
                for y in range(h)]
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(png_bytes(w, h, depth, color, rows))
        return p

    def _jpeg(self, name, **kw):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(jpeg_bytes(**kw))
        return p

    def test_ok_and_failed_lists(self):
        paths = [self._png("a.png"), self._jpeg("b.jpg"),
                 os.path.join(self.dir, "c.txt")]
        with open(paths[2], "wb") as fh:
            fh.write(b"not an image at all")
        out = os.path.join(self.dir, "o.pdf")
        res = core.convert(paths, out)
        self.assertEqual(res["ok"], ["a.png", "b.jpg"])
        self.assertEqual([n for n, _ in res["failed"]], ["c.txt"])
        self.assertIn("不支持的格式", res["failed"][0][1])
        self.assertEqual(res["pages"], 2)
        self.assertTrue(os.path.exists(out))
        with open(out, "rb") as fh:
            head = fh.read(8)
        self.assertEqual(head[:5], b"%PDF-")
        self.assertGreater(res["bytes"], 300)

    def test_all_bad_raises_with_reason(self):
        p = os.path.join(self.dir, "x.jpg")
        with open(p, "wb") as fh:
            fh.write(b"garbage")
        with self.assertRaises(core.ImageError) as ctx:
            core.convert([p], os.path.join(self.dir, "y.pdf"))
        self.assertIn("没有一张图能读出来", str(ctx.exception))

    def test_empty_input_list(self):
        with self.assertRaises(core.ImageError):
            core.convert([], os.path.join(self.dir, "z.pdf"))

    def test_no_tmp_file_left_behind(self):
        out = os.path.join(self.dir, "clean.pdf")
        core.convert([self._png("p.png")], out)
        self.assertFalse(os.path.exists(out + ".tmp"))
        self.assertEqual(sorted(f for f in os.listdir(self.dir)
                                 if f.endswith(".tmp")), [])

    def test_overwrite_existing_is_atomic(self):
        out = os.path.join(self.dir, "twice.pdf")
        core.convert([self._png("a.png")], out)
        size1 = os.path.getsize(out)
        core.convert([self._png("a.png"), self._jpeg("b.jpg")], out)
        self.assertGreater(os.path.getsize(out), size1)
        with open(out, "rb") as fh:
            self.assertIn(b"/Count 2", fh.read())

    def test_suggested_output_names(self):
        self.assertEqual(core.suggested_output(["D:\\x\\发票.png"]),
                         "D:\\x\\发票.pdf")
        self.assertEqual(core.suggested_output(["D:\\x\\发票.png", "D:\\x\\b.png"]),
                         "D:\\x\\发票等2张.pdf")
        self.assertEqual(core.suggested_output([]), "")

    def test_describe_mentions_kind_and_size(self):
        img = core.load(self._png("d.png", w=7, h=5))
        text = core.describe(img)
        self.assertIn("PNG", text)
        self.assertIn("7x5", text)
        self.assertIn("DeviceRGB", text)

    def test_load_rejects_unknown_extension(self):
        p = os.path.join(self.dir, "notes.txt")
        with open(p, "wb") as fh:
            fh.write(b"hi")
        with self.assertRaises(core.ImageError) as ctx:
            core.load(p)
        self.assertIn("不支持的格式", str(ctx.exception))

    def test_load_rejects_empty_file(self):
        p = os.path.join(self.dir, "e.png")
        open(p, "wb").close()
        with self.assertRaises(core.ImageError) as ctx:
            core.load(p)
        self.assertIn("空的", str(ctx.exception))

    def test_load_sniffs_content_not_extension(self):
        """扩展名写 .jpg 实际是 PNG：按内容走，别当坏文件。"""
        p = os.path.join(self.dir, "lie.jpg")
        with open(p, "wb") as fh:
            fh.write(png_bytes(2, 2, 8, 0, [b"\x01\x02", b"\x03\x04"]))
        img = core.load(p)
        self.assertFalse(img["jpeg"])
        self.assertEqual(img["colorspace"], b"/DeviceGray")

    def test_pdf_from_convert_roundtrips_pixels(self):
        """
        端到端：PNG 进 → PDF 出 → 把 PDF 里的图像流解压回来，
        必须和原图的像素一模一样。这是"无损"这句文案唯一的证据。
        """
        rows = [bytes([i * 7 % 256 for i in range(18)]) for _ in range(3)]
        src = png_bytes(6, 3, 8, 2, rows)
        p = os.path.join(self.dir, "r.png")
        with open(p, "wb") as fh:
            fh.write(src)
        out = os.path.join(self.dir, "r.pdf")
        core.convert([p], out)
        with open(out, "rb") as fh:
            pdf = fh.read()
        i = pdf.find(b"/Subtype /Image")
        head_start = pdf.rfind(b"<<", 0, i)
        j = pdf.find(b"stream\n", head_start)
        body = pdf[j + 7:]
        head = pdf[head_start:j]
        declared = int(head.split(b"/Length ")[1].split(b" >>")[0])
        payload = zlib.decompress(body[:declared])
        self.assertEqual(payload, b"".join(rows))


# ------------------------------------------------------------ probe（只读头部）

class TestProbe(unittest.TestCase):
    """
    probe() 是界面"规格"那一列的数据源。它和 load() 是**两条独立算出来的路**
    （一个只看头部，一个真解像素），所以必须逐格式核对它们说的一致 ——
    否则列表写 RGB 8 位、产物里是别的东西，用户第一个骂的就是我们。
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i2p_probe_")

    def _png(self, name, *args, **kw):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(png_bytes(*args, **kw))
        return p

    def _jpeg(self, name, **kw):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(jpeg_bytes(**kw))
        return p

    def _pair(self, path):
        a, b = core.probe(path), core.load(path)
        return a, b

    def test_matches_load_on_every_png_flavour(self):
        cases = [
            ("g8.png", self._png("g8.png", 4, 2, 8, 0, [b"\x01\x02\x03\x04"] * 2)),
            ("g4.png", self._png("g4.png", 4, 2, 4, 0, [b"\x12\x34"] * 2)),
            ("g1.png", self._png("g1.png", 8, 2, 1, 0, [b"\xa5", b"\x5a"])),
            ("rgb.png", self._png("rgb.png", 2, 2, 8, 2,
                                  [b"\x01\x02\x03\x04\x05\x06"] * 2)),
            ("rgba.png", self._png("rgba.png", 2, 1, 8, 6,
                                   [b"\x01\x02\x03\x7f\x04\x05\x06\x80"])),
            ("ga.png", self._png("ga.png", 2, 1, 8, 4, [b"\x01\x02\x03\x04"])),
            ("pal.png", self._png("pal.png", 2, 1, 8, 3, [b"\x00\x01"],
                                  plte=b"\xff\x00\x00\x00\xff\x00")),
            ("palt.png", self._png("palt.png", 2, 1, 8, 3, [b"\x00\x01"],
                                   plte=b"\xff\x00\x00\x00\xff\x00",
                                   trns=b"\x00\xff")),
            ("16bit.png", self._png("16bit.png", 2, 1, 16, 2,
                                    [b"\x00\x01\x00\x02\x00\x03"
                                     b"\x04\x05\x06\x07\x08\x09"])),
            ("dpi.png", self._png("dpi.png", 2, 1, 8, 0, [b"\x01\x02"],
                                  phys=3937)),      # 3937 像素/米 ≈ 100 DPI
        ]
        for label, path in cases:
            a, b = self._pair(path)
            for key in ("width", "height", "colorspace", "bpc", "dpi",
                        "jpeg", "orientation", "name", "bytes"):
                self.assertEqual(a[key], b[key],
                                 "%s 的 %s 两边不一致：%r vs %r"
                                 % (label, key, a[key], b[key]))

    def test_matches_load_on_jpeg(self):
        p = self._jpeg("o.jpg", w=640, h=480, jfif_dpi=300, exif_orientation=6)
        a, b = self._pair(p)
        self.assertEqual(a["width"], b["width"])
        self.assertEqual(a["orientation"], 6, "EXIF 方向头部就能读到")
        self.assertEqual(a["colorspace"], b["colorspace"])
        self.assertEqual(a["dpi"], b["dpi"])
        self.assertTrue(a["jpeg"])

    def test_cmyk_jpeg_matches(self):
        a, b = self._pair(self._jpeg("c.jpg", comps=4))
        self.assertEqual(a["colorspace"], b["colorspace"])
        self.assertEqual(a["decode"], b["decode"])

    def test_alpha_flag(self):
        """带 alpha/透明调色板的要说"透明"，纯 RGB 不能说。"""
        self.assertTrue(core.probe(self._png(
            "a6.png", 2, 1, 8, 6, [b"\x01\x02\x03\x7f\x04\x05\x06\x80"])
        )["has_alpha"])
        self.assertTrue(core.probe(self._png("a4b.png", 2, 1, 8, 4,
                                             [b"\x01\x02\x03\x04"]))["has_alpha"])
        self.assertTrue(core.probe(self._png("t.png", 2, 1, 8, 3, [b"\x00\x01"],
                                             plte=b"\xff\x00\x00\x00\xff\x00",
                                             trns=b"\x00\xff"))["has_alpha"])
        self.assertFalse(core.probe(self._png("r.png", 2, 1, 8, 2,
                                              [b"\x01\x02\x03\x04\x05\x06"])
                                    )["has_alpha"])

    def test_smask_never_means_透明_by_accident(self):
        """产物里真写了掩膜的话，头部探测那条路也必须说透明（不能说漏）。"""
        p = self._png("op.png", 2, 1, 8, 6, [b"\x01\x02\x03\xff\x04\x05\x06\xfe"])
        loaded = core.load(p)
        probed = core.probe(p)
        if loaded["smask"]:
            self.assertTrue(probed["has_alpha"])

    def test_probe_does_not_touch_pixels(self):
        """
        把解码那一步换成"一调就炸"，probe 照样出结果 —— 这才证明列表
        真的没解像素（否则 200 张截图的文件夹一点「添加文件夹」就冻住）。
        """
        p = self._png("big.png", 8, 8, 8, 2,
                      [bytes(range(24))] * 8)
        real = core.png_scanlines
        try:
            def boom(*_a, **_kw):
                raise AssertionError("probe 不许解扫描线")
            core.png_scanlines = boom
            info = core.probe(p)
        finally:
            core.png_scanlines = real
        self.assertEqual((info["width"], info["height"]), (8, 8))

    def test_describe_works_on_probe_dicts(self):
        text = core.describe(core.probe(self._png(
            "d.png", 2, 1, 8, 6, [b"\x01\x02\x03\x7f\x04\x05\x06\x80"])))
        self.assertIn("PNG 2x1", text)
        self.assertIn("DeviceRGB", text)
        self.assertIn("透明", text)
        self.assertNotIn("None", text)

    def test_probe_rejects_the_same_junk_as_load(self):
        txt = os.path.join(self.dir, "a.txt")
        with open(txt, "wb") as fh:
            fh.write(b"hi")
        for bad in (txt, self._png("empty.png", 1, 1, 8, 0, [b"\x00"])[:4]):
            with self.assertRaises(core.ImageError):
                core.probe(bad)

    def test_probe_rejects_unknown_ext_even_when_content_is_png(self):
        """
        扩展名这道门必须真的存在：光靠"内容不认识"是拦不住的。
        把 probe 里的 SUPPORTED_EXTS 判断删掉，这张合法 PNG 就会混进列表，
        用户拖进来一堆 .txt/.docx 时，界面会安静地给出错的规格。
        """
        p = os.path.join(self.dir, "note.txt")
        with open(p, "wb") as fh:
            fh.write(png_bytes(2, 1, 8, 0, [b"\x01\x02"]))
        for fn in (core.probe, core.load):
            with self.assertRaises(core.ImageError) as ctx:
                fn(p)
            self.assertIn("不支持的格式", str(ctx.exception))

    def test_probe_rejects_empty_file_with_the_same_words(self):
        """0 字节要说"空的"，不能说"内容既不是 JPEG 也不是 PNG"。"""
        p = os.path.join(self.dir, "e.png")
        open(p, "wb").close()
        for fn in (core.probe, core.load):
            with self.assertRaises(core.ImageError) as ctx:
                fn(p)
            self.assertIn("空的", str(ctx.exception))

    def test_probe_on_lying_extension(self):
        """扩展名写 .jpg 其实是 PNG：按内容走，和 load() 同一个口径。"""
        p = os.path.join(self.dir, "lie.jpg")
        with open(p, "wb") as fh:
            fh.write(png_bytes(2, 2, 8, 0, [b"\x01\x02", b"\x03\x04"]))
        self.assertFalse(core.probe(p)["jpeg"])
        self.assertEqual(core.probe(p)["colorspace"], b"/DeviceGray")


# ------------------------------------------------------------ 真 JPEG 集成

class TestRealJpeg(unittest.TestCase):
    """
    用系统自带照片验证"真 JPEG"这条路。机器上没有就跳过（不影响单测跑通），
    但这几条是我们敢写"JPEG 原样封装、无损"的根据。
    """

    CANDIDATES = [r"C:\Windows\Web\Screen\img100.jpg",
                  r"C:\Windows\Web\Lock Screen\\img100.jpg",
                  r"C:\Windows\Web\Wallpaper\Windows\img0.jpg"]

    def _path(self):
        for p in self.CANDIDATES:
            if os.path.exists(p):
                return p
        return None

    def test_real_jpeg_parses(self):
        p = self._path()
        if not p:
            self.skipTest("这台机器上没找到系统自带的 JPEG")
        meta = core.read_jpeg(core._read_bytes(p))
        self.assertGreater(meta["width"], 100)
        self.assertGreater(meta["height"], 100)
        self.assertIn(meta["comps"], (1, 3, 4))
        self.assertEqual(meta["bpc"], 8)

    def test_real_jpeg_bytes_survive(self):
        p = self._path()
        if not p:
            self.skipTest("这台机器上没找到系统自带的 JPEG")
        img = core.load(p)
        self.assertTrue(img["jpeg"])
        pdf = core.build_pdf([img])
        with open(p, "rb") as fh:
            raw = fh.read()
        self.assertGreater(raw, b"")
        self.assertIn(raw, pdf, "真 JPEG 没有原样进 PDF")
        # 产物体积应该 ≈ 原图 + 一点结构开销
        self.assertLess(len(pdf), len(raw) + 4096)

    def test_real_jpeg_convert_writes_openable_pdf(self):
        p = self._path()
        if not p:
            self.skipTest("这台机器上没找到系统自带的 JPEG")
        d = tempfile.mkdtemp(prefix="i2p_real_")
        out = os.path.join(d, "real.pdf")
        res = core.convert([p], out)
        self.assertEqual(res["pages"], 1)
        with open(out, "rb") as fh:
            blob = fh.read()
        self.assertEqual(blob[:8], b"%PDF-1.7")
        objs = find_objects(blob)
        rows = xref_table(blob)
        for num, (off, _g, kind) in enumerate(rows[1:], start=1):
            self.assertEqual(kind, b"n")
            self.assertEqual(off, objs.get(num))



class TestDocReferences(unittest.TestCase):
    """
    文档里提到的文件必须真的存在、提到的版本号必须和内核一致。

    起因：smoke_ui 的注释里写了个"像素对不对看 tests/shot.py"，而那个脚本
    压根没有。这种话留在仓库里比不写更糟——读者会照着找。
    """

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _docs(self):
        for name in ("README.md", "CHANGELOG.md"):
            p = os.path.join(self.ROOT, name)
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    yield name, fh.read()

    def test_every_script_the_docs_mention_exists(self):
        seen = 0
        for name, text in self._docs():
            for ref in set(re.findall(r"(?:src|tests|assets)/[\w.一-鿿-]+\.(?:py|ico|md|json)",
                                      text)):
                seen += 1
                self.assertTrue(
                    os.path.exists(os.path.join(self.ROOT, ref.replace("\\", "/"))),
                    "%s 里引用了不存在的文件：%s" % (name, ref))
        self.assertGreater(seen, 5, "一条都没扫到 = 正则在空跑")

    def test_readme_version_matches_the_kernel(self):
        for name, text in self._docs():
            if name != "README.md":
                continue
            self.assertIn("v" + core.VERSION, text,
                          "README 署的版本号和 core.VERSION 对不上")
            self.assertNotIn("你的用户名", text, "README 里还留着占位地址")


if __name__ == "__main__":
    unittest.main(verbosity=2)
