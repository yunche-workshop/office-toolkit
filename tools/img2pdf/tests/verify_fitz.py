# -*- coding: utf-8 -*-
"""
verify_fitz.py —— 拿真 PDF 阅读器验收我们手写的 PDF（开发期专用，不进运行时）

为什么单独一支脚本：
    仓库里那 120 多条 unittest 全是"自己写的东西自己验"——core 生成 PDF，
    测试按同一套规矩去数 xref。它能证明结构自洽，证明不了 **Adobe / MuPDF
    这些真阅读器认不认**。这一支用 PyMuPDF（独立实现）把我们写的文件解出来、
    渲染成像素，再和 PIL 解码原图得到的像素逐字节比 —— 等价于"发给用户之后
    他打开看到的就是那张图"。

    测试图全部用 **Pillow 真编码器**现场生成，不再是我们手写 IHDR/IDAT 造的
    那些"合规但理想化"的字节；顺带覆盖 RGBA/LA/调色板+tRNS/16 位/1-2-4 位/
    灰度 JPEG/4:2:0 JPEG/CMYK JPEG 这些真机会出现的形状。

判据怎么定的（都是跑出来的实测值，不是拍脑袋）：
  · 不透明的 PNG/JPEG：MuPDF 渲染 == PIL 解码，容差 1（解码器取整差）
  · 带 alpha 的：按 PDF 白底混色公式自己算预期，容差 2（混色取整差）
  · CMYK：不逐字节比（两边都没做 ICC，差 20 多个色阶正常），改比"色块色相
    对不对"——白块必须亮、黑块必须暗、红块 R 压倒 G/B；并且额外造一份
    抹掉 /Decode 的 PDF，确认那条断言会把颜色翻成暗的（证明断言有牙）
  · JPEG：用 extract_image() 取出 PDF 里的码流，和源文件字节全等 —— 这才
    是"原样封装、不重编码"的硬证据

⚠️ 许可红线：PyMuPDF 是 AGPL-3.0，Pillow 是 HPMD。**只在开发机上跑这一支**，
   绝不进 src、绝不打进 exe（build.py 的 --exclude-module 里要有它们）。

跑法（系统 Python 3.12 装了这两个库；tk38 那个 venv 没装，会在开头 SKIP）：
    PYTHONUTF8=1 python tests/verify_fitz.py
"""

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)

try:
    import fitz                      # PyMuPDF：独立 PDF 阅读器实现
    from PIL import Image
except Exception as exc:             # 没装就直接退出，别把"没验"报成"验过了"
    print("SKIP：这台机器没有 PyMuPDF / Pillow（%s）" % exc)
    print("      这一支是开发期验收件，缺了不影响 unittest 那 124 条。")
    sys.exit(0)

import core                           # noqa: E402
from test_core import png_bytes       # noqa: E402（2/4 位 PNG 只能自己编）

ok = 0
bad = []


def assert_ihdr(path, depth, color):
    """
    夹具自检：声称是 2 bit 的样本，IHDR 里必须真是 2 bit。

    没有这一句，"低位深透传"那几条断言会在 8 bit 样本上空跑（Pillow 就是这么
    悄悄把 bits=2 写成 8 的），而 MuPDF 那边照样全绿 —— 典型的假通过。
    """
    import struct
    with open(path, "rb") as fh:
        data = fh.read(26)
    # 8 字节签名 + 4 字节长度 + "IHDR"，之后才是 width/height/depth/color
    w, h, d, c = struct.unpack(">IIBB", data[16:26])
    if d != depth or c != color:
        raise AssertionError("夹具没编对：%s 想要 depth=%d color=%d，"
                             "实际 %dx%d depth=%d color=%d"
                             % (os.path.basename(path), depth, color,
                                w, h, d, c))


