# -*- coding: utf-8 -*-
"""
test_cli.py —— 命令行那条路（不开窗口）

跑法：
    python tests/test_cli.py
或
    python -m unittest discover -s tests

界面和 CLI 共用同一个 core，所以这里只测 CLI 自己那部分职责：
参数怎么拆、目录怎么摊开、退出码给得对不对。
"""

import io
import os
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

import main  # noqa: E402
import core  # noqa: E402
from test_core import png_bytes, jpeg_bytes  # noqa: E402


class TestParseArgs(unittest.TestCase):

    def test_plain_paths(self):
        paths, opts = main.parse_args(["a.jpg", "b.png"])
        self.assertEqual(paths, ["a.jpg", "b.png"])
        self.assertEqual(opts, {})

    def test_all_flags(self):
        paths, opts = main.parse_args(
            ["a.jpg", "-o", "out.pdf", "--paper", "A4", "--orient",
             "landscape", "--margin", "12", "--dpi", "300", "--title", "发票"])
        self.assertEqual(paths, ["a.jpg"])
        self.assertEqual(opts["out_path"], "out.pdf")
        self.assertEqual(opts["page"], "A4")
        self.assertEqual(opts["orient"], "landscape")
        self.assertEqual(opts["margin"], 12.0)
        self.assertEqual(opts["dpi"], 300.0)
        self.assertEqual(opts["title"], "发票")

    def test_long_out_flag(self):
        _p, opts = main.parse_args(["a.jpg", "--out", "x.pdf"])
        self.assertEqual(opts["out_path"], "x.pdf")

    def test_help_returns_none(self):
        for flag in ("-h", "--help"):
            self.assertEqual(main.parse_args([flag]), (None, None))

    def test_version_flag_is_a_separate_sentinel(self):
        """
        版本和帮助都"不转换就走"，但不能混成同一个返回值——
        否则 `--version` 会打印一遍用法，用户看不到版本号。
        """
        for flag in ("-v", "--version"):
            self.assertEqual(main.parse_args([flag]), (None, main.SHOW_VERSION))

    def test_bad_flag_is_still_refused_after_the_version_change(self):
        """反向确认：加 -v 没把 -x 之类的未知参数一起放过。"""
        for flag in ("-x", "--vers", "--verion"):
            with self.assertRaises(ValueError) as ctx:
                main.parse_args(["a.jpg", flag])
            self.assertIn("不认识的参数", str(ctx.exception))
            self.assertIn(flag, str(ctx.exception))

    def test_unknown_flag_says_which(self):
        with self.assertRaises(ValueError) as ctx:
            main.parse_args(["a.jpg", "--wat"])
        self.assertIn("不认识的参数", str(ctx.exception))
        self.assertIn("--wat", str(ctx.exception))

    def test_bad_paper_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            main.parse_args(["a.jpg", "--paper", "B5"])
        self.assertIn("A4", str(ctx.exception))

    def test_bad_orient_is_refused(self):
        with self.assertRaises(ValueError):
            main.parse_args(["a.jpg", "--orient", "sideways"])

    def test_margin_must_be_a_number(self):
        with self.assertRaises(ValueError) as ctx:
            main.parse_args(["a.jpg", "--margin", "两指宽"])
        self.assertIn("margin", str(ctx.exception))

    def test_dpi_must_be_a_number(self):
        with self.assertRaises(ValueError) as ctx:
            main.parse_args(["a.jpg", "--dpi", "auto"])
        self.assertIn("dpi", str(ctx.exception))

    def test_dangling_flag_without_value(self):
        with self.assertRaises(ValueError) as ctx:
            main.parse_args(["a.jpg", "-o"])
        self.assertIn("缺值", str(ctx.exception))

    def test_title_with_spaces_stays_one_value(self):
        _p, opts = main.parse_args(["a.jpg", "--title", "一 二 三"])
        self.assertEqual(opts["title"], "一 二 三")


class TestVersionOutput(unittest.TestCase):
    """
    --version 打出来的内容 + 没有控制台时不炸。

    exe 是 --windowed 打的，那种模式下 sys.stdout 是 None：
    print() 只是不输出，但 sys.stderr.write 会直接 AttributeError，
    所以这里既查"说了什么"也查"把 stdout 掐了还能正常返回"。
    """

    def setUp(self):
        self.buf = io.StringIO()
        self._old_out, self._old_err = sys.stdout, sys.stderr

    def tearDown(self):
        sys.stdout, sys.stderr = self._old_out, self._old_err

    def _run(self, flag):
        sys.stdout = sys.stderr = self.buf
        rc = main.cli([flag])
        return rc, self.buf.getvalue()

    def test_version_prints_the_single_source_number(self):
        rc, text = self._run("--version")
        self.assertEqual(rc, 0)
        self.assertIn("v%s" % core.VERSION, text)
        self.assertIn(core.TOOL_CN, text)
        self.assertIn(core.SUMMARY, text)
        # 仓库地址不带协议头（和界面同一套显示规则），但版本号只有一处来源
        self.assertIn("github.com/yunche-workshop/office-toolkit", text)
        self.assertNotIn("https://", text)

    def test_help_does_not_print_the_version(self):
        _rc, text = self._run("--help")
        self.assertIn("用法：", text)
        self.assertNotIn("v%s —— " % core.VERSION, text)

    def test_no_console_does_not_crash(self):
        """
        把 stdout/stderr 都掐成 None —— --windowed 打出来的 exe 就是这样。
        正常路径（--version / --help）和报错路径（未知参数走 main() 的
        stderr 分支）都必须返回退出码，而不是 AttributeError。
        """
        sys.stdout = sys.stderr = None
        old_argv = sys.argv
        try:
            self.assertEqual(main.cli(["--version"]), 0)
            self.assertEqual(main.cli(["--help"]), 0)
            sys.argv = ["Img2Pdf", "--wat"]
            self.assertEqual(main.main(), 2)     # main() 兜住并写 stderr
        finally:
            sys.argv = old_argv
            sys.stdout, sys.stderr = self._old_out, self._old_err