def check(name, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print("  ok   %s" % name)
    else:
        bad.append(name)
        print("  FAIL %s %s" % (name, extra))


def render(path, index=0):
    """按 72 DPI 渲染：1pt = 1px，于是能和原图一一对上。"""
    doc = fitz.open(path)
    try:
        page = doc[index]
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        return doc.page_count, page.rect, pix
    finally:
        doc.close()


def stats(a, b, stride=1):
    """
    两段像素的平均/最大逐字节差；长度不同返回 (None, None)。

    stride > 1 时按步长抽样：4K 壁纸有 2700 万字节，纯 Python 逐字节全比
    要跑几十秒，抽样几万个点足够说明问题。
    """
    if len(a) != len(b):
        return None, None
    total = peak = count = 0
    for i in range(0, len(a), stride):
        d = abs(a[i] - b[i])
        total += d
        count += 1
        if d > peak:
            peak = d
    return total / float(count or 1), peak


def over_white(img):
    """
    PDF 页面是白底：软掩膜渲染出来 = 前景按 alpha 混到白上。
    自己算一遍预期，才能反证"alpha 拆成 /SMask 时没写反（0/255 搞倒）"。
    """
    rgba = img.convert("RGBA")
    d = rgba.tobytes()
    out = bytearray()
    for i in range(0, len(d), 4):
        a = d[i + 3]
        for k in range(3):
            out.append((d[i + k] * a + 255 * (255 - a)) // 255)
    return bytes(out)


def rgb_of(path):
    """PIL 解码原图 → 每像素 3 字节（独立解码器给的"标准答案"）。"""
    with Image.open(path) as im:
        return im.convert("RGB")


def one(name, path, expect, tol, label=None):
    """一张图：转 PDF → MuPDF 打开 → 尺寸和像素都对一遍。"""
    out = os.path.join(os.path.dirname(path),
                       "%s.pdf" % os.path.splitext(os.path.basename(path))[0])
    core.convert([path], out, page="image", margin=0.0, dpi=72.0)
    n, rect, pix = render(out)
    src = rgb_of(path)
    check("%s：MuPDF 打开且 1 页" % name, n == 1, n)
    check("%s：MediaBox 就是像素尺寸 %dx%d" % (name, src.width, src.height),
          abs(rect.width - src.width) < 1.0 and
          abs(rect.height - src.height) < 1.0,
          "%sx%s" % (rect.width, rect.height))
    check("%s：渲染尺寸对得上" % name,
          (pix.width, pix.height) == (src.width, src.height),
          "%dx%d" % (pix.width, pix.height))
    mean, peak = stats(pix.samples, expect)
    if mean is None:
        check("%s：像素长度一致" % name, False,
              "%d vs %d" % (len(pix.samples), len(expect)))
    else:
        check("%s：像素和原图一致（均差 %.3f 峰差 %d，容差 %d）"
              % (name, mean, peak, tol), mean <= tol,
              "mean=%.3f peak=%d" % (mean, peak))
    return out


def build(dir_):
    """用 Pillow 现场编码真图，返回 [(标题, 路径, 预期像素, 容差)]。"""
    cases = []
    w = h = 40
    n = w * h

    def ramp(i):
        return ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256)

    def put(mode, data):
        img = Image.new(mode, (w, h))
        img.putdata(data)
        return img

    # 1) RGB8 PNG
    img = put("RGB", [ramp(i) for i in range(n)])
    p = os.path.join(dir_, "rgb.png")
    img.save(p)
    cases.append(("RGB8 PNG", p, rgb_of(p).tobytes(), 1))

    # 2) 灰度 PNG
    img = put("L", [(i * 11) % 256 for i in range(n)])
    p = os.path.join(dir_, "gray.png")
    img.save(p)
    cases.append(("灰度 PNG", p, rgb_of(p).tobytes(), 1))

    # 3) RGBA（真半透明 → 拆 /SMask）
    img = put("RGBA", [ramp(i) + ((i * 17) % 256,) for i in range(n)])
    p = os.path.join(dir_, "rgba.png")
    img.save(p)
    cases.append(("RGBA→白底混色", p,
                  over_white(Image.open(p)), 2))

    # 4) LA 灰度 + alpha
    img = put("LA", [((i * 5) % 256, (i * 23) % 256) for i in range(n)])
    p = os.path.join(dir_, "la.png")
    img.save(p)
    cases.append(("LA→白底混色", p, over_white(Image.open(p)), 2))

    # 5) 16 位灰度 PNG：我们取高字节，PIL 也按高位优先读
    vals = [(i * 65535) // n for i in range(n)]
    p = os.path.join(dir_, "16bit.png")
    put("I;16", vals).save(p)
    want = bytearray()
    for v in vals:
        want += bytes([v >> 8]) * 3
    cases.append(("16 位降 8 位（取高字节）", p, bytes(want), 1))

    # 6) 调色板 + tRNS（index 0 透明）
    pal = Image.new("P", (w, h))
    pal.putdata([i % 4 for i in range(n)])
    pal.putpalette([0, 0, 0, 255, 0, 0, 0, 255, 0, 0, 0, 255,
                    0, 128, 255, 128, 0, 255, 255, 128, 0, 9, 9, 9])
    p = os.path.join(dir_, "pal.png")
    pal.save(p, transparency=0)
    cases.append(("调色板 + tRNS", p,
                  over_white(Image.open(p)), 2))

    # 7) 1/2/4 位灰度：原样透传给 DeviceGray，bpc 跟着走
    #    ⚠️ 这里差点又空跑一次：Pillow 的 `save(bits=2)` 对 L 模式**根本不写
    #    低位深**（IHDR 仍是 8 bit），第三节的低比特用例曾因此白测。
    #    所以 1 bit 用 Pillow 的 "1" 模式（它真会写 depth=1），2/4 bit 用
    #    test_core 里那个手写编码器，并且每张都回头查 IHDR 确认位深真的是那个数。
    one_bit = Image.new("1", (w, h))
    one_bit.putdata([1 if (i * 3) % 2 else 0 for i in range(n)])
    p = os.path.join(dir_, "low1.png")
    one_bit.save(p)
    assert_ihdr(p, 1, 0)
    cases.append(("低位深 1 bit 透传", p, rgb_of(p).tobytes(), 2))

    for depth in (2, 4):
        p = os.path.join(dir_, "low%d.png" % depth)
        step = 256 // (1 << depth)
        rows = []
        for y in range(h):
            vals = [((y * w + x) * 3) % (1 << depth) for x in range(w)]
            buf = bytearray()
            per = 8 // depth
            for k in range(0, w, per):
                byte = 0
                for j, v in enumerate(vals[k:k + per]):
                    byte |= (v & ((1 << depth) - 1)) << (8 - depth * (j + 1))
                buf.append(byte)
            rows.append(bytes(buf))
        with open(p, "wb") as fh:
            fh.write(png_bytes(w, h, depth, 0, rows))
        assert_ihdr(p, depth, 0)
        # 2/4 位这种"一个字节塞好几个像素"的图没有现成解码器可比，
        # 预期按 PNG 规范手算：索引 * (255 / (2**depth - 1))
        maxv = (1 << depth) - 1
        want = bytearray()
        for y in range(h):
            for x in range(w):
                g = (((y * w + x) * 3) % (maxv + 1)) * 255 // maxv
                want += bytes([g]) * 3
        cases.append(("低位深 %d bit 透传" % depth, p, bytes(want), 2))

    # 8) JPEG 4:4:4（不抽样色度，两个解码器才可能逐字节对上）
    img = put("RGB", [((i // w) * 8, (i % w) * 6, 90) for i in range(n)])
    p = os.path.join(dir_, "yuv444.jpg")
    img.save(p, quality=95, subsampling=0)
    cases.append(("JPEG 4:4:4 原样封装", p, rgb_of(p).tobytes(), 3))

    # 9) JPEG 4:2:0 + 高频色度（真相机/截图的常见形状）：这里两边上采样
    #    滤波器不同，峰差会有几十，属解码器差异而不是我们的锅，所以只看
    #    "平均差 < 12"，另用第 12 节的 extract_image 证明码流没被动过
    img = put("RGB", [ramp(i) for i in range(n)])
    p = os.path.join(dir_, "yuv420.jpg")
    img.save(p, quality=92)
    cases.append(("JPEG 4:2:0（换解码器比上采样）", p,
                  rgb_of(p).tobytes(), 12))

    # 10) 渐进式（progressive）JPEG：手机 App 和很多截图工具会存成这种，
    #     SOF 标记是 0xC2 而不是 0xC0。我们照样只包码流，所以关键看
    #     MuPDF 认不认这个 /DCTDecode 流
    img = put("RGB", [ramp(i) for i in range(n)])
    p = os.path.join(dir_, "progressive.jpg")
    img.save(p, quality=85, progressive=True)
    cases.append(("渐进式 JPEG 原样封装", p, rgb_of(p).tobytes(), 12))

    # 11) 灰度 JPEG
    img = put("L", [(i * 11) % 256 for i in range(n)])
    p = os.path.join(dir_, "gray.jpg")
    img.save(p)
    cases.append(("灰度 JPEG", p, rgb_of(p).tobytes(), 3))
    return cases


def main():
    work = tempfile.mkdtemp(prefix="i2p_fitz_")
    try:
        print("0) 环境")
        print("     MuPDF %s（只当验收器，不进 src、不进 exe）" % fitz.version[0])

        cases = build(work)
        print("1) 逐格式：能不能打开 + 像素对不对")
        for title, path, expect, tol in cases:
            one(title, path, expect, tol)

        print("1b) 物理尺寸：图里声明的 DPI 要真的变成页面磅值")
        # 这一节专治"page_points 算错"：上面所有用例都强制 dpi=72，
        # 于是"像素 == 磅"，把换算整个删掉也不会红 —— 典型的用例没覆盖到。
        dp = os.path.join(work, "dpi300.png")
        im = Image.new("RGB", (120, 60))
        im.putdata([ramp_global(i) for i in range(120 * 60)])
        im.save(dp, dpi=(300, 300))
        got = core.probe(dp)["dpi"]
        check("Pillow 写的 pHYs 我们能读成 ~300 DPI", 295 < got < 305, got)
        o = os.path.join(work, "dpi300.pdf")
        core.convert([dp], o, page="image", margin=0.0)      # dpi=None → 用图里的
        _n, rect, _pix = render(o)
        check("按图里的 300 DPI：120px 宽 = 28.8pt",
              abs(rect.width - 120 * 72.0 / 300.0) < 0.2 and
              abs(rect.height - 60 * 72.0 / 300.0) < 0.2,
              "%sx%s pt" % (rect.width, rect.height))
        # 反向：强制 150 DPI 时页面必须翻倍大，像素还是那么多
        o2 = os.path.join(work, "dpi150.pdf")
        core.convert([dp], o2, page="image", margin=0.0, dpi=150.0)
        _n2, rect2, _pix2 = render(o2)
        check("强制 150 DPI：同一张图页面变成 57.6x28.8pt",
              abs(rect2.width - 57.6) < 0.2 and abs(rect2.height - 28.8) < 0.2,
              "%sx%s" % (rect2.width, rect2.height))
        # 按声明密度放大渲染：300/72 倍 == 每个像素 1px，像素还得和原图对得上
        doc = fitz.open(o)
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(300.0 / 72.0, 300.0 / 72.0),
                                alpha=False)
        mean, peak = stats(pix.samples, rgb_of(dp).tobytes())
        # 别写 `mean or -1`：完全一致时 mean 正好是 0.0，falsy，会被显示成 "-1"，
        # 看着像出错其实是满分通过（同一类坑在"文件大小当空白判据"上栽过）
        shown = "-" if mean is None else "%.3f/%d" % (mean, peak)
        check("300 DPI 页按原密度渲染回 120x60 且像素一致（均差/峰差 %s）" % shown,
              (pix.width, pix.height) == (120, 60) and mean is not None and
              mean <= 1.0,
              "%dx%d mean=%s" % (pix.width, pix.height, mean))
        doc.close()

        print("2) JPEG 码流原样封装（extract_image 取回来和源文件全等）")
        for path in [c[1] for c in cases if c[1].endswith(".jpg")]:
            o = os.path.join(work, "xref.pdf")
            core.convert([path], o, page="image", margin=0.0, dpi=72.0)
            doc = fitz.open(o)
            xref = doc[0].get_images(full=True)[0][0]
            got = doc.extract_image(xref)
            with open(path, "rb") as fh:
                src = fh.read()
            check("%s：PDF 里的图就是源文件那串字节"
                  % os.path.basename(path),
                  got["image"] == src,
                  "PDF 内 %d 字节 vs 源 %d 字节，ext=%s"
                  % (len(got["image"]), len(src), got.get("ext")))
            # MuPDF 报的扩展名就是它对这串流的认识：jpeg = /DCTDecode，
            # 如果被我们重压过，这里会变成 "pdf2" / "flate" 之类
            check("%s：解码器是 JPEG（码流没被重压）" % os.path.basename(path),
                  got.get("ext") == "jpeg", got.get("ext"))
            doc.close()

        print("3) 真照片（系统自带壁纸）：无损 + 体积只多一层壳")
        wall = next((c for c in (r"C:\Windows\Web\Wallpaper\Windows\img0.jpg",
                                 r"C:\Windows\Web\Screen\img100.jpg")
                     if os.path.exists(c)), None)
        if wall is None:
            print("     SKIP：这台机器找不到系统壁纸")
        else:
            with open(wall, "rb") as fh:
                raw = fh.read()
            wo = os.path.join(work, "wall.pdf")
            core.convert([wall], wo, page="image", margin=0.0, dpi=72.0)
            doc = fitz.open(wo)
            xref = doc[0].get_images(full=True)[0][0]
            got = doc.extract_image(xref)
            check("真照片 %dx%d：码流一字节没动" % (got["width"], got["height"]),
                  got["image"] == raw,
                  "PDF 内 %d vs 源 %d" % (len(got["image"]), len(raw)))
            overhead = os.path.getsize(wo) - len(raw)
            check("体积只多了 PDF 外壳（%+d 字节）" % overhead,
                  0 <= overhead < 4096, overhead)
            doc.close()
            # 渲染回来和 PIL 解码比：不同 JPEG 实现，允许每通道 ±2
            _n, _r, pix = render(wo)
            with Image.open(wall) as ref:
                ref_bytes = ref.convert("RGB").tobytes()
            mean, peak = stats(pix.samples, ref_bytes, stride=397)
            check("真照片渲染像素和 PIL 解码一致（均差 %.3f 峰差 %d）"
                  % (mean, peak), mean <= 1.0,
                  "mean=%.3f peak=%d" % (mean, peak))

        print("4) CMYK JPEG：色相对不对（不比字节，两边都没做 ICC）")
        blocks = [("白", (0, 0, 0, 0)), ("黑", (0, 0, 0, 255)),
                  ("红", (0, 255, 255, 0)), ("蓝", (255, 255, 0, 0))]
        img = Image.new("CMYK", (8 * len(blocks), 8))
        px = []
        for _name, c in blocks:
            px.extend([c] * 8)
        img.putdata(px)
        cp = os.path.join(work, "cmyk.jpg")
        img.save(cp)
        co = os.path.join(work, "cmyk.pdf")
        core.convert([cp], co, page="image", margin=0.0, dpi=72.0)
        _n, _r, pix = render(co)
        s = pix.samples

        def blk(i):
            return tuple(s[i * 8 * 3:i * 8 * 3 + 3])

        check("CMYK 白块渲染成亮的", all(v > 200 for v in blk(0)), blk(0))
        check("CMYK 黑块渲染成暗的", all(v < 110 for v in blk(1)), blk(1))
        check("CMYK 红块 R 压倒 G/B",
              blk(2)[0] > blk(2)[1] + 80 and blk(2)[0] > blk(2)[2] + 80, blk(2))
        check("CMYK 蓝块 B 压倒 R/G",
              blk(3)[2] > blk(3)[0] + 40 and blk(3)[2] > blk(3)[1] + 40, blk(3))

        # 断言有没有牙：把 /Decode 抹掉，同一批色块必须翻成暗的
        with open(co, "rb") as fh:
            blob = fh.read()
        stripped = blob.replace(b"/Decode [1 0 1 0 1 0 1 0]", b"")
        check("反证：/Decode 真被写进了 PDF", stripped != blob)
        no = os.path.join(work, "cmyk_nodecode.pdf")
        with open(no, "wb") as fh:
            fh.write(stripped)
        _n2, _r2, pix2 = render(no)
        t = pix2.samples
        check("反证：没有 /Decode 时白块会变成暗的（说明断言抓得住）",
              max(t[0:3]) < 110, tuple(t[0:3]))

        print("5) 多页 + 纸张 + 元数据")
        paths = [c[1] for c in cases[:3]]
        out = os.path.join(work, "all.pdf")
        core.convert(paths, out, page="A4", orient="portrait", margin=28.0,
                     title="发票归档")
        doc = fitz.open(out)
        check("A4 三页", doc.page_count == 3, doc.page_count)
        check("A4 尺寸 595.28x841.89",
              all(abs(p.rect.width - 595.28) < 0.5 and
                  abs(p.rect.height - 841.89) < 0.5 for p in doc),
              [(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc])
        check("文档标题写进去了", doc.metadata.get("title") == "发票归档",
              doc.metadata)
        check("每页都渲染得出内容（纸白之外还有画上去的像素）",
              all(min(doc[i].get_pixmap(alpha=False).samples) < 250
                  for i in range(3)),
              [min(doc[i].get_pixmap(alpha=False).samples) for i in range(3)])
        doc.close()

        print("6) 横版 A4 装大图（缩放矩阵 + 各向异性重采样）")
        big = os.path.join(work, "big.png")
        wide = Image.new("RGB", (1200, 400))
        wide.putdata([ramp_global(i) for i in range(1200 * 400)])
        wide.save(big)
        out = os.path.join(work, "land.pdf")
        core.convert([big], out, page="A4", orient="landscape", margin=20.0)
        doc = fitz.open(out)
        page = doc[0]
        check("横版 A4 纸张", abs(page.rect.width - 841.89) < 0.5 and
              abs(page.rect.height - 595.28) < 0.5,
              "%sx%s" % (page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        content = pix.samples
        stride = pix.width * 3
        top = content[:stride]
        mid = content[(pix.height // 2) * stride:(pix.height // 2 + 1) * stride]
        # 1200x400 的横图塞进横版 A4 是"宽度受限"：等比缩到约 802x267，
        # 居中后**上下各留一条纯白纸边**。注意 MuPDF 把空白画纸渲成 255（白），
        # 判"有没有留白"要比 255，别按 0 判 —— 按 0 判的话整页都是 255 也能过
        check("横版：顶部是纯白纸边", set(top) == {255},
              "顶部唯一值=%s" % sorted(set(top))[:4])
        check("横版：中间那一行有色带且不满页",
              min(mid) < 250 and set(mid) != {255},
              "中间行 min=%d max=%d" % (min(mid), max(mid)))
        check("横版：底部也留白（图是居中不是贴底）",
              set(content[-stride:]) == {255},
              "底部唯一值=%s" % sorted(set(content[-stride:]))[:4])
        doc.close()

        print("7) 旋转方向（EXIF 6 = 显示时要顺时针转 90°）")
        rot = os.path.join(work, "rot.jpg")
        src = Image.new("RGB", (60, 30))
        src.putdata([(255, 0, 0) if x < 30 else (0, 0, 0)
                     for _y in range(30) for x in range(60)])
        src.save(rot, exif=exif_90())
        out = os.path.join(work, "rot.pdf")
        core.convert([rot], out, page="image", margin=0.0, dpi=72.0)
        doc = fitz.open(out)
        p = doc[0]
        check("按方向把页面转成竖的（30x60）",
              abs(p.rect.width - 30) < 1.0 and abs(p.rect.height - 60) < 1.0,
              "%sx%s" % (p.rect.width, p.rect.height))
        pix = p.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
        t = pix.samples
        wpx = pix.width

        def row(i):
            o = i * wpx * 3
            return tuple(t[o:o + 3])

        # 原图左半红、右半黑；顺时针 90° 之后"左列"变"顶行"，
        # 所以顶上是红的、底下是黑的 —— 这条同时在核对我们那张 ORIENT_MATRIX
        check("旋转后顶行是红的", row(0)[0] > 180 and row(0)[1] < 60, row(0))
        check("旋转后底行是黑的", max(row(pix.height - 1)) < 60,
              row(pix.height - 1))
        check("旋转后中间仍是竖着分两半（左红右黑变成上红下黑）",
              row(pix.height // 4)[0] > 180 and
              row(pix.height * 3 // 4)[0] < 60,
              (row(pix.height // 4), row(pix.height * 3 // 4)))
        doc.close()

        print("8) 坏文件跳过之后剩下的页仍能被读")
        junk = os.path.join(work, "junk.jpg")
        with open(junk, "wb") as fh:
            fh.write("不是图".encode("utf-8"))
        out = os.path.join(work, "mixed.pdf")
        res = core.convert([paths[0], junk, paths[1]], out,
                           margin=0.0, dpi=72.0)
        check("跳过了 1 张", len(res["failed"]) == 1, res["failed"])
        doc = fitz.open(out)
        check("产物仍可读、2 页", doc.page_count == 2, doc.page_count)
        doc.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("\n通过 %d 项" % ok)
    if bad:
        print("失败 %d 项：%s" % (len(bad), ", ".join(bad)))
        return 1
    print("真阅读器验收全过：我们写的 PDF 能被 MuPDF 按规范解出并渲染成对的像素")
    return 0


def ramp_global(i):
    return ((i * 7) % 256, (i * 13) % 256, (i * 29) % 256)


def exif_90():
    """带 Orientation=6（顺时针 90°）的 EXIF 头，Pillow 存 JPEG 时挂上去。"""
    from PIL import Image as _I
    ex = _I.Exif()
    ex[0x0112] = 6
    return ex


if __name__ == "__main__":
    sys.exit(main())