class TestExpand(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i2p_cli_")
        rows = [bytes(range(60))] * 10
        self.png = os.path.join(self.dir, "01.png")
        with open(self.png, "wb") as fh:
            fh.write(png_bytes(20, 10, 8, 2, rows))
        self.jpg = os.path.join(self.dir, "02.jpg")
        with open(self.jpg, "wb") as fh:
            fh.write(jpeg_bytes(w=300, h=200))
        self.txt = os.path.join(self.dir, "readme.txt")
        with open(self.txt, "wb") as fh:
            fh.write(b"not an image")

    def test_directory_expands_sorted_and_skips_other_types(self):
        got = main.expand([self.dir])
        self.assertEqual([os.path.basename(p) for p in got], ["01.png", "02.jpg"])

    def test_single_file_passes_through(self):
        self.assertEqual(main.expand([self.jpg]), [self.jpg])

    def test_missing_path_is_a_clear_error(self):
        with self.assertRaises(ValueError) as ctx:
            main.expand([os.path.join(self.dir, "nope.jpg")])
        self.assertIn("找不到", str(ctx.exception))

    def test_order_follows_the_user_not_the_disk(self):
        """显式给两张：顺序就是页序，别偷偷重排。"""
        got = main.expand([self.jpg, self.png])
        self.assertEqual(got, [self.jpg, self.png])


class TestCliEndToEnd(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i2p_cli_e2e_")
        rows = [bytes([i % 256 for i in range(12)]) for _ in range(4)]
        self.png = os.path.join(self.dir, "a.png")
        with open(self.png, "wb") as fh:
            fh.write(png_bytes(4, 4, 8, 2, rows))
        self.jpg = os.path.join(self.dir, "b.jpg")
        with open(self.jpg, "wb") as fh:
            fh.write(jpeg_bytes(w=160, h=90))

    def _out(self, name):
        return os.path.join(self.dir, name)

    def test_two_images_one_pdf(self):
        out = self._out("all.pdf")
        rc = main.cli([self.png, self.jpg, "-o", out])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(out))
        with open(out, "rb") as fh:
            blob = fh.read()
        self.assertEqual(blob[:5], b"%PDF-")
        self.assertIn(b"/Type /Pages /Count 2", blob)
        self.assertIn(b"/DCTDecode", blob, "JPEG 那页该是原样封装")
        self.assertIn(b"/FlateDecode", blob, "PNG 那页该是重新压的像素流")

    def test_default_name_lands_next_to_first_image(self):
        rc = main.cli([self.png, self.jpg])
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(
            os.path.join(self.dir, "a等2张.pdf")))

    def test_directory_input(self):
        out = self._out("dir.pdf")
        self.assertEqual(main.cli([self.dir, "-o", out]), 0)
        with open(out, "rb") as fh:
            self.assertIn(b"/Count 2", fh.read())

    def test_jpeg_blob_is_untouched_in_output(self):
        """CLI 的"无损"承诺：原码流按字节出现在 PDF 里。"""
        out = self._out("lossless.pdf")
        main.cli([self.jpg, "-o", out])
        with open(self.jpg, "rb") as fh:
            raw = fh.read()
        with open(out, "rb") as fh:
            blob = fh.read()
        self.assertGreater(blob.find(raw), 0)

    def test_unreadable_only_exits_nonzero(self):
        p = os.path.join(self.dir, "bad.jpg")
        with open(p, "wb") as fh:
            fh.write(b"definitely not a jpeg")
        with self.assertRaises(core.ImageError):
            core.convert([p], self._out("nope.pdf"))

    def test_partial_skip_is_reported_not_fatal(self):
        bad = os.path.join(self.dir, "c.jpg")
        with open(bad, "wb") as fh:
            fh.write(b"junk")
        out = self._out("mixed.pdf")
        rc = main.cli([self.png, bad, "-o", out])
        self.assertEqual(rc, 0)
        with open(out, "rb") as fh:
            self.assertIn(b"/Count 1", fh.read())

    def test_a4_options_reach_the_kernel(self):
        out = self._out("a4.pdf")
        main.cli([self.png, "-o", out, "--paper", "A4", "--margin", "20",
                  "--orient", "landscape"])
        with open(out, "rb") as fh:
            blob = fh.read()
        self.assertIn(b"/MediaBox [0 0 841.89 595.28]", blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
